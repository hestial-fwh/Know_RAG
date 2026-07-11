import base64
import hashlib
from pathlib import Path

from app.services.storage import minio_uri, object_key_for_media, save_bytes


def _safe_media_filename(filename_hint: str, digest: str, default_ext: str) -> str:
    suffix = Path(filename_hint).suffix.lower() or default_ext
    stem = Path(filename_hint).stem or "image"
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in stem)[:40] or "image"
    return f"{safe_stem}_{digest[:16]}{suffix}"


def save_media_bytes(
    *,
    knowledge_base_id: str,
    document_id: str,
    filename_hint: str,
    data: bytes,
    default_ext: str = ".png",
) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    filename = _safe_media_filename(filename_hint, digest, default_ext)
    object_key = object_key_for_media(knowledge_base_id, document_id, filename)
    stored = save_bytes(data, object_key)
    return {
        "backend": "minio",
        "bucket": stored.bucket,
        "object_key": stored.object_key,
        "uri": stored.uri,
        # path 保留给既有 ParsedBlock.image_path 使用，但不再是本地路径。
        "path": stored.uri,
        "relative_path": stored.object_key,
        "url": f"/files/{stored.object_key}",
        "filename": filename,
        "file_name": filename,
        "sha256": digest,
        "size": len(data),
        "base64": base64.b64encode(data).decode("utf-8"),
    }
