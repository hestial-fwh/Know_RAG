import asyncio
import base64
import logging
from pathlib import Path
from typing import Awaitable, Callable, Iterable, TypeVar
from app.core.config import settings
import httpx
from sqlalchemy.orm import Session

from types import SimpleNamespace
from app.db.models import ModelConfig
from app.services.document_parser.types import ParsedBlock
from app.services.storage import get_file_bytes, is_minio_uri
from app.utils.text import sanitize_text_for_postgres

logger = logging.getLogger(__name__)
T = TypeVar("T")

async def _with_retries(label: str, fn: Callable[[], Awaitable[T]], *, retries: int = 3, base_delay: float = 1.5) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, max(retries, 1) + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            logger.warning("%s attempt %s/%s failed: %s", label, attempt, retries, exc)
            if attempt < retries:
                await asyncio.sleep(base_delay * attempt)
    assert last_exc is not None
    raise last_exc

DEFAULT_PROMPT = "请用中文简洁描述这张文档图片的主要内容。如果图片包含图表、流程、数字或文字，请提取关键信息。"
OCR_PROMPT = (
    "请对这张扫描文档图片进行 OCR。只输出图片中的可读文字，保持原始语义和段落顺序；"
    "如果是表格，请用 Markdown 表格或逐行键值形式输出；不要编造不存在的内容。"
)
def _build_mineru_output_dir(
    *,
    knowledge_base_id: str | None = None,
    document_id: str | None = None,
    subdir: str = "mineru_ocr_output",
) -> str:
    """Build an output_dir path for Windows-local MinerU.

    Important:
    - This path is for the MinerU process, not for the backend container.
    - Do not mkdir here, because backend may run in Linux Docker while MinerU runs on Windows.
    - Example local dev:
        MINERU_OUTPUT_DIR=D:/know_rag/cache
        output_dir=D:/know_rag/cache/{kb_id}/{doc_id}/mineru_ocr_output
    """
    if not getattr(settings, "mineru_send_output_dir", True):
        return ""

    base_output_dir = (getattr(settings, "mineru_output_dir", "") or "").strip()
    if not base_output_dir:
        return ""

    if knowledge_base_id and document_id:
        output_dir = Path(base_output_dir) / str(knowledge_base_id) / str(document_id) / subdir
    else:
        # Fallback: still avoid MinerU default ./output, but this shared cache
        # cannot be cleaned by delete_document_cache unless caller passes ids.
        output_dir = Path(base_output_dir) / "_shared" / subdir

    return str(output_dir).replace("\\", "/")

def _get_model_config(db: Session, model_type: str) -> ModelConfig | SimpleNamespace | None:
    row = db.query(ModelConfig).filter(ModelConfig.model_type == model_type).first()
    if row:
        if (row.provider or "").lower() == "deepdoc-builtin":
            return row
        if row.model_name and row.base_url:
            return row
    if model_type == "ocr":
        # RAGFlow-style default: OCR is provided by the built-in DeepDoc path,
        # not by a separately configured remote OCR API.
        return SimpleNamespace(provider="deepdoc-builtin", model_name="deepdoc-local-ocr", base_url="", api_key="")
    return None


def get_image2text_config(db: Session) -> ModelConfig | None:
    return _get_model_config(db, "image2text")


def get_ocr_config(db: Session) -> ModelConfig | None:
    return _get_model_config(db, "ocr")


def _as_data_url(image_base64: str, mime_type: str = "image/png") -> str:
    if image_base64.startswith("data:"):
        return image_base64
    return f"data:{mime_type};base64,{image_base64}"


def _strip_data_url(image_base64: str) -> str:
    if image_base64.startswith("data:"):
        return image_base64.split(",", 1)[-1]
    return image_base64


def _chat_completions_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


OPENAI_COMPATIBLE_VISION_PROVIDERS = {
    "openai",
    "openai-compatible",
    "api",
    "custom",
    "dashscope",
    "aliyun",
    "aliyun-dashscope",
    "siliconflow",
    "zhipu",
    "hunyuan",
    "volcengine",
    "ark",
    "xai",
    "stepfun",
    "deepseek",
}


def _provider_slug(config: ModelConfig | SimpleNamespace) -> str:
    return (getattr(config, "provider", "") or "").strip().lower()


async def _vision_chat_with_config(config: ModelConfig | SimpleNamespace, image_base64: str, prompt: str) -> str:
    provider = _provider_slug(config)
    base_url = (getattr(config, "base_url", "") or "").rstrip("/")
    model_name = getattr(config, "model_name", "") or ""
    if not image_base64 or not model_name or not base_url:
        return ""

    if provider in {"ollama", "local_ollama"}:
        raw_b64 = _strip_data_url(image_base64)
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt, "images": [raw_b64]}],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return sanitize_text_for_postgres((data.get("message") or {}).get("content") or data.get("response") or "")

    if provider in OPENAI_COMPATIBLE_VISION_PROVIDERS:
        headers = {"Content-Type": "application/json"}
        api_key = getattr(config, "api_key", "") or ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _as_data_url(image_base64)}},
                    ],
                }
            ],
            "temperature": 0.0,
        }
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(_chat_completions_url(base_url), headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return sanitize_text_for_postgres(data.get("choices", [{}])[0].get("message", {}).get("content", ""))

    raise ValueError(f"Unsupported vision provider for Image2Text/OCR: {getattr(config, 'provider', '') or '-'}")


async def _custom_ocr_with_config(
    config: ModelConfig | SimpleNamespace,
    image_base64: str,
    prompt: str = OCR_PROMPT,
    *,
    knowledge_base_id: str | None = None,
    document_id: str | None = None,
) -> str:
    """Best-effort OCR endpoint adapter.

    For openai-compatible / ollama providers we use the normal vision chat API.
    For provider values such as paddleocr/custom-ocr, we try a simple JSON OCR
    endpoint first, then fall back to vision chat if the endpoint is compatible.
    """

    provider = (getattr(config, "provider", "") or "").lower()

    if provider == "deepdoc-builtin":
        from app.services.document_parser.deepdoc_runtime import get_deepdoc_runtime

        return get_deepdoc_runtime().recognize_text(image_base64, prompt=prompt)

    if provider in {"ollama", "local_ollama"} or provider in OPENAI_COMPATIBLE_VISION_PROVIDERS:
        return await _vision_chat_with_config(config, image_base64, prompt)

    if provider == "mineru":
        base_url = (getattr(config, "base_url", "") or "").rstrip("/")
        if not base_url:
            return ""

        mineru_headers: dict[str, str] = {}
        api_key = getattr(config, "api_key", "") or ""
        if api_key:
            mineru_headers["Authorization"] = f"Bearer {api_key}"

        raw_bytes = base64.b64decode(_strip_data_url(image_base64))
        files = {"files": ("ocr_image.png", raw_bytes, "image/png")}

        # Always pass backend/parse options.  Otherwise MinerU may use its own
        # service default and accidentally enter VLM/lmdeploy mode even when the
        # project is configured for CPU pipeline.
        mineru_data = {
            "lang_list": getattr(settings, "mineru_lang", "ch") or "ch",
            "backend": getattr(settings, "mineru_cpu_backend", "pipeline") or "pipeline",
            "parse_method": getattr(settings, "mineru_cpu_parse_method", "auto") or "auto",
            "formula_enable": str(getattr(settings, "mineru_enable_formula", True)).lower(),
            "table_enable": str(getattr(settings, "mineru_enable_table", True)).lower(),
            "return_content_list": "true",
            "return_images": "false",
        }

        output_dir = _build_mineru_output_dir(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            subdir="mineru_ocr_output",
        )
        if output_dir:
            mineru_data["output_dir"] = output_dir

        logger.info(
            "MinerU OCR request prepared: base_url=%s, output_dir=%s, document_id=%s",
            base_url,
            output_dir or "",
            document_id or "",
        )

        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{base_url}/file_parse",
                headers=mineru_headers,
                files=files,
                data=mineru_data,
            )

        if resp.status_code != 200:
            body_preview = resp.text[:200]
            raise RuntimeError(
                f"MinerU parse failed: status={resp.status_code}, body={body_preview}"
            )

        try:
            resp_data = resp.json()
        except Exception:
            raise RuntimeError("MinerU parse failed: response is not valid JSON")

        blocks: list[dict] = []

        if isinstance(resp_data, list):
            blocks = [item for item in resp_data if isinstance(item, dict)]
        elif isinstance(resp_data, dict):
            direct = resp_data.get("content_list") or resp_data.get("data") or []
            if isinstance(direct, list):
                blocks = [item for item in direct if isinstance(item, dict)]

            if not blocks:
                for container_key in ("results", "result", "output"):
                    container = resp_data.get(container_key)
                    if not isinstance(container, dict):
                        continue

                    for file_result in container.values():
                        if not isinstance(file_result, dict):
                            continue

                        content_list = file_result.get("content_list") or file_result.get("data") or []
                        if isinstance(content_list, list):
                            blocks = [item for item in content_list if isinstance(item, dict)]
                            if blocks:
                                break

                    if blocks:
                        break

        text_parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type", "")
            if block_type in {"text", "equation", "code", "list"}:
                text_parts.append(str(block.get("text") or ""))
            elif block_type == "table":
                text_parts.append(str(block.get("table_body") or ""))
            elif block_type == "image":
                captions = block.get("image_caption") or []
                if isinstance(captions, list):
                    text_parts.extend(str(item) for item in captions if str(item or "").strip())
                elif isinstance(captions, str):
                    text_parts.append(captions)

        result = "\n".join(part for part in text_parts if part).strip()
        return sanitize_text_for_postgres(result)

    base_url = (getattr(config, "base_url", "") or "").rstrip("/")
    if not base_url:
        return ""

    headers = {"Content-Type": "application/json"}
    api_key = getattr(config, "api_key", "") or ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": getattr(config, "model_name", "") or "",
        "image": _as_data_url(image_base64),
        "image_base64": _strip_data_url(image_base64),
        "prompt": prompt,
    }

    endpoint = base_url if base_url.endswith("/ocr") else f"{base_url}/ocr"

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(endpoint, headers=headers, json=payload)
        if resp.status_code == 404 and endpoint != base_url:
            resp = await client.post(base_url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    text = data.get("text") or data.get("ocr_text") or data.get("result")
    if isinstance(text, dict):
        text = text.get("text") or text.get("content")
    if not text and isinstance(data.get("data"), dict):
        text = data["data"].get("text") or data["data"].get("ocr_text")

    return sanitize_text_for_postgres(str(text or ""))

async def describe_image_with_config(
    config: ModelConfig | SimpleNamespace,
    image_base64: str,
    prompt: str = DEFAULT_PROMPT,
) -> str:
    return await _vision_chat_with_config(config, image_base64, prompt)


async def recognize_image_with_config(
    config: ModelConfig | SimpleNamespace,
    image_base64: str,
    prompt: str = OCR_PROMPT,
    *,
    knowledge_base_id: str | None = None,
    document_id: str | None = None,
) -> str:
    return await _custom_ocr_with_config(
        config,
        image_base64,
        prompt,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )

def _ensure_block_image_base64(block: ParsedBlock) -> str:
    """Return base64 image data for an image/table block.

    Some RAGFlow adapter paths store an image file path/url but omit the in-memory
    base64 payload.  OCR/VLM enhancement should work for all image blocks, so we
    lazily read the saved media file and backfill block.image_base64.
    """
    if block.image_base64:
        return block.image_base64
    info = (block.metadata or {}).get("image_info") or {}
    candidates = [
        getattr(block, "image_path", "") or "",
        info.get("path") or "",
        info.get("relative_path") or "",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            object_ref = candidate[len("/files/"):] if candidate.startswith("/files/") else candidate
            if is_minio_uri(object_ref) or not str(object_ref).startswith("/"):
                data = get_file_bytes(object_ref)
                b64 = base64.b64encode(data).decode("utf-8")
                block.image_base64 = b64
                return b64
        except Exception:
            pass

        path = Path(candidate)
        if not path.is_absolute():
            try:
                from app.core.config import settings
                path = Path(settings.local_storage_dir) / candidate
            except Exception:
                pass
        try:
            if path.exists() and path.is_file():
                b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
                block.image_base64 = b64
                return b64
        except Exception:
            continue
    return ""


async def enhance_ocr_blocks(
    db: Session,
    blocks: Iterable[ParsedBlock],
    *,
    knowledge_base_id: str | None = None,
    document_id: str | None = None,
) -> int:
    config = get_ocr_config(db)
    if config is None:
        return 0

    enhanced = 0

    for block in blocks:
        image_base64 = _ensure_block_image_base64(block)
        if block.block_type not in {"image", "table"} or not image_base64:
            continue

        if (block.metadata or {}).get("ocr_raw_text") or (block.metadata or {}).get("ocr_cleaned_text"):
            continue

        source = (block.metadata or {}).get("source")

        if (
            source
            not in {
                "pdf_page_render",
                "pdfplumber_figure_crop",
                "pdfplumber_table",
                "pdfplumber_table_crop",
                "uploaded_image",
                "pptx_media",
                "epub_media",
                "mineru_content_list",
            }
            and not (block.metadata or {}).get("ocr_candidate")
        ):
            continue

        block_kb_id = (
            knowledge_base_id
            or (block.metadata or {}).get("knowledge_base_id")
            or (block.metadata or {}).get("kb_id")
        )
        block_doc_id = (
            document_id
            or (block.metadata or {}).get("document_id")
            or (block.metadata or {}).get("doc_id")
        )

        try:
            ocr_text = await _with_retries(
                "OCR",
                lambda: recognize_image_with_config(
                    config,
                    image_base64,
                    knowledge_base_id=block_kb_id,
                    document_id=block_doc_id,
                ),
            )
        except Exception as exc:
            logger.warning("OCR failed for %s: %s", block.image_url or block.image_path, exc)
            block.metadata["ocr_status"] = "failed"
            block.metadata["ocr_error"] = str(exc)[:300]
            continue

        if not ocr_text:
            if (getattr(config, "provider", "") or "").lower() == "deepdoc-builtin":
                block.metadata["ocr_status"] = "skipped"
                block.metadata["ocr_provider"] = "deepdoc-builtin"
                block.metadata["ocr_note"] = (
                    "DeepDoc 内置 OCR 默认启用；当前运行环境未检测到可用本地 OCR 引擎或未返回文本。"
                )
            continue

        cleaned_ocr = sanitize_text_for_postgres(ocr_text)
        previous = block.text.strip()

        if not previous:
            block.text = cleaned_ocr

        block.metadata["ocr_raw_text"] = ocr_text
        block.metadata["ocr_cleaned_text"] = cleaned_ocr
        block.metadata["ocr_text"] = cleaned_ocr
        block.metadata["ocr_model"] = getattr(config, "model_name", "") or ""
        block.metadata["ocr_provider"] = getattr(config, "provider", "") or ""
        block.metadata["ocr_status"] = "success"

        enhanced += 1

    return enhanced
async def enhance_image_blocks(db: Session, blocks: Iterable[ParsedBlock]) -> int:
    config = get_image2text_config(db)
    if config is None:
        return 0
    enhanced = 0
    for block in blocks:
        image_base64 = _ensure_block_image_base64(block)
        if block.block_type != "image" or not image_base64:
            continue
        try:
            description = await _with_retries("Image2Text", lambda: describe_image_with_config(config, image_base64))
        except Exception as exc:
            logger.warning("Image2Text failed for %s: %s", block.image_url or block.image_path, exc)
            block.metadata["vlm_status"] = "failed"
            block.metadata["vlm_error"] = str(exc)[:300]
            continue
        if not description:
            block.metadata["vlm_status"] = "empty"
            block.metadata["vlm_provider"] = config.provider
            block.metadata["vlm_model"] = config.model_name
            continue
        # Keep VLM output separate from OCR/parser text. The chunk builder will
        # compose image chunk content from vlm_description and generate OCR text
        # chunks from ocr_raw_text/ocr_cleaned_text when available.
        block.metadata["vlm_description"] = description
        block.metadata["vlm_model"] = config.model_name
        block.metadata["vlm_provider"] = config.provider
        block.metadata["vlm_status"] = "success"
        enhanced += 1
    return enhanced
