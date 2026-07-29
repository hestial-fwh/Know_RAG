
"""MinerU document parser adapter.

This adapter makes MinerU a first-class **document parsing engine** in know_rag.
It calls the MinerU ``/file_parse`` endpoint for any supported file format, then
converts MinerU's ``content_list`` / markdown output into the local
ParsedDocument/ParsedBlock model used by chunking, embedding and retrieval.

Supported formats (from MinerU OpenAPI): PDF, image (PNG/JPG/JPEG/WEBP),
DOCX, PPTX, XLSX.  Additional formats can be configured via
``MINERU_SUPPORTED_EXTENSIONS``.
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ModelConfig
from app.services.document_parser.media_storage import save_media_bytes
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.pipeline_debug_artifacts import save_debug_mineru_raw
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres

logger = logging.getLogger(__name__)


@dataclass
class MinerUPdfConfig:
    base_url: str
    api_key: str = ""
    model_name: str = "mineru"
    provider: str = "mineru"
    source: str = "unknown"
    runtime_profile: str = "cpu"
    backend: str = "pipeline"
    parse_method: str = "auto"


def _clean(text: Any) -> str:
    return sanitize_text_for_postgres(str(text or "").strip())


def _is_mineru_provider(value: str | None) -> bool:
    return (value or "").strip().lower() == "mineru"


def _normalize_mineru_profile(value: str | None = None) -> str:
    profile = (value or getattr(settings, "mineru_runtime_profile", "cpu") or "cpu").strip().lower()
    if profile in {"gpu", "cuda", "vlm"}:
        return "gpu"
    return "cpu"


def _profile_base_url(profile: str) -> str:
    profile = _normalize_mineru_profile(profile)
    if profile == "gpu":
        return (getattr(settings, "mineru_gpu_base_url", "") or getattr(settings, "mineru_base_url", "") or "").strip().rstrip("/")
    return (getattr(settings, "mineru_cpu_base_url", "") or getattr(settings, "mineru_base_url", "") or "").strip().rstrip("/")


def _profile_backend(profile: str) -> str:
    profile = _normalize_mineru_profile(profile)
    if profile == "gpu":
        return (getattr(settings, "mineru_gpu_backend", "") or getattr(settings, "mineru_backend", "") or "vlm-auto-engine").strip()
    return (getattr(settings, "mineru_cpu_backend", "") or "pipeline").strip()


def _profile_parse_method(profile: str) -> str:
    profile = _normalize_mineru_profile(profile)
    if profile == "gpu":
        return (getattr(settings, "mineru_gpu_parse_method", "") or getattr(settings, "mineru_parse_method", "") or "auto").strip()
    return (getattr(settings, "mineru_cpu_parse_method", "") or getattr(settings, "mineru_parse_method", "") or "auto").strip()


def _config_from_row(row: ModelConfig, *, profile: str | None = None) -> MinerUPdfConfig | None:
    if not row or not _is_mineru_provider(row.provider) or not (row.base_url or "").strip():
        return None
    runtime_profile = _normalize_mineru_profile(profile)
    return MinerUPdfConfig(
        base_url=(row.base_url or "").strip().rstrip("/"),
        api_key=row.api_key or "",
        model_name=row.model_name or "mineru",
        provider=row.provider or "mineru",
        source=f"model_config:{row.model_type}",
        runtime_profile=runtime_profile,
        backend=_profile_backend(runtime_profile),
        parse_method=_profile_parse_method(runtime_profile),
    )


def get_mineru_pdf_config(db: Session | None = None, *, profile: str | None = None) -> MinerUPdfConfig | None:
    """Resolve MinerU config for document parsing.

    Runtime profile is explicit and deterministic:
    - cpu → MINERU_CPU_BASE_URL + MINERU_CPU_BACKEND=pipeline
    - gpu → MINERU_GPU_BASE_URL + MINERU_GPU_BACKEND=vlm-auto-engine/hybrid-auto-engine

    Environment profile URLs take precedence over DB rows because they are tied
    to docker-compose service names.  DB rows remain a fallback for older setups.
    """
    runtime_profile = _normalize_mineru_profile(profile)
    env_url = _profile_base_url(runtime_profile)
    if env_url:
        return MinerUPdfConfig(
            base_url=env_url,
            api_key=getattr(settings, "mineru_api_key", "") or "",
            source=f"env:MINERU_{runtime_profile.upper()}_BASE_URL",
            runtime_profile=runtime_profile,
            backend=_profile_backend(runtime_profile),
            parse_method=_profile_parse_method(runtime_profile),
        )

    base_url = getattr(settings, "mineru_base_url", "") or ""
    if base_url.strip():
        return MinerUPdfConfig(
            base_url=base_url.strip().rstrip("/"),
            api_key=getattr(settings, "mineru_api_key", "") or "",
            source="env:MINERU_BASE_URL",
            runtime_profile=runtime_profile,
            backend=_profile_backend(runtime_profile),
            parse_method=_profile_parse_method(runtime_profile),
        )

    if db is not None:
        preferred_types = ["parser", "document_parser", "pdf_parser", "ocr"]
        for model_type in preferred_types:
            row = (
                db.query(ModelConfig)
                .filter(ModelConfig.model_type == model_type, ModelConfig.provider.ilike("mineru"))
                .first()
            )
            cfg = _config_from_row(row, profile=runtime_profile) if row else None
            if cfg:
                return cfg
        row = db.query(ModelConfig).filter(ModelConfig.provider.ilike("mineru")).first()
        cfg = _config_from_row(row, profile=runtime_profile) if row else None
        if cfg:
            return cfg

    return None


def is_mineru_supported_format(file_type: str) -> bool:
    """Check whether MinerU can handle this file extension.

    The list is driven by ``MINERU_SUPPORTED_EXTENSIONS`` and can be
    extended/restricted via configuration.  The MinerU service itself
    advertises PDF, image, DOCX, PPTX, XLSX support in its OpenAPI.
    """
    raw = (getattr(settings, "mineru_supported_extensions", "") or "").strip()
    if not raw:
        return False
    extensions = {ext.strip().lower().lstrip(".") for ext in raw.split(",") if ext.strip()}
    return (file_type or "").lower().lstrip(".") in extensions


def _file_parse_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    endpoint = getattr(settings, "mineru_parse_endpoint", "/file_parse") or "/file_parse"
    if base.endswith(endpoint):
        return base
    return f"{base}{endpoint}"


def _mineru_error_message(status_code: int, body: str, backend: str) -> str:
    """Produce a clear error message for MinerU failures, including CUDA hints."""
    # Check for CUDA/GPU-related errors
    body_lower = body.lower()
    cuda_keywords = ["cuda is not available", "cuda not available", "no cuda",
                     "cuda error", "cudaerror", "torch.cuda", "cuda device",
                     "gpu is not available", "no gpu", "out of memory",
                     "lmdeploy", "vllm"]
    if any(kw in body_lower for kw in cuda_keywords):
        gpu_backends = {"hybrid-auto-engine", "vlm-auto-engine", "hybrid", "vlm"}
        if backend in gpu_backends:
            return (
                "MinerU backend '{backend}' triggered CUDA/VLM engine but CUDA is not available. "
                "This machine has no GPU. Switch MINERU_BACKEND=pipeline for CPU-only parsing, "
                "or use a remote VLM backend (vlm-http-client / hybrid-http-client). "
                "Original error: {body}".format(backend=backend, body=body[:300])
            )
        return (
            "MinerU reported CUDA/GPU error but backend is '{backend}'. "
            "Ensure MINERU_BACKEND=pipeline for CPU-only mode. "
            "Original error: {body}".format(backend=backend, body=body[:300])
        )
    return "MinerU parse failed: status={status}, body={body}".format(
        status=status_code, body=body[:500])


def _extract_mineru_error(payload: dict, backend: str) -> str:
    """Extract error messages from a MinerU 200-OK response payload.

    MinerU may return HTTP 200 but with an error field inside the JSON body.
    This can happen when the backend fails after the request is accepted.
    """
    error = payload.get("error")
    if not error:
        # Check nested results for errors
        for container_key in ("results", "result"):
            container = payload.get(container_key)
            if isinstance(container, dict):
                for file_result in container.values():
                    if isinstance(file_result, dict):
                        error = file_result.get("error")
                        if error:
                            break
                if error:
                    break

    if not error:
        return ""

    if isinstance(error, str):
        error_str = error
    elif isinstance(error, dict):
        error_str = error.get("message") or error.get("detail") or str(error)
    else:
        error_str = str(error)

    if not error_str:
        return ""

    error_lower = error_str.lower()
    cuda_keywords = ["cuda is not available", "cuda not available", "no cuda",
                     "cuda error", "torch.cuda", "cuda device",
                     "gpu is not available", "no gpu", "out of memory"]
    if any(kw in error_lower for kw in cuda_keywords):
        return (
            "MinerU backend '{backend}' triggered CUDA/VLM engine. "
            "Current machine has no CUDA. Please switch MINERU_BACKEND=pipeline "
            "or use remote VLM backend. Original error: {error}".format(
                backend=backend, error=error_str[:300])
        )

    return "MinerU reported error: {error}".format(error=error_str[:300])


def _post_file_parse(
    path: Path,
    config: MinerUPdfConfig,
    output_dir: str | None = None,
) -> Any:
    """Call MinerU /file_parse and return the JSON response."""
    data_bytes = path.read_bytes()
    headers: dict[str, str] = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    # Build form data from settings
    lang = getattr(settings, "mineru_lang", "ch") or "ch"
    backend = config.backend or getattr(settings, "mineru_backend", "pipeline") or "pipeline"
    parse_method = config.parse_method or getattr(settings, "mineru_parse_method", "auto") or "auto"
    formula_enable = getattr(settings, "mineru_enable_formula", True)
    table_enable = getattr(settings, "mineru_enable_table", True)
    timeout = getattr(settings, "mineru_timeout_seconds", 300) or 300

    form = {
        "lang_list": lang,
        "backend": backend,
        "parse_method": parse_method,
        "formula_enable": str(formula_enable).lower(),
        "table_enable": str(table_enable).lower(),
        "return_content_list": "true",
        "return_images": "true",
        "return_md": "true",
    }

    # 关键点：
    # output_dir 不能在这里自己拼 document，因为当前函数没有 document 变量。
    # 由上层 parse_document_with_mineru() 根据 knowledge_base_id/document_id 拼好后传进来。
    if output_dir:
        form["output_dir"] = output_dir

    endpoint = _file_parse_url(config.base_url)

    logger.info(
        "MinerU request prepared: endpoint=%s, file=%s, profile=%s, backend=%s, parse_method=%s, output_dir=%s",
        endpoint,
        path.name,
        config.runtime_profile,
        backend,
        parse_method,
        output_dir or "",
    )

    def _send(field_name: str) -> httpx.Response:
        files = {field_name: (path.name, data_bytes, mime_type)}
        with httpx.Client(timeout=timeout) as client:
            return client.post(endpoint, headers=headers, files=files, data=form)

    response = _send("files")

    # Some MinerU deployments expose a single-file form field named ``file``.
    if response.status_code in {400, 404, 415, 422}:
        response = _send("file")

    if response.status_code != 200:
        body = response.text[:500]
        raise RuntimeError(_mineru_error_message(response.status_code, body, backend))

    # Even a 200 response may contain an error payload.
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("MinerU parse failed: response is not valid JSON") from exc

    # Detect MinerU errors that are returned inside a 200 wrapper.
    if isinstance(payload, dict):
        error_msg = _extract_mineru_error(payload, backend)
        if error_msg:
            raise RuntimeError(error_msg)

    return payload
def _iter_dicts(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _looks_like_content_block(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    keys = set(item.keys())
    return bool(keys & {"type", "block_type", "text", "table_body", "img_path", "image_path", "bbox", "page_idx"})


def _find_file_result(payload: Any) -> dict | None:
    """Locate the per-file result object inside MinerU v2/v3 response.

    MinerU v3.2.x wraps results like::

        {"results": {"filename": {"md_content": "...", "content_list": "[...]"}}}

    Older versions or different backends may return::

        {"content_list": [...], "markdown": "..."}
        {"data": {"results": {"filename": {...}}}}
        {"result": {"content_list": [...], ...}}

    Returns the innermost per-file dict, or None.
    """
    if not isinstance(payload, dict):
        return None

    # ── results.{filename} pattern (MinerU v3.2.x) ─────────────────────
    for container_key in ("results", "result", "output"):
        container = payload.get(container_key)
        if isinstance(container, dict):
            # If it has md_content / content_list / markdown directly, it's the file result
            if any(k in container for k in ("md_content", "content_list", "markdown", "md")):
                return container
            # Otherwise explore each file entry
            for _file_name, file_result in container.items():
                if isinstance(file_result, dict):
                    # Check if this is the file result or a wrapper
                    if any(k in file_result for k in ("md_content", "content_list", "markdown", "md")):
                        return file_result
                    # May have nested results
                    for inner_key in ("results", "result", "output"):
                        inner = file_result.get(inner_key)
                        if isinstance(inner, dict):
                            if any(k in inner for k in ("md_content", "content_list", "markdown", "md")):
                                return inner

    # ── data wrapping pattern ──────────────────────────────────────────
    data = payload.get("data")
    if isinstance(data, dict):
        found = _find_file_result(data)
        if found:
            return found

    # ── top-level pattern (backward compat) ────────────────────────────
    if any(k in payload for k in ("md_content", "content_list", "markdown", "md")):
        return payload

    return None


def _find_content_list(payload: Any) -> list[dict]:
    """Find MinerU content_list in several common response shapes.

    MinerU v3.2.x returns ``content_list`` as a **JSON-encoded string**, not a
    parsed list.  We detect strings that look like JSON arrays and parse them.
    """
    import json as _json

    file_result = _find_file_result(payload)

    # ── Search the file result first ───────────────────────────────────
    if isinstance(file_result, dict):
        for key in ("content_list", "content", "blocks", "layout", "layouts"):
            value = file_result.get(key)
            # content_list may be a JSON-encoded string (MinerU v3.2.x)
            if isinstance(value, str) and value.strip().startswith("["):
                try:
                    value = _json.loads(value)
                except Exception:
                    logger.warning("MinerU content_list is a string but not valid JSON")
                    continue
            if isinstance(value, list) and any(_looks_like_content_block(item) for item in value):
                logger.info("MinerU content_list found: key=%s, blocks=%d", key, len(value))
                return [item for item in value if isinstance(item, dict)]

    # ── Direct list at top-level or file_result ────────────────────────
    if isinstance(payload, list) and payload and all(_looks_like_content_block(item) for item in payload if isinstance(item, dict)):
        return [item for item in payload if isinstance(item, dict)]

    # ── Direct key search at top-level ─────────────────────────────────
    direct_keys = ["content_list", "content", "blocks", "layout", "layouts"]
    if isinstance(payload, dict):
        for key in direct_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip().startswith("["):
                try:
                    value = _json.loads(value)
                except Exception:
                    continue
            if isinstance(value, list) and any(_looks_like_content_block(item) for item in value):
                return [item for item in value if isinstance(item, dict)]

    # ── Recursive fallback ─────────────────────────────────────────────
    best: list[dict] = []
    for dct in _iter_dicts(payload):
        for value in dct.values():
            if isinstance(value, str) and value.strip().startswith("["):
                try:
                    value = _json.loads(value)
                except Exception:
                    continue
            if isinstance(value, list) and any(_looks_like_content_block(item) for item in value):
                candidate = [item for item in value if isinstance(item, dict)]
                if len(candidate) > len(best):
                    best = candidate
    if best:
        logger.info("MinerU content_list found via recursive search: blocks=%d", len(best))
    return best


def _find_markdown(payload: Any) -> str:
    """Find MinerU markdown content across response structures.

    MinerU v3.2.x uses the key ``md_content`` nested under
    ``results.{filename}.md_content``.
    """
    keys = ["markdown", "md", "md_content", "markdown_content", "content_md", "text"]

    file_result = _find_file_result(payload)

    # ── Search the file result first ───────────────────────────────────
    if isinstance(file_result, dict):
        for key in keys:
            value = file_result.get(key)
            if isinstance(value, str) and value.strip():
                logger.info("MinerU markdown found: key=%s, len=%d", key, len(value))
                return sanitize_text_for_postgres(value)

    # ── Top-level search ───────────────────────────────────────────────
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return sanitize_text_for_postgres(value)
        # Recurse into data/results
        for container_key in ("data", "results", "result"):
            container = payload.get(container_key)
            if isinstance(container, (dict, list)):
                found = _find_markdown(container)
                if found:
                    return found

    if isinstance(payload, list):
        for item in payload:
            found = _find_markdown(item)
            if found:
                return found
    return ""


def _find_image_map(payload: Any) -> dict[str, Any]:
    image_map: dict[str, Any] = {}
    image_keys = {"images", "image_dict", "imgs", "page_images", "image", "charts", "chart_dict", "chart_images"}
    for dct in _iter_dicts(payload):
        for key, value in dct.items():
            if key not in image_keys:
                continue
            if isinstance(value, dict):
                for name, image_payload in value.items():
                    image_map[str(name)] = image_payload
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("filename") or item.get("file_name") or item.get("path") or item.get("img_path") or str(idx)
                        image_map[str(name)] = item
                    else:
                        image_map[str(idx)] = item
    return image_map


def _dump_mineru_debug_payload(
    payload: Any,
    content_list: list[dict],
    image_map: dict[str, Any],
    *,
    knowledge_base_id: str,
    document_id: str,
    file_name: str,
    endpoint: str,
    mineru_output_dir: str | None,
    mineru_local_output_dir: Path,
) -> None:
    """Persist MinerU raw HTTP response for debugging only.

    This is intentionally a side-channel: it must not change parsing behavior.
    It writes files only when CLEANUP_PARSE_CACHE_AFTER_PARSE=false.
    """
    if getattr(settings, "cleanup_parse_cache_after_parse", True):
        return

    try:
        debug_dir = mineru_local_output_dir.parent / "mineru_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        type_counts: dict[str, int] = {}
        for item in content_list or []:
            if not isinstance(item, dict):
                continue
            raw_type = str(item.get("type") or item.get("block_type") or "").strip() or "unknown"
            type_counts[raw_type] = type_counts.get(raw_type, 0) + 1

        summary = {
            "knowledge_base_id": str(knowledge_base_id),
            "document_id": str(document_id),
            "file_name": file_name,
            "endpoint": endpoint,
            "mineru_output_dir": mineru_output_dir or "",
            "mineru_local_output_dir": str(mineru_local_output_dir),
            "content_list_count": len(content_list or []),
            "image_map_count": len(image_map or {}),
            "type_counts": type_counts,
        }

        (debug_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (debug_dir / "mineru_response.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (debug_dir / "content_list.json").write_text(
            json.dumps(content_list or [], ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (debug_dir / "image_map_keys.json").write_text(
            json.dumps(list((image_map or {}).keys()), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        logger.info(
            "MinerU debug payload dumped: dir=%s, content_list_count=%s, image_map_count=%s, type_counts=%s",
            debug_dir,
            len(content_list or []),
            len(image_map or {}),
            type_counts,
        )
    except Exception:
        logger.warning("Failed to dump MinerU debug payload", exc_info=True)


def _extract_base64(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        if raw.startswith("data:"):
            return raw.split(",", 1)[-1]
        # Avoid treating normal file paths as base64.
        if re.search(r"[/\\]", raw) or raw.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            return ""
        if len(raw) > 80:
            return raw
        return ""
    if isinstance(value, dict):
        for key in ("base64", "image_base64", "data", "bytes", "content"):
            b64 = _extract_base64(value.get(key))
            if b64:
                return b64
    return ""


def _decode_image_bytes(value: Any) -> bytes | None:
    b64 = _extract_base64(value)
    if not b64:
        return None
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


def _image_payload_for_path(img_path: str, image_map: dict[str, Any]) -> Any:
    if not img_path:
        return None
    candidates = [img_path, Path(img_path).name, Path(img_path).stem]
    for key in candidates:
        if key in image_map:
            return image_map[key]
    for key, value in image_map.items():
        if key and (key in img_path or Path(key).name == Path(img_path).name):
            return value
    return None


def _normalize_path_text(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _backend_mineru_output_dir(knowledge_base_id: str, document_id: str) -> Path:
    """Document-level MinerU output path as seen by backend container.

    Local Windows dev normally maps:
      Windows MinerU: D:/know_rag/cache/{kb_id}/{doc_id}/mineru_output
      Backend Docker: /data/cache/{kb_id}/{doc_id}/mineru_output
    """
    return (
        Path(getattr(settings, "local_cache_dir", "/data/cache") or "/data/cache")
        / str(knowledge_base_id)
        / str(document_id)
        / "mineru_output"
    )


def _backend_mineru_base_dir() -> Path:
    """Base cache dir as seen by backend container."""
    return Path(getattr(settings, "local_cache_dir", "/data/cache") or "/data/cache")


def _mineru_output_base_text() -> str:
    """Base output dir as seen by MinerU process.

    In current Windows-local MinerU mode this is a Windows path, such as:
      D:/know_rag/cache

    In Linux/Docker MinerU mode this may be:
      /data/cache
    """
    return _normalize_path_text(getattr(settings, "mineru_output_dir", "") or "")


def _translate_mineru_path_to_backend_path(path_text: str) -> Path | None:
    """Translate a MinerU-visible path to backend-visible path when possible.

    This is needed when MinerU returns img_path as a Windows path under
    MINERU_OUTPUT_DIR, while backend sees the same folder via LOCAL_CACHE_DIR.
    """
    raw = _normalize_path_text(path_text)
    if not raw:
        return None

    mineru_base = _mineru_output_base_text().rstrip("/")
    if mineru_base and raw.startswith(mineru_base):
        relative = raw[len(mineru_base):].lstrip("/")
        return _backend_mineru_base_dir() / relative

    return None


def _legacy_mineru_output_dirs(
    knowledge_base_id: str,
    document_id: str,
    requested_output_dir: str | None = None,
) -> list[Path]:
    """Candidate roots for MinerU output when MinerU ignores output_dir.

    Some Windows-local MinerU services still write to their default ./output.
    Backend can read that only if the host output folder is bind-mounted, for
    example:
      ./output:/data/mineru_output

    Optional env:
      MINERU_LEGACY_OUTPUT_DIR=/data/mineru_output
    """
    dirs: list[Path] = []

    configured = (getattr(settings, "mineru_legacy_output_dir", "") or "").strip()
    if configured:
        dirs.append(Path(configured))

    # Recommended bind mount target if MinerU's Windows ./output is mounted.
    dirs.append(Path("/data/mineru_output"))

    # If project source is bind-mounted, these may also work in local dev.
    dirs.append(Path("./output"))
    dirs.append(Path("/app/output"))

    # Requested output dir translated to backend path.
    translated = _translate_mineru_path_to_backend_path(requested_output_dir or "")
    if translated:
        dirs.append(translated)

    # Document-level cache dir.
    dirs.append(_backend_mineru_output_dir(knowledge_base_id, document_id))

    # Deduplicate while preserving order.
    unique: list[Path] = []
    seen: set[str] = set()
    for item in dirs:
        key = str(item)
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return unique


def _find_legacy_mineru_task_dirs(file_name: str, roots: list[Path]) -> list[Path]:
    """Find task directories created by MinerU default ./output mode."""
    if not file_name:
        return []
    task_dirs: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
            for found in root.rglob(file_name):
                if not found.is_file():
                    continue
                task_dir = found.parent.parent if found.parent.name == "uploads" else found.parent
                while task_dir.parent != root and task_dir.parent != task_dir:
                    task_dir = task_dir.parent
                if task_dir == root:
                    continue
                key = str(task_dir.resolve())
                if key not in seen:
                    seen.add(key)
                    task_dirs.append(task_dir)
        except Exception:
            logger.warning("Failed to scan MinerU legacy output root: %s", root, exc_info=True)
    return task_dirs


def _cleanup_legacy_mineru_outputs(file_name: str, roots: list[Path]) -> None:
    """Remove MinerU default ./output task directories after media was persisted."""
    if not getattr(settings, "cleanup_mineru_legacy_output_after_parse", True):
        return
    for task_dir in _find_legacy_mineru_task_dirs(file_name, roots):
        try:
            shutil.rmtree(task_dir, ignore_errors=True)
            logger.info("Cleaned MinerU legacy output task dir: %s", task_dir)
        except Exception:
            logger.warning("Failed to clean MinerU legacy output task dir: %s", task_dir, exc_info=True)


def _read_mineru_image_file(
    img_path: str,
    *,
    knowledge_base_id: str,
    document_id: str,
    mineru_local_output_dir: Path | None = None,
    mineru_legacy_output_dirs: list[Path] | None = None,
) -> bytes | None:
    """Read image bytes when MinerU returns only img_path.

    MinerU content_list often contains paths like:
      images/xxx.jpg
      <document>/auto/images/xxx.jpg
      D:/know_rag/cache/<kb>/<doc>/mineru_output/.../images/xxx.jpg

    This function tries:
      1. Direct backend-visible path.
      2. Translated MINERU_OUTPUT_DIR -> LOCAL_CACHE_DIR path.
      3. Paths relative to document-level mineru_output.
      4. Legacy output roots, if MinerU ignored output_dir.
      5. Filename search under candidate roots.
    """
    raw = _normalize_path_text(img_path)
    if not raw:
        return None

    candidates: list[Path] = []

    # Direct path as seen by backend.
    candidates.append(Path(raw))

    # Translate Windows MinerU path to Docker backend path.
    translated = _translate_mineru_path_to_backend_path(raw)
    if translated:
        candidates.append(translated)

    candidate_roots: list[Path] = []
    if mineru_local_output_dir:
        candidate_roots.append(mineru_local_output_dir)
    candidate_roots.extend(mineru_legacy_output_dirs or [])

    for root in candidate_roots:
        candidates.append(root / raw)
        candidates.append(root / Path(raw).name)

    # Try direct candidates first.
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate.read_bytes()
        except Exception:
            continue

    # Last fallback: recursive search by filename under known roots.
    filename = Path(raw).name
    if filename:
        for root in candidate_roots:
            try:
                if root.exists() and root.is_dir():
                    for found in root.rglob(filename):
                        if found.is_file():
                            logger.info("MinerU image found by filename search: img_path=%s, file=%s", img_path, found)
                            return found.read_bytes()
            except Exception:
                continue

    logger.warning(
        "MinerU image file not found: img_path=%s, kb=%s, doc=%s, roots=%s",
        img_path,
        knowledge_base_id,
        document_id,
        [str(p) for p in candidate_roots],
    )
    return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _page_from_block(block: dict) -> int | None:
    # Preserve page_idx in metadata, but expose page_no as one-based for UI.
    # Use explicit None checks because 0 is falsy in Python but valid here.
    pn_raw = block.get("page_no")
    if pn_raw is None:
        pn_raw = block.get("page")
    if pn_raw is None:
        pn_raw = block.get("page_num")
    page_no = _to_int(pn_raw)
    if page_no is not None and page_no > 0:
        return page_no
    pi_raw = block.get("page_idx")
    if pi_raw is None:
        pi_raw = block.get("page_index")
    page_idx = _to_int(pi_raw)
    if page_idx is not None:
        return page_idx + 1 if page_idx >= 0 else None
    return None


def _bbox_from_block(block: dict) -> tuple[float, float, float, float] | None:
    value = block.get("bbox") or block.get("box") or block.get("position") or block.get("layout_bbox")
    if not isinstance(value, (list, tuple)):
        return None
    try:
        numbers = [float(v) for v in value]
        if len(numbers) >= 4:
            if len(numbers) >= 8:
                xs = numbers[0::2]
                ys = numbers[1::2]
                return (min(xs), min(ys), max(xs), max(ys))
            return (numbers[0], numbers[1], numbers[2], numbers[3])
    except Exception:
        return None
    return None


def _text_from_block(block: dict) -> str:
    for key in ("text", "content", "md", "markdown", "latex", "equation", "html"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return _clean(value)
    lines = block.get("lines") or block.get("spans")
    if isinstance(lines, list):
        parts: list[str] = []
        for item in lines:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return _clean("\n".join(part for part in parts if part))
    return ""


def _caption_text(value: Any) -> str:
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, list):
        return _clean("\n".join(str(item) for item in value if str(item or "").strip()))
    return ""


def _block_type(raw_type: str) -> str:
    t = (raw_type or "").strip().lower()
    if t in {"table", "table_body"}:
        return "table"
    # MinerU 3.x may classify paper curves/statistical plots as `chart`
    # instead of `image`. Treat them as image-like visual blocks so Know_RAG
    # can persist them to MinIO and create image chunks.
    if t in {"image", "figure", "fig", "chart", "graph", "plot"}:
        return "image"
    return "text"


def _save_mineru_image(
    block: dict,
    image_map: dict[str, Any],
    *,
    knowledge_base_id: str,
    document_id: str,
    order_index: int,
    mineru_local_output_dir: Path | None = None,
    mineru_legacy_output_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    img_path = str(
        block.get("img_path")
        or block.get("image_path")
        or block.get("chart_img_path")
        or block.get("chart_path")
        or block.get("path")
        or block.get("url")
        or ""
    ).strip()

    payload = block.get("image_base64") or block.get("base64") or block.get("image")
    if not payload:
        payload = _image_payload_for_path(img_path, image_map)

    data = _decode_image_bytes(payload)

    # MinerU often returns only img_path instead of base64 image bytes.
    # Read the image from MinerU output/cache and then upload it to MinIO.
    if not data and img_path:
        data = _read_mineru_image_file(
            img_path,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            mineru_local_output_dir=mineru_local_output_dir,
            mineru_legacy_output_dirs=mineru_legacy_output_dirs,
        )

    if not data:
        return {}

    ext = Path(img_path).suffix or ".png"
    return save_media_bytes(
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        filename_hint=Path(img_path).name or f"mineru_image_{order_index}{ext}",
        data=data,
        default_ext=ext,
    )


def _metadata_from_block(block: dict, parser: str) -> dict[str, Any]:
    raw_type = str(block.get("type") or block.get("block_type") or "text")
    keys_to_keep = [
        "type",
        "block_type",
        "page_idx",
        "page_index",
        "page_no",
        "bbox",
        "page_width",
        "page_height",
        "source_block_id",
        "section_path",
        "role",
        "text_level",
        "level",
        "img_path",
        "image_path",
        "chart_img_path",
        "chart_path",
        "table_body",
        "table_caption",
        "image_caption",
        "chart_caption",
        "image_footnote",
        "chart_footnote",
        "table_footnote",
        "score",
        "layout_region_candidates",
        "layout_candidate_method",
    ]
    meta = {key: block.get(key) for key in keys_to_keep if key in block}
    meta.update({
        "parser": parser,
        "source": "mineru_content_list",
        "raw_block_type": raw_type,
    })
    if "text_level" not in meta and "level" in block:
        meta["text_level"] = block.get("level")
    try:
        page_width = float(block.get("page_width"))
        page_height = float(block.get("page_height"))
    except (TypeError, ValueError):
        page_width = 0.0
        page_height = 0.0
    if page_width > 0.0 and page_height > 0.0:
        meta["page_width"] = page_width
        meta["page_height"] = page_height
        meta["page_extent"] = [0.0, 0.0, page_width, page_height]
        meta["page_extent_source"] = "authoritative"
        meta["page_extent_coordinate_space"] = "mineru_page"
    else:
        meta.pop("page_width", None)
        meta.pop("page_height", None)
    return sanitize_json_for_postgres(meta)


def _markdown_table_to_html(md_table: str) -> str:
    """Convert a basic Markdown pipe table to an HTML <table>.

    MinerU pipeline backend may return tables as Markdown (pipe-separated rows
    with a ``|---|---|`` separator line).  chunk_service._block_index_content
    relies on ``_plain_text_from_html`` for table text extraction, so we convert
    Markdown tables to HTML to keep the downstream path unchanged.
    """
    lines = [line.strip() for line in (md_table or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return ""
    # A valid markdown table has at least one data row plus a separator row.
    # The separator row looks like: |---|---|
    has_separator = any(
        re.match(r"^\|?[\s]*:?---+:?[\s]*(\|[\s]*:?---+:?[\s]*)*\|?$", line)
        for line in lines
    )
    if not has_separator:
        return ""

    rows: list[list[str]] = []
    for line in lines:
        # Skip separator rows
        if re.match(r"^\|?[\s]*:?---+:?[\s]*(\|[\s]*:?---+:?[\s]*)*\|?$", line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(cells)

    if not rows:
        return ""

    # Normalize row lengths
    max_cols = max(len(row) for row in rows)
    for row in rows:
        while len(row) < max_cols:
            row.append("")

    html_parts = ["<table>"]
    if rows:
        html_parts.append("<thead><tr>")
        for cell in rows[0]:
            html_parts.append(f"<th>{cell}</th>")
        html_parts.append("</tr></thead>")
    if len(rows) > 1:
        html_parts.append("<tbody>")
        for row in rows[1:]:
            html_parts.append("<tr>")
            for cell in row:
                html_parts.append(f"<td>{cell}</td>")
            html_parts.append("</tr>")
        html_parts.append("</tbody>")
    html_parts.append("</table>")
    return "\n".join(html_parts)


def _content_list_to_blocks(
    content_list: list[dict],
    image_map: dict[str, Any],
    *,
    knowledge_base_id: str,
    document_id: str,
    mineru_local_output_dir: Path | None = None,
    mineru_legacy_output_dirs: list[Path] | None = None,
) -> list[ParsedBlock]:
    blocks: list[ParsedBlock] = []
    for raw in content_list:
        if not isinstance(raw, dict):
            continue
        raw_type = str(raw.get("type") or raw.get("block_type") or "text")
        kind = _block_type(raw_type)
        page_no = _page_from_block(raw)
        bbox = _bbox_from_block(raw)
        meta = _metadata_from_block(raw, "mineru")
        text_level = raw.get("text_level") if raw.get("text_level") is not None else raw.get("level")
        context_header = ""
        if text_level is not None:
            meta["text_level"] = text_level

        if kind == "table":
            table_body = _clean(raw.get("table_body") or raw.get("html") or raw.get("text") or "")
            table_caption = _caption_text(raw.get("table_caption") or raw.get("caption"))
            if table_caption:
                context_header = table_caption
                meta["caption"] = table_caption
                meta["table_caption"] = table_caption
            # MinerU may return Markdown tables; convert to basic HTML so
            # chunk_service._block_index_content can produce readable text.
            table_html = table_body if "<table" in table_body.lower() else ""
            if not table_html and table_body and ("|" in table_body):
                table_html = _markdown_table_to_html(table_body)
            text = table_body or table_caption or "表格"
            image_info = _save_mineru_image(
                raw,
                image_map,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                order_index=len(blocks),
                mineru_local_output_dir=mineru_local_output_dir,
                mineru_legacy_output_dirs=mineru_legacy_output_dirs,
            )
            if image_info:
                meta["image_info"] = {k: v for k, v in image_info.items() if k != "base64"}
            blocks.append(ParsedBlock(
                block_type="table",
                text=text,
                table_html=table_html,
                image_path=image_info.get("relative_path", ""),
                image_url=image_info.get("url", ""),
                image_base64=image_info.get("base64", ""),
                page_no=page_no,
                bbox=bbox,
                order_index=len(blocks),
                context_header=context_header,
                metadata=meta,
            ))
            continue

        if kind == "image":
            img_path = str(
                raw.get("img_path")
                or raw.get("image_path")
                or raw.get("chart_img_path")
                or raw.get("chart_path")
                or raw.get("path")
                or raw.get("url")
                or ""
            ).strip()
            caption = _caption_text(
                raw.get("image_caption")
                or raw.get("chart_caption")
                or raw.get("caption")
                or raw.get("title")
            )
            footnote = _caption_text(raw.get("image_footnote") or raw.get("chart_footnote") or raw.get("footnote"))
            ocr_text = _text_from_block(raw)
            image_info = _save_mineru_image(
                raw,
                image_map,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                order_index=len(blocks),
                mineru_local_output_dir=mineru_local_output_dir,
                mineru_legacy_output_dirs=mineru_legacy_output_dirs,
            )
            if image_info:
                meta["image_info"] = {k: v for k, v in image_info.items() if k != "base64"}
            if img_path:
                meta["img_path"] = img_path
            if caption:
                # Use standard keys that chunk_service._block_index_content looks for
                meta["caption"] = caption
                meta["figure_caption"] = caption
                meta["image_caption"] = caption
            if footnote:
                meta["footnote"] = footnote
                meta["image_footnote"] = footnote
            if ocr_text:
                meta["ocr_raw_text"] = ocr_text
                meta["ocr_cleaned_text"] = ocr_text
                meta["ocr_text"] = ocr_text
                meta["ocr_status"] = "from_mineru_content_list"
            text = caption or footnote or ocr_text or "PDF 图片"
            blocks.append(ParsedBlock(
                block_type="image",
                text=text,
                image_path=image_info.get("relative_path", img_path),
                image_url=image_info.get("url", img_path if img_path.startswith(("http://", "https://", "/files/")) else ""),
                image_base64=image_info.get("base64", ""),
                page_no=page_no,
                bbox=bbox,
                order_index=len(blocks),
                context_header=caption,
                metadata=meta,
            ))
            continue

        text = _text_from_block(raw)
        if not text:
            continue
        # MinerU title blocks are kept as text chunks but carry text_level so the
        # chunker can expose a context header instead of losing the structure.
        if raw_type.lower() in {"title", "heading"} or text_level is not None:
            context_header = text
            meta["is_heading"] = True
        blocks.append(ParsedBlock(
            block_type="text",
            text=text,
            page_no=page_no,
            bbox=bbox,
            order_index=len(blocks),
            context_header=context_header,
            metadata=meta,
        ))
    return blocks


def _finalize(blocks: list[ParsedBlock], parser: str, extra_meta: dict[str, Any]) -> ParsedDocument:
    blocks = [b for b in blocks if b and (b.index_text() or b.text or b.table_html or b.image_path)]
    for idx, block in enumerate(blocks):
        block.order_index = idx
    content = "\n\n".join(
        b.index_text() or b.text or b.table_html
        for b in blocks
        if (b.index_text() or b.text or b.table_html)
    )
    return ParsedDocument(
        content=_clean(content),
        blocks=blocks,
        metadata=sanitize_json_for_postgres({
            "parser": parser,
            "parser_requested": "mineru",
            "parser_actual": "mineru",
            "fallback_used": False,
            "document_parser_engine": getattr(settings, "document_parser_engine", "auto"),
            "mineru_base_url": _file_parse_url(extra_meta.get("mineru_endpoint", "")),
            "recognition_alignment": "mineru_document_parser_adapter",
            "pdf_engine": "mineru_file_parse",
            "layout_recognizer": "mineru",
            "table_structure_recognizer": "mineru",
            "figure_caption_binding": "mineru_content_list",
            "multi_column_ordering": "mineru_reading_order",
            "vertical_concat_engine": "mineru_block_reflow_ready",
            "mineru_pdf_enabled": True,
            "block_count": len(blocks),
            "text_blocks": sum(1 for b in blocks if b.block_type == "text"),
            "table_blocks": sum(1 for b in blocks if b.block_type == "table"),
            "image_blocks": sum(1 for b in blocks if b.block_type == "image"),
            **extra_meta,
        }),
    )


def parse_document_with_mineru(
    path: Path,
    *,
    knowledge_base_id: str,
    document_id: str,
    config: MinerUPdfConfig,
    file_type: str = "",
    runtime_profile: str | None = None,
) -> ParsedDocument:
    """Parse a document with MinerU via POST /file_parse.

    This is the main entry point for MinerU-based document parsing. It handles
    any format that MinerU supports (PDF, images, DOCX, PPTX, XLSX, etc.).

    Important for current local deployment:
    - know_rag backend runs in Docker.
    - MinerU runs as a Windows-local service.
    - MINERU_OUTPUT_DIR is the path visible to MinerU, e.g. D:/know_rag/cache.
    - LOCAL_CACHE_DIR is the path visible to backend, e.g. /data/cache.
    """
    runtime_profile = _normalize_mineru_profile(runtime_profile or config.runtime_profile)
    if config.runtime_profile != runtime_profile:
        config.runtime_profile = runtime_profile
        config.backend = _profile_backend(runtime_profile)
        config.parse_method = _profile_parse_method(runtime_profile)
    endpoint = _file_parse_url(config.base_url)

    mineru_output_dir: str | None = None
    mineru_local_output_dir = _backend_mineru_output_dir(knowledge_base_id, document_id)

    if getattr(settings, "mineru_send_output_dir", True):
        base_output_dir = (getattr(settings, "mineru_output_dir", "") or "").strip()
        if base_output_dir:
            mineru_output_dir = str(
                Path(base_output_dir)
                / str(knowledge_base_id)
                / str(document_id)
                / "mineru_output"
            ).replace("\\", "/")

    mineru_legacy_output_dirs = _legacy_mineru_output_dirs(
        knowledge_base_id,
        document_id,
        requested_output_dir=mineru_output_dir,
    )

    logger.info(
        "MinerU parse starting: file=%s, type=%s, endpoint=%s, output_dir=%s, backend_output_dir=%s, legacy_roots=%s",
        path.name,
        file_type or path.suffix,
        endpoint,
        mineru_output_dir or "",
        str(mineru_local_output_dir),
        [str(p) for p in mineru_legacy_output_dirs],
    )

    payload = _post_file_parse(path, config, output_dir=mineru_output_dir)

    # Diagnostic: log response structure (keys and types only, not full content)
    if isinstance(payload, dict):
        top_keys = list(payload.keys())
        logger.info("MinerU response top_keys=%s", top_keys)

        # Log the file result structure
        file_result = _find_file_result(payload)
        if isinstance(file_result, dict):
            fr_keys = list(file_result.keys())
            logger.info("MinerU file_result keys=%s", fr_keys)
            for key in fr_keys:
                val = file_result.get(key)
                if isinstance(val, str):
                    logger.info("  %s: str(len=%d)", key, len(val))
                elif isinstance(val, list):
                    logger.info("  %s: list(len=%d)", key, len(val))
                elif isinstance(val, dict):
                    logger.info("  %s: dict(keys=%s)", key, list(val.keys())[:10])
        else:
            logger.info("MinerU file_result: None (no recognized structure found)")

    elif isinstance(payload, list):
        logger.info("MinerU response: list(len=%d)", len(payload))
    else:
        logger.info("MinerU response type=%s", type(payload).__name__)

    content_list = _find_content_list(payload)
    image_map = _find_image_map(payload)

    _dump_mineru_debug_payload(
        payload,
        content_list,
        image_map,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        file_name=path.name,
        endpoint=endpoint,
        mineru_output_dir=mineru_output_dir,
        mineru_local_output_dir=mineru_local_output_dir,
    )

    save_debug_mineru_raw(
        payload=payload,
        content_list=content_list,
        image_map=image_map,
        markdown=_find_markdown(payload),
        source_dir=mineru_local_output_dir,
        source_file_name=path.name,
    )

    if content_list:
        blocks = _content_list_to_blocks(
            content_list,
            image_map,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            mineru_local_output_dir=mineru_local_output_dir,
            mineru_legacy_output_dirs=mineru_legacy_output_dirs,
        )
        if blocks:
            _cleanup_legacy_mineru_outputs(path.name, mineru_legacy_output_dirs)
            return _finalize(
                blocks,
                "mineru",
                {
                    "mineru_config_source": config.source,
                    "mineru_endpoint": endpoint,
                    "mineru_output_dir": mineru_output_dir or "",
                    "mineru_local_output_dir": str(mineru_local_output_dir),
                    "mineru_legacy_output_dirs": [str(p) for p in mineru_legacy_output_dirs],
                    "mineru_content_list_count": len(content_list),
                    "mineru_image_map_count": len(image_map),
                    "mineru_content_list_used": True,
                    "file_type": file_type or path.suffix,
                },
            )

    markdown = _find_markdown(payload)

    if markdown:
        block = ParsedBlock(
            block_type="text",
            text=markdown,
            order_index=0,
            metadata={
                "parser": "mineru",
                "source": "mineru_markdown_fallback",
            },
        )
        _cleanup_legacy_mineru_outputs(path.name, mineru_legacy_output_dirs)
        return _finalize(
            [block],
            "mineru_markdown_fallback",
            {
                "mineru_config_source": config.source,
                "mineru_endpoint": endpoint,
                "mineru_output_dir": mineru_output_dir or "",
                "mineru_local_output_dir": str(mineru_local_output_dir),
                "mineru_legacy_output_dirs": [str(p) for p in mineru_legacy_output_dirs],
                "mineru_content_list_count": 0,
                "mineru_content_list_used": False,
                "mineru_markdown_fallback": True,
                "file_type": file_type or path.suffix,
            },
        )

    # Build a helpful error message with response structure summary
    summary_parts = ["MinerU response did not contain usable content_list or markdown"]

    if isinstance(payload, dict):
        top_keys = list(payload.keys())
        summary_parts.append("top_keys={}".format(top_keys))
        fr = _find_file_result(payload)
        if isinstance(fr, dict):
            summary_parts.append("file_result_keys={}".format(list(fr.keys())))
        else:
            summary_parts.append("file_result=None (no results/result/data wrapper found)")
    elif isinstance(payload, list):
        summary_parts.append("type=list len={}".format(len(payload)))
    else:
        summary_parts.append("type={}".format(type(payload).__name__))

    _cleanup_legacy_mineru_outputs(path.name, mineru_legacy_output_dirs)
    raise RuntimeError("; ".join(summary_parts))


# Backward-compatible alias
parse_pdf_with_mineru = parse_document_with_mineru
