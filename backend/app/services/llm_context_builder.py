
"""Build clean LLM-ready context from retrieval results.

This module is intentionally separated from retrieval ranking.
Retrieval APIs may return rich/debug payloads for front-end inspection, while
this builder keeps only the evidence that should be sent to a model:
matched child content, parent context, and concise summaries of related
image/table/equation children.

Design goals:
- Do not send debugging fields such as chunk id, rank, matched/neighbor labels,
  scores, or raw metadata to LLMs.
- Prefer parent-group level sources.  One source should normally represent one
  parent context, not one raw child hit.
- Extract document title/file/heading robustly from direct fields, metadata,
  context headers and cleaned content text.
- Keep image URLs disabled by default.  Text LLMs get image semantics
  (caption/OCR/VLM text), not local URLs, unless explicitly enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import os
import re
from typing import Any
from urllib.parse import urlsplit

from app.core.config import settings


_IMAGE_KINDS = {"image", "chart", "graph", "plot", "figure", "fig"}
_TABLE_KINDS = {"table", "table_body"}
_EQUATION_KINDS = {"equation", "formula", "inline_equation", "interline_equation", "display_equation"}
_TEXT_KINDS = {"text", "list", "paragraph", "title", "heading"}

_DEBUG_KEYS = {
    "chunk_id",
    "parent_chunk_id",
    "score",
    "rrf_score",
    "vector_score",
    "keyword_score",
    "vector_rank",
    "keyword_rank",
    "rank",
    "relation",
    "is_matched",
    "metadata",
    "metadata_json",
    "image_info",
}


@dataclass
class ContextBudget:
    max_chars: int
    used_chars: int = 0

    def add(self, text: str) -> tuple[str, bool]:
        """Return text clipped to remaining budget and whether clipping happened."""
        text = str(text or "")
        if self.max_chars <= 0:
            return "", bool(text)
        remaining = self.max_chars - self.used_chars
        if remaining <= 0:
            return "", bool(text)
        if len(text) <= remaining:
            self.used_chars += len(text)
            return text, False
        clipped = text[:remaining].rstrip()
        self.used_chars += len(clipped)
        return clipped, True


def _as_int(value: Any, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value) if value is not None and value != "" else int(default)
    except Exception:
        parsed = int(default)
    parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


def _metadata(item: dict | None) -> dict:
    if not isinstance(item, dict):
        return {}
    for key in ("metadata", "metadata_json", "chunk_metadata"):
        value = item.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _norm_space(text: Any) -> str:
    text = unescape(str(text or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    # Preserve rough table cell boundaries before stripping tags.
    text = re.sub(r"</t[dh]>", " | ", text, flags=re.I)
    text = re.sub(r"</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _clean_llm_text(text: Any, *, max_chars: int | None = None) -> str:
    """Clean text for model context, not for UI debugging.

    This intentionally removes HTML wrappers, excessive whitespace and some
    raw storage-only lines, while keeping scientific formula text and table text.
    """
    text = _norm_space(text)
    if not text:
        return ""
    text = _strip_html(text)
    text = _norm_space(text)

    cleaned_lines: list[str] = []
    for raw_line in text.split("\n"):
        line = _norm_space(raw_line)
        if not line:
            continue
        # Drop storage-only filename lines if they are not useful captions.
        if re.match(r"^(文件名|filename|file name)[:：]\s*[^\s]+\.(png|jpg|jpeg|webp|gif|bmp)$", line, re.I):
            continue
        # Drop pure local/minio path lines.
        if line.startswith(("minio://", "/data/", "D:/", "C:/")):
            continue
        # Very long object keys are not useful for LLM.
        if re.search(r"kb/[0-9a-f-]{20,}/documents/[0-9a-f-]{20,}/media/", line):
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\|\s*\|\s*\|", "|", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _first_str_from_dict(dct: dict | None, keys: tuple[str, ...] | list[str]) -> str:
    if not isinstance(dct, dict):
        return ""
    for key in keys:
        value = dct.get(key)
        if isinstance(value, str) and value.strip():
            return _norm_space(value)
        if isinstance(value, (list, tuple)) and value:
            joined = " > ".join(str(v).strip() for v in value if str(v).strip())
            if joined:
                return joined
    return ""


def _text_from(item: dict | None, *keys: str, max_chars: int | None = None, clean: bool = True) -> str:
    if not isinstance(item, dict):
        return ""
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            text = _clean_llm_text(value, max_chars=max_chars) if clean else _norm_space(value)
            return text
    return ""


def _parse_labeled_value(text: str, labels: tuple[str, ...]) -> str:
    text = _norm_space(text)
    if not text:
        return ""
    label_pattern = "|".join(re.escape(label) for label in labels)
    # Match line-based labels: 文档：xxx, 文件：xxx, 文档标题：xxx
    pattern = re.compile(rf"(?:^|\n)\s*(?:{label_pattern})\s*[:：]\s*(.+?)(?=\n|$)", re.I)
    match = pattern.search(text)
    if not match:
        return ""
    value = _norm_space(match.group(1))
    value = re.sub(r"[。；;]+$", "", value).strip()
    return value[:200]


def _heading_from_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return " > ".join(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, str):
        return _norm_space(value)
    return ""


def _context_header(item: dict | None, parent: dict | None = None) -> str:
    candidates: list[str] = []
    for obj in (parent, item):
        if not isinstance(obj, dict):
            continue
        direct = _first_str_from_dict(obj, ("context_header", "heading_path", "heading", "section_title"))
        if direct:
            candidates.append(direct)
        meta = _metadata(obj)
        meta_header = _heading_from_value(
            meta.get("heading_path")
            or meta.get("context_header")
            or meta.get("section_title")
            or meta.get("title")
        )
        if meta_header:
            candidates.append(meta_header)
    for candidate in candidates:
        if candidate and candidate not in {"-", "未知"}:
            return candidate[:300]
    return ""


def _title_from_text(text: str) -> str:
    text = _norm_space(text)
    if not text:
        return ""
    value = _parse_labeled_value(text, ("文档", "文档标题", "文件", "文件名", "Document", "File", "Filename"))
    if value:
        return value
    # Some parent_context starts with "文档：xxx\n章节：..." after display cleaning.
    first_line = next((line.strip() for line in text.split("\n") if line.strip()), "")
    if first_line.startswith(("文档：", "文件：", "文档标题：", "文件名：")):
        return first_line.split("：", 1)[-1].strip()[:200]
    return ""


def _source_title(item: dict | None, parent: dict | None = None) -> str:
    """Best-effort document title extraction.

    Retrieval result payloads do not always include document_title directly, so
    this function also looks into metadata, context headers and cleaned content.
    """
    objects = [item, parent]
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        direct = _first_str_from_dict(
            obj,
            (
                "document_title",
                "knowledge_title",
                "doc_title",
                "title",
                "file_name",
                "filename",
                "knowledge_filename",
            ),
        )
        if direct:
            return direct
        meta = _metadata(obj)
        meta_title = _first_str_from_dict(
            meta,
            (
                "document_title",
                "doc_title",
                "title",
                "file_name",
                "filename",
                "source_file",
                "knowledge_filename",
            ),
        )
        if meta_title:
            return meta_title

    # Fallback: parse from parent/child display or raw content.
    for obj in objects:
        text = _text_from(obj, "display_content", "display_preview", "content", "content_preview", "matched_content", clean=False)
        parsed = _title_from_text(text)
        if parsed:
            return parsed

    header = _context_header(item, parent)
    if header:
        # context_header often looks like: 文档标题 > 章节 > 小节
        first = header.split(">", 1)[0].strip()
        if first:
            return first[:200]

    return "未知文档"


def _source_file(item: dict | None, parent: dict | None = None) -> str:
    for obj in (item, parent):
        if not isinstance(obj, dict):
            continue
        direct = _first_str_from_dict(obj, ("file_name", "filename", "knowledge_filename", "source_file"))
        if direct:
            return direct
        meta = _metadata(obj)
        meta_file = _first_str_from_dict(meta, ("file_name", "filename", "source_file", "knowledge_filename"))
        if meta_file:
            return meta_file
    for obj in (item, parent):
        text = _text_from(obj, "display_content", "display_preview", "content", "content_preview", clean=False)
        parsed = _parse_labeled_value(text, ("文件", "文件名", "File", "Filename"))
        if parsed:
            return parsed
    return ""


def _page_no(item: dict | None) -> int | None:
    metadata = _metadata(item)
    for key in ("page_no", "source_page", "page", "page_idx", "page_index"):
        value = item.get(key) if isinstance(item, dict) else None
        if value is None:
            value = metadata.get(key)
        try:
            if value is None or value == "":
                continue
            parsed = int(value)
            if key in {"page_idx", "page_index"}:
                return parsed + 1 if parsed >= 0 else None
            return parsed
        except Exception:
            continue
    return None


def _public_base_url() -> str:
    """Return the public gateway base URL used to expose local file resources.

    Typical value:
        PUBLIC_BASE_URL=http://sqx6.ipyingshe.net
    """
    for key in ("PUBLIC_BASE_URL", "KNOW_RAG_PUBLIC_BASE_URL", "API_PUBLIC_BASE_URL"):
        value = (os.getenv(key) or "").strip().rstrip("/")
        if value:
            return value

    for key in ("public_base_url", "PUBLIC_BASE_URL"):
        value = str(getattr(settings, key, "") or "").strip().rstrip("/")
        if value:
            return value

    return ""


def _to_public_url(url: str | None) -> str:
    """Convert a local/relative file URL to a public URL."""
    value = str(url or "").strip()
    if not value:
        return ""

    public_base = _public_base_url()
    if not public_base:
        return value

    if value.startswith("/"):
        return f"{public_base}{value}"

    lowered = value.lower()
    if lowered.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if host in {"127.0.0.1", "localhost", "backend", "minio", "gateway"}:
            query = f"?{parsed.query}" if parsed.query else ""
            return f"{public_base}{parsed.path}{query}"
        return value

    if value.startswith(("files/", "api/", "media/")):
        return f"{public_base}/{value}"

    return value


def _frontend_document_url(document_id: str | None) -> str:
    """Build the frontend document content page URL for MCP responses."""
    if not document_id:
        return ""
    base = str(settings.frontend_base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/documents/{document_id}/content"


def _image_url(item: dict | None, *, include_image_urls: bool) -> str:
    """Resolve an image/table asset URL and convert it to a public URL.

    Keep URLs out of content fields; expose them only as `image_url`.
    """
    if not include_image_urls or not isinstance(item, dict):
        return ""

    candidates: list[str] = []

    def collect(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        for key in ("public_url", "image_url", "url", "src", "file_url", "object_url"):
            raw = obj.get(key)
            if isinstance(raw, str) and raw.strip():
                candidates.append(raw.strip())

    collect(item)

    image_info = item.get("image_info")
    collect(image_info)

    metadata = _metadata(item)
    collect(metadata)

    nested = metadata.get("image_info") if isinstance(metadata, dict) else None
    collect(nested)

    for obj in (item, metadata, image_info, nested):
        if not isinstance(obj, dict):
            continue
        for key in ("path", "file_path", "local_path", "media_path"):
            raw = obj.get(key)
            if isinstance(raw, str) and raw.strip():
                candidates.append(raw.strip())

    for candidate in candidates:
        public_url = _to_public_url(candidate)
        if public_url:
            return public_url

    return ""
def _chunk_kind(item: dict | None) -> str:
    if not isinstance(item, dict):
        return "text"
    metadata = _metadata(item)
    raw_type = str(metadata.get("raw_block_type") or metadata.get("type") or "").strip().lower()
    subtype = str(metadata.get("subtype") or metadata.get("visual_subtype") or "").strip().lower()
    chunk_type = str(item.get("chunk_type") or metadata.get("chunk_type") or "text").strip().lower()
    if metadata.get("is_equation") or subtype in _EQUATION_KINDS or raw_type in _EQUATION_KINDS:
        return "equation"
    if chunk_type == "image" and subtype:
        return subtype
    if raw_type in _IMAGE_KINDS:
        return raw_type
    if raw_type in _TABLE_KINDS:
        return "table"
    if chunk_type == "image_ocr_text":
        return "text"
    return chunk_type or "text"


def _semantic_label(kind: str) -> str:
    kind = (kind or "text").lower()
    if kind in {"chart", "graph", "plot"}:
        return "图表"
    if kind in {"image", "figure", "fig"}:
        return "图片"
    if kind == "table":
        return "表格"
    if kind == "equation":
        return "公式"
    return "文本"


def _child_text_for_llm(child: dict, *, max_chars: int = 1200) -> str:
    """Return clean semantic text for one child evidence."""
    metadata = _metadata(child)
    candidates: list[str] = []

    # Prefer display fields already cleaned by retrieval_service.
    for key in ("display_content", "display_preview", "matched_content", "content", "content_preview"):
        value = child.get(key) if isinstance(child, dict) else None
        if isinstance(value, str) and value.strip():
            candidates.append(value)

    # Add semantic fields useful for visual/equation/table chunks.
    for key in (
        "caption",
        "figure_caption",
        "image_caption",
        "table_caption",
        "ocr_text",
        "ocr_cleaned_text",
        "ocr_raw_text",
        "vlm_description",
        "description",
        "latex",
        "equation",
        "table_body",
    ):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value)
        elif isinstance(value, list):
            joined = "\n".join(str(v) for v in value if str(v).strip())
            if joined:
                candidates.append(joined)

    for candidate in candidates:
        text = _clean_llm_text(candidate, max_chars=max_chars)
        if text:
            return text
    return ""


def _format_child_evidence(
    child: dict,
    *,
    source_id: str,
    document_title: str,
    file_name: str,
    context_header: str,
    include_image_urls: bool,
    max_chars: int = 1200,
) -> tuple[str, dict]:
    kind = _chunk_kind(child)
    label = _semantic_label(kind)
    text = _child_text_for_llm(child, max_chars=max_chars)
    page = _page_no(child)
    image_url = _image_url(child, include_image_urls=include_image_urls)

    meta_bits = [label]
    if page:
        meta_bits.append(f"第 {page} 页")
    if context_header:
        meta_bits.append(context_header[:100])
    prefix = " / ".join(meta_bits)

    if not text:
        text = "（该证据没有可用文本描述）"
    line = f"- [{prefix}] {text}"
    if image_url:
        line += f"\n  图片URL：{image_url}"

    source = {
        "source_id": source_id,
        "document_title": document_title,
        "file_name": file_name,
        "context_header": context_header,
        "chunk_type": child.get("chunk_type") or "text",
        "semantic_type": kind,
        "semantic_label": label,
        "page_no": page,
        "content": text,
    }
    if image_url:
        source["image_url"] = image_url
    return line, source


def _iter_parent_groups(retrieval_data: dict) -> list[dict]:
    groups = retrieval_data.get("parent_groups") or retrieval_data.get("grouped_results") or []
    return groups if isinstance(groups, list) else []


def _iter_flat_results(retrieval_data: dict) -> list[dict]:
    results = retrieval_data.get("results") or []
    return results if isinstance(results, list) else []


def _dedup_children(children: list[Any]) -> list[dict]:
    seen_keys: set[str] = set()
    unique: list[dict] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        key = str(child.get("chunk_id") or child.get("id") or "")
        if not key:
            content_key = _clean_llm_text(_child_text_for_llm(child, max_chars=200))[:120]
            key = f"content:{content_key}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(child)
    return unique


def _source_key_from_group(group: dict, first_child: dict | None, parent: dict | None) -> str:
    parent_id = group.get("parent_chunk_id") or group.get("parent_key")
    if parent_id:
        return str(parent_id)
    if isinstance(first_child, dict):
        return str(first_child.get("parent_chunk_id") or first_child.get("document_id") or first_child.get("chunk_id") or "")
    if isinstance(parent, dict):
        return str(parent.get("chunk_id") or "")
    return ""


def _count_kinds(children: list[dict]) -> dict[str, int]:
    counts = {"text": 0, "image": 0, "table": 0, "equation": 0}
    for child in children:
        kind = _chunk_kind(child)
        if kind in _IMAGE_KINDS:
            counts["image"] += 1
        elif kind == "table":
            counts["table"] += 1
        elif kind == "equation":
            counts["equation"] += 1
        else:
            counts["text"] += 1
    return counts


def _is_related_child_allowed(kind: str, *, include_images: bool, include_tables: bool, include_equations: bool) -> bool:
    if kind in _IMAGE_KINDS:
        return include_images
    if kind == "table":
        return include_tables
    if kind == "equation":
        return include_equations
    return True


def build_llm_context_payload(retrieval_data: dict, options: dict | None = None) -> dict[str, Any]:
    """Convert retrieval output into a clean context payload for LLM/MCP.

    The returned `context_text` is suitable for text LLMs. Image URLs are omitted
    by default to avoid sending local resource URLs to a cloud model unless the
    caller explicitly enables them.
    """
    options = options or {}
    query = str(
        options.get("query_text")
        or options.get("query")
        or retrieval_data.get("query_text")
        or retrieval_data.get("query")
        or ""
    ).strip()
    max_context_chars = _as_int(
        options.get("max_context_chars"),
        getattr(settings, "llm_context_max_chars", 8000),
        minimum=500,
        maximum=100000,
    )
    max_sources = _as_int(
        options.get("max_sources"),
        getattr(settings, "llm_context_max_sources", 8),
        minimum=1,
        maximum=100,
    )
    include_images = _as_bool(options.get("include_images"), getattr(settings, "llm_context_include_images", True))
    include_image_urls = _as_bool(
        options.get("include_image_urls"),
        getattr(settings, "llm_context_include_image_urls", False),
    )
    include_tables = _as_bool(options.get("include_tables"), True)
    include_equations = _as_bool(options.get("include_equations"), True)

    budget = ContextBudget(max_context_chars)
    sections: list[str] = []
    sources: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    truncated = False

    header = (
        "你是知识库问答助手。请只基于以下资料回答用户问题；如果资料不足，请明确说明无法确定。\n\n"
        f"用户问题：{query or '（未提供）'}\n\n"
    )
    clipped, was_truncated = budget.add(header)
    if clipped:
        sections.append(clipped)
    truncated = truncated or was_truncated

    parent_groups = _iter_parent_groups(retrieval_data)
    used_source_keys: set[str] = set()

    if parent_groups:
        for group in parent_groups:
            if len(sources) >= max_sources or truncated:
                break
            if not isinstance(group, dict):
                continue
            parent = group.get("parent_context") or {}
            if not isinstance(parent, dict):
                parent = {}
            matched_children = _dedup_children(group.get("matched_children") or [])
            parent_children = _dedup_children(group.get("parent_children") or [])
            all_children = _dedup_children(matched_children + parent_children)
            first_child = matched_children[0] if matched_children else (parent_children[0] if parent_children else {})

            source_key = _source_key_from_group(group, first_child, parent)
            if source_key and source_key in used_source_keys:
                continue
            if source_key:
                used_source_keys.add(source_key)

            source_id = f"S{len(sources) + 1}"
            title = _source_title(first_child, parent)
            file_name = _source_file(first_child, parent)
            parent_header = _context_header(first_child, parent)
            parent_text = _text_from(parent, "display_content", "display_preview", "content", "content_preview", max_chars=5000)

            lines = [f"【资料 {source_id}】", f"文档：{title}"]
            if file_name and file_name != title:
                lines.append(f"文件：{file_name}")
            if parent_header:
                lines.append(f"章节/上下文：{parent_header}")

            if matched_children:
                lines.append("命中内容：")
                for child in matched_children[:10]:
                    line, source = _format_child_evidence(
                        child,
                        source_id=source_id,
                        document_title=title,
                        file_name=file_name,
                        context_header=parent_header,
                        include_image_urls=include_image_urls,
                    )
                    lines.append(line)
                    if _chunk_kind(child) in _IMAGE_KINDS and include_images:
                        images.append(source)

            if parent_text:
                lines.append("父级上下文：")
                lines.append(parent_text)

            related_lines: list[str] = []
            related_sources: list[dict[str, Any]] = []
            matched_keys = {str(child.get("chunk_id")) for child in matched_children if isinstance(child, dict) and child.get("chunk_id")}
            # Prefer non-plain-text related evidence first. Plain same-parent text is noisy;
            # keep only a small amount after visual/table/equation evidence.
            candidate_related = [child for child in parent_children if isinstance(child, dict) and str(child.get("chunk_id")) not in matched_keys]
            candidate_related.sort(
                key=lambda child: (
                    0 if _chunk_kind(child) in _IMAGE_KINDS else 1 if _chunk_kind(child) in {"table", "equation"} else 2,
                    int(child.get("chunk_index") or 0),
                )
            )
            plain_text_related = 0
            for child in candidate_related:
                kind = _chunk_kind(child)
                if not _is_related_child_allowed(kind, include_images=include_images, include_tables=include_tables, include_equations=include_equations):
                    continue
                if kind not in _IMAGE_KINDS and kind not in {"table", "equation"}:
                    plain_text_related += 1
                    if plain_text_related > 2:
                        continue
                line, source = _format_child_evidence(
                    child,
                    source_id=source_id,
                    document_title=title,
                    file_name=file_name,
                    context_header=parent_header,
                    include_image_urls=include_image_urls,
                    max_chars=800,
                )
                related_lines.append(line)
                related_sources.append(source)
                if kind in _IMAGE_KINDS and include_images:
                    images.append(source)
                if len(related_lines) >= 6:
                    break
            if related_lines:
                lines.append("相关图表/表格/公式/邻近内容：")
                lines.extend(related_lines)

            block = "\n".join(lines).strip() + "\n\n"
            clipped, was_truncated = budget.add(block)
            if clipped:
                sections.append(clipped)
            truncated = truncated or was_truncated

            counts = _count_kinds(all_children)
            evidence_sources: list[dict[str, Any]] = []
            for child in matched_children[:10]:
                _, source = _format_child_evidence(
                    child,
                    source_id=source_id,
                    document_title=title,
                    file_name=file_name,
                    context_header=parent_header,
                    include_image_urls=include_image_urls,
                )
                evidence_sources.append(source)
            evidence_sources.extend(related_sources[:10])

            sources.append(
                {
                    "source_id": source_id,
                    "document_title": title,
                    "file_name": file_name,
                    "context_header": parent_header,
                    "parent_returned_chars": parent.get("returned_chars"),
                    "parent_total_chars": parent.get("content_length"),
                    "matched_child_count": len(matched_children),
                    "related_child_count": max(0, len(all_children) - len(matched_children)),
                    "image_count": counts["image"],
                    "table_count": counts["table"],
                    "equation_count": counts["equation"],
                    "evidence_count": len(evidence_sources),
                    "evidence": evidence_sources[:20],
                }
            )
    else:
        # Fallback for old retrieval responses without parent_groups.
        results = _iter_flat_results(retrieval_data)
        for item in results:
            if len(sources) >= max_sources or truncated:
                break
            if not isinstance(item, dict):
                continue
            parent = item.get("parent_context") or (item.get("context") or {}).get("parent") or {}
            if not isinstance(parent, dict):
                parent = {}
            source_id = f"S{len(sources) + 1}"
            title = _source_title(item, parent)
            file_name = _source_file(item, parent)
            parent_header = _context_header(item, parent)
            parent_text = _text_from(parent, "display_content", "display_preview", "content", "content_preview", max_chars=3000)
            line, source = _format_child_evidence(
                item,
                source_id=source_id,
                document_title=title,
                file_name=file_name,
                context_header=parent_header,
                include_image_urls=include_image_urls,
            )
            lines = [f"【资料 {source_id}】", f"文档：{title}"]
            if file_name and file_name != title:
                lines.append(f"文件：{file_name}")
            if parent_header:
                lines.append(f"章节/上下文：{parent_header}")
            lines.append("命中内容：")
            lines.append(line)
            if parent_text:
                lines.append("父级上下文：")
                lines.append(parent_text)

            block = "\n".join(lines).strip() + "\n\n"
            clipped, was_truncated = budget.add(block)
            if clipped:
                sections.append(clipped)
            truncated = truncated or was_truncated
            sources.append({"source_id": source_id, **source})
            if _chunk_kind(item) in _IMAGE_KINDS and include_images:
                images.append(source)

    footer = "回答要求：\n1. 优先引用资料中的事实。\n2. 不要编造资料外内容。\n3. 如果资料不足，请说明无法确定。\n"
    clipped, was_truncated = budget.add(footer)
    if clipped:
        sections.append(clipped)
    truncated = truncated or was_truncated

    context_text = "".join(sections).strip()
    return {
        "query": query,
        "context_text": context_text,
        "sources": sources,
        "images": images if include_images else [],
        "meta": {
            "context_chars": len(context_text),
            "max_context_chars": max_context_chars,
            "is_truncated": truncated,
            "source_count": len(sources),
            "image_count": len(images) if include_images else 0,
            "include_images": include_images,
            "include_image_urls": include_image_urls,
            "builder": "llm_context_builder.v2",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Cloud flat chunks builder (hotfix v4 — selective context, cloud aligned)
# ═══════════════════════════════════════════════════════════════════════════════

import uuid as _cloud_uuid

_CLOUD_IMAGE_KINDS = {"image", "chart", "graph", "plot", "figure", "fig"}
_CLOUD_TABLE_KINDS = {"table", "table_body"}
_CLOUD_EQUATION_KINDS = {"equation", "formula", "inline_equation", "interline_equation", "display_equation"}

_CLOUD_LOW_INFO_TITLE_RE = re.compile(
    r"^(引\s*言|参考\s*文\s*献|结\s*论|讨\s*论|材\s*料|方\s*法|结果与分析|总结与展望|结束语|致谢|附录|"
    r"Abstract|Introduction|References|Conclusion|Discussion|Materials|Methods|Results|Acknowledg(e)?ments?)\s*$",
    re.I,
)
_CLOUD_HEADING_ONLY_RE = re.compile(r"^\s*\d+(\.\d+)*\s*[一-龥A-Za-z][一-龥A-Za-z\s·、：:：\-—]{0,28}\s*$")
_CLOUD_HEADER_FOOTER_RE = re.compile(
    r"(高\s*等\s*学\s*校\s*化\s*学\s*学\s*报|CHEMICAL\s+JOURNAL\s+OF\s+CHINESE\s+UNIVERSITIES|"
    r"Chem\.\s*J\.\s*Chinese\s*Universities|超星\s*[·•]|No\.\s*\d+|\d{4,}\(\d+/\d+\)|Ed\.\s*[:：])",
    re.I,
)
_CLOUD_BIB_META_RE = re.compile(r"(中图分类号|文献标志码|文献标识码|doi\s*[:：]|DOI\s*[:：])", re.I)
_CLOUD_FORWARD_REF_RE = re.compile(
    r"(上述|如下|前述|该方法|该结果|这些结果|这种机制|该体系|本文方法|该模型|如图所示|如图|见图|见表|如下表|如下所示|详见表|详见图|"
    r"the above|as shown|this method|these results|the proposed method|as shown in\s+Fig\.?|as listed in\s+Table|shown in|illustrated in|depicted in|presented in)",
    re.I,
)
_CLOUD_FIG_TABLE_RE = re.compile(r"(图\s*\d+|表\s*\d+|Fig\.?\s*\d+|Figure\s*\d+|Table\s*\d+)", re.I)


def _cloud_meta(item: dict | None) -> dict:
    if not isinstance(item, dict):
        return {}
    for key in ("metadata", "metadata_json", "chunk_metadata"):
        val = item.get(key)
        if isinstance(val, dict):
            return val
    return {}


def _cloud_image_info(item: dict | None) -> dict:
    if not isinstance(item, dict):
        return {}
    val = item.get("image_info")
    if isinstance(val, dict):
        return val
    meta = _cloud_meta(item)
    val = meta.get("image_info")
    return val if isinstance(val, dict) else {}


def _cloud_kind(item: dict | None) -> str:
    try:
        kind = _chunk_kind(item)
    except Exception:
        kind = "text"
    kind = str(kind or "text").lower()
    if kind in _CLOUD_IMAGE_KINDS:
        return "image"
    if kind in _CLOUD_TABLE_KINDS:
        return "table"
    if kind in _CLOUD_EQUATION_KINDS:
        return "equation"
    if kind == "image_ocr_text":
        return "text"
    return kind or "text"


def _cloud_chunk_id(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    meta = _cloud_meta(item)
    for obj in (item, meta):
        for key in ("chunk_id", "id", "block_id", "node_id"):
            val = obj.get(key) if isinstance(obj, dict) else None
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _cloud_parent_id(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    meta = _cloud_meta(item)
    for obj in (item, meta):
        for key in ("parent_chunk_id", "parent_id", "parent_block_id"):
            val = obj.get(key) if isinstance(obj, dict) else None
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _cloud_document_id(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    meta = _cloud_meta(item)
    for obj in (item, meta):
        for key in ("document_id", "doc_id", "knowledge_document_id", "file_id", "source_document_id"):
            val = obj.get(key) if isinstance(obj, dict) else None
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _cloud_first_str(item: dict | None, keys: tuple[str, ...]) -> str:
    if not isinstance(item, dict):
        return ""
    meta = _cloud_meta(item)
    image_info = _cloud_image_info(item)
    for obj in (item, meta, image_info):
        if not isinstance(obj, dict):
            continue
        for key in keys:
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return _norm_space(val)
            if isinstance(val, (list, tuple)) and val:
                joined = " > ".join(str(x).strip() for x in val if str(x).strip())
                if joined:
                    return _norm_space(joined)
    return ""


def _cloud_page_no(item: dict | None) -> int | None:
    if not isinstance(item, dict):
        return None
    meta = _cloud_meta(item)
    image_info = _cloud_image_info(item)
    for obj in (item, meta, image_info):
        if not isinstance(obj, dict):
            continue
        for key in ("page_no", "source_page", "page"):
            val = obj.get(key)
            try:
                if val is not None and val != "":
                    parsed = int(val)
                    return parsed if parsed > 0 else None
            except Exception:
                pass
        for key in ("page_idx", "page_index"):
            val = obj.get(key)
            try:
                if val is not None and val != "":
                    parsed = int(val)
                    return parsed + 1 if parsed >= 0 else None
            except Exception:
                pass
    return None


def _cloud_page_range(item: dict | None) -> str:
    p = _cloud_page_no(item)
    return str(p) if p else ""


def _cloud_clean_content(text: Any, *, max_chars: int | None = None) -> str:
    text = _clean_llm_text(text or "")
    # Remove common wrapper labels and URLs that should not be embedded in content.
    text = re.sub(r"(?m)^\s*(文档标题|上下文|正文|前文|后文|绑定标题|图片URL|图片地址|文件名)\s*[:：].*$", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"/files/\S+", "", text)
    text = re.sub(r"kb/[0-9a-fA-F-]+/documents/[0-9a-fA-F-]+/media/\S+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _cloud_content(item: dict | None, *, max_chars: int | None = None) -> str:
    if not isinstance(item, dict):
        return ""
    kind = _cloud_kind(item)
    meta = _cloud_meta(item)
    image_info = _cloud_image_info(item)
    candidates: list[Any] = []
    # Prefer clean core content first.
    for key in ("content", "matched_content", "content_preview", "display_content", "display_preview"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            candidates.append(val)
    if kind == "image":
        for obj in (meta, image_info):
            for key in ("vlm_description", "description", "caption", "figure_caption", "image_caption", "ocr_cleaned_text", "ocr_text"):
                val = obj.get(key) if isinstance(obj, dict) else None
                if isinstance(val, str) and val.strip():
                    candidates.append(val)
    elif kind == "table":
        for obj in (meta, image_info):
            for key in ("table_markdown", "table_body", "table_html", "caption", "table_caption", "description"):
                val = obj.get(key) if isinstance(obj, dict) else None
                if isinstance(val, str) and val.strip():
                    candidates.append(val)
    elif kind == "equation":
        for obj in (meta, image_info):
            for key in ("latex", "equation", "description", "caption"):
                val = obj.get(key) if isinstance(obj, dict) else None
                if isinstance(val, str) and val.strip():
                    candidates.append(val)
    for cand in candidates:
        cleaned = _cloud_clean_content(cand, max_chars=max_chars)
        if cleaned:
            return cleaned
    return ""


def _cloud_image_url(item: dict | None, include_image_urls: bool = True) -> str:
    """Return public image URL for cloud flat chunks."""
    if not include_image_urls or not isinstance(item, dict):
        return ""

    candidates: list[str] = []

    def collect(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        for key in ("public_url", "image_url", "url", "src", "file_url", "object_url"):
            raw = obj.get(key)
            if isinstance(raw, str) and raw.strip():
                candidates.append(raw.strip())
        for key in ("path", "file_path", "local_path", "media_path"):
            raw = obj.get(key)
            if isinstance(raw, str) and raw.strip():
                candidates.append(raw.strip())

    collect(item)
    meta = _cloud_meta(item)
    image_info = _cloud_image_info(item)
    collect(meta)
    collect(image_info)

    nested = meta.get("image_info") if isinstance(meta, dict) else None
    collect(nested)

    try:
        legacy_url = _image_url(item, include_image_urls=True)
        if legacy_url:
            candidates.insert(0, legacy_url)
    except Exception:
        pass

    for candidate in candidates:
        public_url = _to_public_url(candidate)
        if public_url:
            return public_url

    return ""
def _cloud_is_low_information_chunk(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return True
    meta = _cloud_meta(item)
    if meta.get("skip_embedding_recommended") or meta.get("low_content_chunk"):
        return True
    content = _cloud_content(item, max_chars=1000)
    if not content:
        return True
    compact = re.sub(r"\s+", "", content)
    # Obvious title / header / footer / bibliography fragments.
    if _CLOUD_LOW_INFO_TITLE_RE.match(content.strip()):
        return True
    if _CLOUD_HEADING_ONLY_RE.match(content.strip()) and len(compact) < 30:
        return True
    if _CLOUD_HEADER_FOOTER_RE.search(content):
        return True
    # Bibliographic metadata fragments are low quality when they are short.
    if _CLOUD_BIB_META_RE.search(content) and len(content) < 220:
        return True
    # Keyword-only / category-only fragments.
    if ("关键词" in content or "关键词：" in content) and len(content) < 180 and ("；" in content or ";" in content):
        return True
    # Very short text without sentence punctuation is usually not valid evidence.
    if len(content) < 120 and not re.search(r"[。！？；，,.!?;]", content):
        return True
    # Pure title-like line: one short line, no punctuation, no verbs/facts.
    if len(content) < 80 and "\n" not in content and not re.search(r"[。！？；，,.!?;：:]", content):
        return True
    return False


def _cloud_query_tokens(query_text: str) -> set[str]:
    tokens: set[str] = set()
    q = str(query_text or "").strip()
    if not q:
        return tokens
    for run in re.findall(r"[一-鿿]+", q):
        if len(run) <= 4:
            if len(run) >= 2:
                tokens.add(run)
        else:
            for i in range(0, len(run) - 1):
                seg = run[i : i + 2]
                if len(seg) >= 2:
                    tokens.add(seg)
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", q.lower()):
        tokens.add(word)
    return {t for t in tokens if len(t) >= 2}


def _cloud_score(item: dict | None) -> float | None:
    if not isinstance(item, dict):
        return None
    for key in ("score", "fusion_score", "rrf_score"):
        val = item.get(key)
        try:
            if val is not None:
                return float(val)
        except Exception:
            pass
    return None


def _cloud_context_header(item: dict | None) -> str:
    try:
        return _context_header(item) or ""
    except Exception:
        return _cloud_first_str(item, ("context_header", "heading_path", "section_title", "heading"))


def _cloud_flat_chunk(
    item: dict,
    *,
    rank: int,
    citation_label: str,
    citation_id: str,
    role: str,
    include_image_urls: bool,
    source_hit_chunk_ids: list[str] | None = None,
) -> dict:
    kind = _cloud_kind(item)
    content = _cloud_content(item, max_chars=5000)
    score = _cloud_score(item)
    meta = _cloud_meta(item)
    image_info = _cloud_image_info(item)
    flat: dict[str, Any] = {
        "rank": rank,
        "citation_label": citation_label,
        "citation_id": citation_id,
        "role": role,
        "chunk_id": _cloud_chunk_id(item),
        "parent_chunk_id": _cloud_parent_id(item) or None,
        "document_id": _cloud_document_id(item),
        "document_url": _frontend_document_url(_cloud_document_id(item)),
        "document_title": _cloud_first_str(item, ("document_title", "doc_title", "title", "knowledge_title")),
        "file_name": _cloud_first_str(item, ("file_name", "filename", "source_file", "knowledge_filename")),
        "page_no": _cloud_page_no(item),
        "page_range": _cloud_page_range(item),
        "chunk_type": kind,
        "semantic_type": kind,
        "context_header": _cloud_context_header(item),
        "content": content,
        "score": score if role == "hit" else None,
    }
    if role == "hit":
        flat["score_detail"] = {
            "vector_score": item.get("vector_score"),
            "keyword_score": item.get("keyword_score"),
            "fusion_score": score,
        }
    if source_hit_chunk_ids:
        flat["source_hit_chunk_ids"] = [x for x in source_hit_chunk_ids if x]
    if kind == "image":
        image_url = _cloud_image_url(item, include_image_urls=include_image_urls)
        if image_url:
            flat["image_url"] = image_url
    elif kind == "table":
        table_html = _cloud_first_str(item, ("table_html",)) or image_info.get("table_html") or meta.get("table_html") or ""
        table_markdown = _cloud_first_str(item, ("table_markdown",)) or meta.get("table_markdown") or ""
        if table_html:
            flat["table_html"] = table_html
        if table_markdown:
            flat["table_markdown"] = table_markdown
    elif kind == "equation":
        latex = _cloud_first_str(item, ("latex", "equation")) or meta.get("latex") or meta.get("equation") or ""
        if latex:
            flat["latex"] = latex
    return flat


def _cloud_needs_expansion(
    *,
    hit: dict,
    query_text: str,
    sibling_chunks: list[dict],
    same_parent_hits: list[dict],
) -> dict:
    parent_id = _cloud_parent_id(hit)
    if not parent_id:
        return {"expanded": False, "reason": "no_parent_chunk", "rules": []}
    if _cloud_is_low_information_chunk(hit):
        return {"expanded": False, "reason": "low_information_hit", "rules": []}
    content = _cloud_content(hit)
    rules: list[str] = []
    if len(content) < 200:
        rules.append("short_but_valid")
    if _CLOUD_FORWARD_REF_RE.search(content):
        rules.append("forward_reference")
    if _CLOUD_FIG_TABLE_RE.search(content):
        rules.append("figure_or_table_reference")
    # Nearby multi hit only when indexes are close in the same parent.
    indexes = []
    for h in same_parent_hits:
        try:
            if _cloud_parent_id(h) == parent_id and h.get("chunk_index") is not None:
                indexes.append(int(h.get("chunk_index")))
        except Exception:
            pass
    if len(indexes) >= 2 and max(indexes) - min(indexes) <= 2:
        rules.append("nearby_multi_hits")
    # Neighbour query overlap is weak and only used when no stronger rule exists.
    if not rules:
        tokens = _cloud_query_tokens(query_text)
        if tokens:
            for sib in sibling_chunks:
                if _cloud_is_low_information_chunk(sib):
                    continue
                if _cloud_kind(sib) != "text":
                    continue
                txt = _cloud_content(sib).lower()
                hits = sum(1 for t in tokens if t.lower() in txt)
                if hits >= 2:
                    rules.append("neighbor_query_overlap")
                    break
    if not rules:
        return {"expanded": False, "reason": "hit_chunk_is_self_contained", "rules": []}
    return {"expanded": True, "reason": "rules_triggered: " + ",".join(rules), "rules": rules}


def _cloud_parent_group_for(parent_groups: list[dict], parent_id: str) -> dict | None:
    if not parent_id:
        return None
    for g in parent_groups:
        if not isinstance(g, dict):
            continue
        if str(g.get("parent_chunk_id") or g.get("parent_key") or "") == parent_id:
            return g
    return None


def _cloud_build_window_for_parent(
    *,
    parent_group: dict,
    query_text: str,
    hit_chunks: list[dict],
    max_chars: int,
    soft_overflow_ratio: float,
    max_extra_text_chunks: int,
    include_media_chunks: bool,
    media_chunk_limit: int,
) -> dict:
    parent_id = str(parent_group.get("parent_chunk_id") or parent_group.get("parent_key") or "")
    soft_max = int(max_chars * soft_overflow_ratio)
    hit_ids = [_cloud_chunk_id(h) for h in hit_chunks if _cloud_chunk_id(h)]
    hit_id_set = set(hit_ids)
    all_siblings = _dedup_children((parent_group.get("parent_children") or []) + (parent_group.get("matched_children") or []) + hit_chunks)
    siblings_by_id = {_cloud_chunk_id(c): c for c in all_siblings if _cloud_chunk_id(c)}
    for h in hit_chunks:
        if _cloud_chunk_id(h):
            siblings_by_id[_cloud_chunk_id(h)] = h
    # Determine whether any hit needs expansion.
    policies = [
        _cloud_needs_expansion(
            hit=h,
            query_text=query_text,
            sibling_chunks=[c for c in all_siblings if _cloud_chunk_id(c) not in hit_id_set],
            same_parent_hits=hit_chunks,
        )
        for h in hit_chunks
    ]
    rules = sorted({r for p in policies for r in p.get("rules", [])})
    if not any(p.get("expanded") for p in policies):
        return {
            "mode": "selective_child_aligned",
            "parent_chunk_id": parent_id,
            "max_chars": max_chars,
            "soft_max_chars": soft_max,
            "actual_chars": sum(len(_cloud_content(h)) for h in hit_chunks),
            "hit_chunk_ids": hit_ids,
            "included_chunk_ids": hit_ids,
            "before_chunk_ids": [],
            "between_chunk_ids": [],
            "after_chunk_ids": [],
            "media_chunk_ids": [],
            "expanded": False,
            "reason": "hit_chunk_is_self_contained",
            "rules": [],
        }
    # Order siblings by chunk_index.
    ordered = sorted(siblings_by_id.values(), key=lambda c: (c.get("chunk_index") is None, c.get("chunk_index") or 10**9))
    index_by_id: dict[str, int] = {}
    for pos, c in enumerate(ordered):
        cid = _cloud_chunk_id(c)
        if cid:
            index_by_id[cid] = pos
    hit_positions = [index_by_id[cid] for cid in hit_ids if cid in index_by_id]
    min_pos = min(hit_positions) if hit_positions else None
    max_pos = max(hit_positions) if hit_positions else None
    included = set(hit_ids)
    before: list[str] = []
    between: list[str] = []
    after: list[str] = []
    media: list[str] = []
    actual_chars = sum(len(_cloud_content(h)) for h in hit_chunks)
    extra_text_count = 0

    def can_add(c: dict, *, media_ok: bool = True) -> bool:
        nonlocal actual_chars, extra_text_count
        cid = _cloud_chunk_id(c)
        if not cid or cid in included or cid in hit_id_set:
            return False
        kind = _cloud_kind(c)
        if kind in ("image", "table", "equation"):
            return include_media_chunks and media_ok and len(media) < media_chunk_limit and not _cloud_is_low_information_chunk(c)
        if kind != "text":
            return False
        if _cloud_is_low_information_chunk(c):
            return False
        if extra_text_count >= max_extra_text_chunks:
            return False
        return actual_chars + len(_cloud_content(c)) <= soft_max

    def add_text(c: dict, target: list[str]) -> None:
        nonlocal actual_chars, extra_text_count
        cid = _cloud_chunk_id(c)
        included.add(cid)
        target.append(cid)
        actual_chars += len(_cloud_content(c))
        extra_text_count += 1

    def add_media(c: dict) -> None:
        nonlocal actual_chars
        cid = _cloud_chunk_id(c)
        included.add(cid)
        media.append(cid)
        actual_chars += len(_cloud_content(c))

    # 1) figure/table reference: add nearby media from same parent.
    if "figure_or_table_reference" in rules:
        for c in ordered:
            if _cloud_kind(c) in ("image", "table") and can_add(c):
                add_media(c)
    # 2) nearby multi-hit: add non-hit chunks between min and max hit positions.
    if "nearby_multi_hits" in rules and min_pos is not None and max_pos is not None and max_pos > min_pos:
        for pos in range(min_pos + 1, max_pos):
            c = ordered[pos]
            if _cloud_kind(c) == "text" and can_add(c):
                add_text(c, between)
    # 3) forward reference: prefer nearest valid before.
    if "forward_reference" in rules and min_pos is not None:
        for pos in range(min_pos - 1, -1, -1):
            c = ordered[pos]
            if _cloud_kind(c) == "text" and can_add(c):
                add_text(c, before)
                break
    # 4) short / overlap: add one best neighbour if there is still no text context.
    if ("short_but_valid" in rules or "neighbor_query_overlap" in rules) and extra_text_count < max_extra_text_chunks and min_pos is not None:
        tokens = _cloud_query_tokens(query_text)
        candidates: list[tuple[int, int, str, dict]] = []
        for pos in range(max(0, min_pos - 3), min(len(ordered), (max_pos if max_pos is not None else min_pos) + 4)):
            c = ordered[pos]
            cid = _cloud_chunk_id(c)
            if not cid or cid in included or cid in hit_id_set or _cloud_kind(c) != "text" or _cloud_is_low_information_chunk(c):
                continue
            txt = _cloud_content(c).lower()
            overlap = sum(1 for t in tokens if t.lower() in txt) if tokens else 0
            dist = 0 if min_pos is None else min(abs(pos - min_pos), abs(pos - (max_pos if max_pos is not None else min_pos)))
            side = "before" if min_pos is not None and pos < min_pos else "after"
            candidates.append((overlap, -dist, side, c))
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        for overlap, _, side, c in candidates:
            if "neighbor_query_overlap" in rules and overlap <= 0:
                continue
            if can_add(c):
                add_text(c, before if side == "before" else after)
                break
    expanded = bool(before or between or after or media)
    return {
        "mode": "selective_child_aligned",
        "parent_chunk_id": parent_id,
        "max_chars": max_chars,
        "soft_max_chars": soft_max,
        "actual_chars": actual_chars,
        "hit_chunk_ids": hit_ids,
        "included_chunk_ids": list(included),
        "before_chunk_ids": before,
        "between_chunk_ids": between,
        "after_chunk_ids": after,
        "media_chunk_ids": media,
        "expanded": expanded,
        "reason": "rules_triggered: " + ",".join(rules) if expanded else "no_valid_context_chunks",
        "rules": rules if expanded else [],
    }


def _cloud_collect_hits(retrieval_data: dict, match_count: int) -> list[dict]:
    results = retrieval_data.get("results") or []
    hits: list[dict] = []
    seen: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        cid = _cloud_chunk_id(item)
        if not cid or cid in seen:
            continue
        if _cloud_is_low_information_chunk(item):
            continue
        seen.add(cid)
        hits.append(item)
        if len(hits) >= match_count:
            break
    if hits:
        return hits
    # Fallback: use matched_children from parent_groups.
    for group in retrieval_data.get("parent_groups") or []:
        for item in group.get("matched_children") or []:
            if not isinstance(item, dict):
                continue
            cid = _cloud_chunk_id(item)
            if not cid or cid in seen or _cloud_is_low_information_chunk(item):
                continue
            seen.add(cid)
            hits.append(item)
            if len(hits) >= match_count:
                return hits
    return hits


def build_cloud_flat_chunks_payload(retrieval_data: dict, options: dict | None = None) -> dict[str, Any]:
    """Build cloud-native flat chunk results for the MCP/cloud Agent.

    This function intentionally does not build a prompt.  It returns flat,
    referenceable chunks.  Selective context chunks are included only when
    rule-based checks find a valid sibling context in the same parent.
    """
    options = options or {}
    return_mode = str(options.get("return_mode") or "chunks_with_selective_context").strip().lower()
    if return_mode not in {"chunks", "chunks_with_selective_context", "debug", "legacy"}:
        return_mode = "chunks_with_selective_context"
    query_text = str(options.get("query_text") or options.get("query") or retrieval_data.get("query_text") or retrieval_data.get("query") or "").strip()
    retrieval_id = str(options.get("retrieval_id") or retrieval_data.get("retrieval_id") or f"ret_{_cloud_uuid.uuid4().hex[:10]}")
    match_count = _as_int(options.get("match_count") or retrieval_data.get("match_count") or 10, 10, minimum=1, maximum=100)
    include_context_window = _as_bool(options.get("include_context_window"), True)
    include_context_text = _as_bool(options.get("include_context_text"), False)
    include_image_urls = _as_bool(options.get("include_image_urls"), False)
    include_media_chunks = _as_bool(options.get("include_media_chunks"), True)
    parent_window_max_chars = _as_int(options.get("parent_window_max_chars") or options.get("parent_context_max_chars"), 1200, minimum=200, maximum=20000)
    soft_ratio = float(options.get("parent_window_soft_overflow_ratio") or 1.1)
    max_extra_text = _as_int(options.get("max_extra_text_chunks_per_parent"), 2, minimum=0, maximum=10)
    media_limit = _as_int(options.get("media_chunk_limit"), 2, minimum=0, maximum=20)

    parent_groups = retrieval_data.get("parent_groups") or []
    hits = _cloud_collect_hits(retrieval_data, match_count)
    # Group hits by parent.
    hits_by_parent: dict[str, list[dict]] = {}
    for h in hits:
        hits_by_parent.setdefault(_cloud_parent_id(h), []).append(h)

    windows_by_parent: dict[str, dict] = {}
    if return_mode in {"chunks_with_selective_context", "debug", "legacy"} and include_context_window:
        for parent_id, parent_hits in hits_by_parent.items():
            if not parent_id:
                continue
            pg = _cloud_parent_group_for(parent_groups, parent_id)
            if not pg:
                continue
            cw = _cloud_build_window_for_parent(
                parent_group=pg,
                query_text=query_text,
                hit_chunks=parent_hits,
                max_chars=parent_window_max_chars,
                soft_overflow_ratio=soft_ratio,
                max_extra_text_chunks=max_extra_text,
                include_media_chunks=include_media_chunks,
                media_chunk_limit=media_limit,
            )
            windows_by_parent[parent_id] = cw

    output_chunks: list[dict] = []
    emitted: set[str] = set()
    next_rank = 1

    def emit(item: dict, role: str, source_hit_ids: list[str] | None = None, context_window: dict | None = None) -> None:
        nonlocal next_rank
        cid = _cloud_chunk_id(item)
        if not cid or cid in emitted:
            return
        emitted.add(cid)
        citation_label = f"[{next_rank}]"
        citation_id = f"{retrieval_id}:{next_rank}"
        flat = _cloud_flat_chunk(
            item,
            rank=next_rank,
            citation_label=citation_label,
            citation_id=citation_id,
            role=role,
            include_image_urls=include_image_urls,
            source_hit_chunk_ids=source_hit_ids,
        )
        if role == "hit":
            parent_id = _cloud_parent_id(item)
            cw = context_window or windows_by_parent.get(parent_id)
            if not parent_id:
                flat["context_policy"] = {"expanded": False, "reason": "no_parent_chunk", "rules": [], "strategy": "rule_based"}
            elif cw and cw.get("expanded"):
                flat["context_policy"] = {"expanded": True, "reason": cw.get("reason") or "rules_triggered", "rules": cw.get("rules") or [], "strategy": "rule_based"}
                flat["context_window"] = cw
            else:
                reason = (cw or {}).get("reason") if cw else "hit_chunk_is_self_contained"
                flat["context_policy"] = {"expanded": False, "reason": reason or "hit_chunk_is_self_contained", "rules": [], "strategy": "rule_based"}
        output_chunks.append(flat)
        next_rank += 1

    # Emit hits first.
    for hit in hits:
        emit(hit, "hit", context_window=windows_by_parent.get(_cloud_parent_id(hit)))

    # Emit valid context chunks referenced by expanded windows.
    if return_mode in {"chunks_with_selective_context", "debug", "legacy"}:
        sibling_lookup: dict[str, dict] = {}
        for group in parent_groups:
            for item in (group.get("parent_children") or []) + (group.get("matched_children") or []):
                cid = _cloud_chunk_id(item)
                if cid:
                    sibling_lookup[cid] = item
        for cw in windows_by_parent.values():
            if not cw.get("expanded"):
                continue
            hit_ids = cw.get("hit_chunk_ids") or []
            for field, role in (
                ("before_chunk_ids", "context_before"),
                ("between_chunk_ids", "context_between"),
                ("after_chunk_ids", "context_after"),
                ("media_chunk_ids", "context_media"),
            ):
                for cid in cw.get(field) or []:
                    item = sibling_lookup.get(cid)
                    if item and not _cloud_is_low_information_chunk(item):
                        emit(item, role, source_hit_ids=hit_ids)

    hit_count = sum(1 for c in output_chunks if c.get("role") == "hit")
    context_count = len(output_chunks) - hit_count
    meta = {
        "schema": "cloud_flat_chunks.v4.selective_context",
        "top_k": match_count,
        "returned_count": len(output_chunks),
        "hit_count": hit_count,
        "context_chunk_count": context_count,
        "return_mode": return_mode,
        "context_expansion_strategy": "rule_based",
        "include_context_text": include_context_text,
        "context_text_included": include_context_text,
    }
    payload: dict[str, Any] = {
        "success": True,
        "query_text": query_text,
        "retrieval_id": retrieval_id,
        "return_mode": return_mode,
        "chunks": output_chunks,
        "meta": meta,
    }
    if include_context_text:
        payload["context_text"] = "\n\n".join(c.get("content") or "" for c in output_chunks if c.get("content"))
    else:
        payload["context_text"] = ""
    if return_mode in {"debug", "legacy"}:
        payload["parent_groups"] = parent_groups
        payload["results"] = retrieval_data.get("results") or []
        # Keep old fields empty unless the old builder is explicitly called elsewhere.
        payload.setdefault("sources", [])
        payload.setdefault("images", [])
        payload.setdefault("tables", [])
        payload.setdefault("equations", [])
    return payload
