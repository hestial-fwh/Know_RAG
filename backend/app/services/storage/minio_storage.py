import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from tempfile import SpooledTemporaryFile
from urllib.parse import quote, unquote, urlparse
import shutil
from fastapi import UploadFile

from app.core.config import settings
from app.utils.files import ensure_dir


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    object_key: str
    uri: str
    size: int
    sha256: str
    content_type: str = "application/octet-stream"


def _minio_client():
    try:
        from minio import Minio
    except ImportError as exc:  # pragma: no cover - depends on deployment deps
        raise RuntimeError("MinIO 存储已启用，但缺少 Python 依赖 minio，请先安装 requirements.txt") from exc

    endpoint = (settings.minio_endpoint or "").replace("http://", "").replace("https://", "").strip("/")
    if not endpoint:
        raise RuntimeError("MINIO_ENDPOINT 未配置")
    return Minio(
        endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=bool(settings.minio_secure),
    )


def _bucket() -> str:
    bucket = (settings.minio_bucket or "know-rag").strip()
    if not bucket:
        raise RuntimeError("MINIO_BUCKET 未配置")
    return bucket


def ensure_bucket() -> None:
    client = _minio_client()
    bucket = _bucket()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _safe_segment(value: str | None, fallback: str = "file") -> str:
    raw = Path(value or fallback).name.strip() or fallback
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)[:180] or fallback


def object_key_for_source(knowledge_base_id: str, document_id: str, filename: str) -> str:
    return f"kb/{knowledge_base_id}/documents/{document_id}/source/{_safe_segment(filename)}"


def object_key_for_media(knowledge_base_id: str, document_id: str, filename: str) -> str:
    return f"kb/{knowledge_base_id}/documents/{document_id}/media/{_safe_segment(filename)}"


def minio_uri(bucket: str, object_key: str) -> str:
    return f"minio://{bucket}/{quote(object_key, safe='/')}"


def parse_minio_uri(uri_or_key: str) -> tuple[str, str]:
    raw = (uri_or_key or "").strip()
    if raw.startswith("minio://"):
        parsed = urlparse(raw)
        bucket = parsed.netloc
        object_key = unquote(parsed.path.lstrip("/"))
        if not bucket or not object_key:
            raise ValueError("invalid minio uri")
        return bucket, object_key
    return _bucket(), raw.lstrip("/")


def is_minio_uri(value: str | None) -> bool:
    return bool(value and value.strip().startswith("minio://"))


def validate_object_key_for_http(object_key: str) -> str:
    key = (object_key or "").strip().lstrip("/")
    parts = [p for p in key.split("/") if p]
    if not key or any(part in {".", ".."} for part in parts):
        raise ValueError("invalid object key")
    return key


def _content_type(filename: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or fallback


def save_upload_file(upload: UploadFile, object_key: str) -> StoredObject:
    ensure_bucket()
    max_size = settings.max_file_size_mb * 1024 * 1024
    hasher = hashlib.sha256()
    size = 0
    content_type = upload.content_type or _content_type(upload.filename or object_key)
    # 上传文件默认上限不大，使用 SpooledTemporaryFile 能同时完成 hash / size / put_object。
    with SpooledTemporaryFile(max_size=min(max_size, 64 * 1024 * 1024), mode="w+b") as tmp:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_size:
                raise ValueError(f"文件超过大小限制：{settings.max_file_size_mb}MB")
            hasher.update(chunk)
            tmp.write(chunk)
        tmp.seek(0)
        client = _minio_client()
        bucket = _bucket()
        client.put_object(bucket, object_key, tmp, length=size, content_type=content_type)
    digest = hasher.hexdigest()
    return StoredObject(bucket=bucket, object_key=object_key, uri=minio_uri(bucket, object_key), size=size, sha256=digest, content_type=content_type)


def save_bytes(data: bytes, object_key: str, *, content_type: str | None = None) -> StoredObject:
    from io import BytesIO

    ensure_bucket()
    payload = data or b""
    digest = hashlib.sha256(payload).hexdigest()
    bucket = _bucket()
    client = _minio_client()
    ctype = content_type or _content_type(object_key)
    client.put_object(bucket, object_key, BytesIO(payload), length=len(payload), content_type=ctype)
    return StoredObject(bucket=bucket, object_key=object_key, uri=minio_uri(bucket, object_key), size=len(payload), sha256=digest, content_type=ctype)


def get_file_bytes(uri_or_key: str) -> bytes:
    bucket, object_key = parse_minio_uri(uri_or_key)
    object_key = validate_object_key_for_http(object_key)
    client = _minio_client()
    response = client.get_object(bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def stat_object(uri_or_key: str):
    bucket, object_key = parse_minio_uri(uri_or_key)
    object_key = validate_object_key_for_http(object_key)
    client = _minio_client()
    return client.stat_object(bucket, object_key)


def iter_object_bytes(uri_or_key: str, chunk_size: int = 1024 * 1024):
    bucket, object_key = parse_minio_uri(uri_or_key)
    object_key = validate_object_key_for_http(object_key)
    client = _minio_client()
    response = client.get_object(bucket, object_key)
    try:
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        response.close()
        response.release_conn()


def ensure_local_document_file(document) -> Path:
    """Return a local file path for parsers while MinIO remains the source of truth."""
    storage_path = str(getattr(document, "storage_path", "") or "")
    if not is_minio_uri(storage_path):
        return Path(storage_path)

    bucket, object_key = parse_minio_uri(storage_path)
    suffix = Path(getattr(document, "file_name", "") or object_key).suffix
    safe_name = _safe_segment(getattr(document, "file_name", "") or f"source{suffix or ''}")
    cache_path = Path(settings.local_cache_dir) / getattr(document, "knowledge_base_id", "unknown") / getattr(document, "id", "unknown") / "source" / safe_name
    if cache_path.exists() and cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path
    ensure_dir(cache_path.parent)
    client = _minio_client()
    client.fget_object(bucket, object_key, str(cache_path))
    return cache_path


def delete_prefix(prefix: str) -> int:
    prefix = validate_object_key_for_http(prefix).rstrip("/") + "/"
    client = _minio_client()
    bucket = _bucket()
    deleted = 0
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        client.remove_object(bucket, obj.object_name)
        deleted += 1
    return deleted


def delete_document_objects(knowledge_base_id: str, document_id: str) -> int:
    return delete_prefix(f"kb/{knowledge_base_id}/documents/{document_id}")


def delete_knowledge_base_objects(knowledge_base_id: str) -> int:
    return delete_prefix(f"kb/{knowledge_base_id}")

def document_cache_dir(knowledge_base_id: str, document_id: str) -> Path:
    return Path(settings.local_cache_dir) / knowledge_base_id / document_id


def knowledge_base_cache_dir(knowledge_base_id: str) -> Path:
    return Path(settings.local_cache_dir) / knowledge_base_id


def delete_document_cache(knowledge_base_id: str, document_id: str) -> None:
    shutil.rmtree(document_cache_dir(knowledge_base_id, document_id), ignore_errors=True)


def delete_knowledge_base_cache(knowledge_base_id: str) -> None:
    shutil.rmtree(knowledge_base_cache_dir(knowledge_base_id), ignore_errors=True)