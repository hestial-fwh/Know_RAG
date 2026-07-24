import hashlib
import logging
import math
import re
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import error_response, success_response
from app.db.models import (
    Chunk,
    ChunkEmbedding,
    Document,
    KnowledgeBase,
    User,
)
from app.services.audit_service import record_audit
from app.services.chunker import split_document
from app.services.chunker.boundary_utils import is_leading_punctuation, strip_invalid_leading_punctuation  # legacy — rarely needed after sentence-unit refactor
from app.services.chunker.markdown_splitter import is_orphan_short_text_chunk
from app.services.chunker.protected_patterns import is_structure_like_text, looks_like_directory_tree
from app.services.chunker.recursive_splitter import split_structure_by_lines
from app.services.chunker.heading_utils import (
    heading_level_from_metadata,
    is_caption_text,
    is_page_noise_text,
    looks_like_section_heading,
    make_continuation_header,
    normalize_context_header,
    strip_continuation_suffix,
)
from app.services.chunker.models import ChunkerConfig
from app.services.chunker.parent_child_splitter import split_parent_child_result
from app.services.chunker.canonical_parent_child import build_canonical_parent_child_plan
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.document_relation.materializer import (
    materialize_figure_bindings,
    materialize_relation_summaries,
)
from app.services.document_relation.asset_group_resolver import (
    build_member_edges,
    resolve_asset_groups,
)
from app.services.document_relation.page_layout_graph import build_page_layout_graph
from app.services.document_relation.relation_graph_merger import (
    build_relation_graph,
    merge_group_relations,
)
from app.services.document_relation.shared_context_resolver import (
    resolve_shared_context_edges,
)
from app.services.document_relation_persistence_service import replace_canonical_artifact
from app.services.chunker_config_service import runtime_config_from_db
from app.services.content_cleaning import (
    MIN_CHILD_TEXT_CHARS,
    MIN_PARENT_TEXT_CHARS,
    clean_chunk_content_for_storage,
    clean_text_for_embedding,
    clear_embedding_skip_metadata,
    clear_stale_embedding_skip_flags,
    is_low_information_text,
    is_url_or_path_only_text,
    normalize_chunk_text,
    should_skip_text_chunk,
)
from app.services.ocr import layered_ocr_metadata, normalize_identifiers, repair_text
from app.services.ocr.templates import get_template
from app.utils.ids import new_id
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres

# ── chunking subpackage imports (real logic migrated from this file) ──
from app.services.chunking.section_builder import (
    block_context_title,
    block_page_no as _block_page_no_new,
    block_parent_text as _block_parent_text_new,
    block_text_for_context,
    heading_level as _heading_level_new,
    heading_path_text,
    is_equation_block as _is_equation_block_new,
    is_heading_block as _is_heading_block_new,
    is_noise_block as _is_noise_block_new,
    make_structured_sections as _make_structured_sections_new,
    normalized_child_type as _normalized_child_type_new,
    parent_context_header as _parent_context_header_new,
    raw_block_type as _raw_block_type_new,
    section_content_len as _section_content_len_new,
)
from app.services.chunking.code_context_merger import (
    CODE_BLOCK_RAW_TYPES as _CODE_BLOCK_RAW_TYPES_IMPORTED,
    CODE_LEAD_IN_SUFFIXES as _CODE_LEAD_IN_SUFFIXES_IMPORTED,
    CODE_TAIL_NOTE_RE as _CODE_TAIL_NOTE_RE_IMPORTED,
    compose_code_block_content,
    is_code_block_block as _is_code_block_block_new,
    is_fenced_code_text as _is_fenced_code_text_new,
    is_orphan_short_text as _is_orphan_short_text_new,
    looks_like_code_lead_in as _looks_like_code_lead_in_new,
    looks_like_code_tail_note as _looks_like_code_tail_note_new,
    merge_short_text_around_code_blocks as _merge_short_text_around_code_blocks_new,
)
from app.services.chunking.parent_builder import (
    build_parent_content as _build_parent_content_new,
)

logger = logging.getLogger(__name__)


def _normalize_file_url(value: str | None) -> tuple[str, str]:
    """Return (relative_path, url) for files served by /files.

    Some parser stages store either ``a/b.png`` or ``/files/a/b.png``.
    Normalize both forms so the frontend never receives /files//files/...
    """
    raw = (value or "").strip()
    if not raw:
        return "", ""
    if raw.startswith(("http://", "https://", "data:", "blob:")):
        return "", raw
    if raw.startswith("/files//files/"):
        raw = raw.replace("/files//files/", "/files/", 1)
    if raw.startswith("/files/"):
        relative = raw[len("/files/"):].lstrip("/")
        return relative, f"/files/{relative}"
    if raw.startswith("files/"):
        relative = raw[len("files/"):].lstrip("/")
        return relative, f"/files/{relative}"
    relative = raw.lstrip("/")
    return relative, f"/files/{relative}"


def _normalize_image_info(info: dict | None) -> dict:
    """Normalize image/table media metadata for API responses and persistence."""
    if not isinstance(info, dict):
        return {}
    result = dict(info)
    nested = result.get("image_info") if isinstance(result.get("image_info"), dict) else {}

    candidate = ""
    for key in ("url", "image_url", "relative_path", "path", "image_path"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
            break
    if not candidate:
        for key in ("url", "image_url", "relative_path", "path", "image_path"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                candidate = value.strip()
                break

    # Absolute local paths should not be exposed as URLs; prefer nested relative/url if present.
    if candidate.startswith("/data/files/"):
        nested_url = nested.get("url") if isinstance(nested.get("url"), str) else ""
        nested_relative = nested.get("relative_path") if isinstance(nested.get("relative_path"), str) else ""
        candidate = nested_url or nested_relative or candidate.replace("/data/files/", "", 1)

    if candidate:
        relative, url = _normalize_file_url(candidate)
        if relative:
            result["relative_path"] = relative
        result["url"] = url
        if isinstance(nested, dict) and nested:
            nested = dict(nested)
            nested.setdefault("relative_path", relative)
            nested.setdefault("url", url)
            result["image_info"] = nested

    image_base64 = result.get("image_base64") or nested.get("image_base64")
    if isinstance(image_base64, str) and image_base64.strip():
        result.setdefault("base64", image_base64.strip())
    return result


def _metadata_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


def _embedding_skip_info(chunk: Chunk) -> tuple[bool, str]:
    metadata = chunk.metadata_json or {}
    flags = chunk.flags or {}
    skip = _metadata_truthy(flags.get("skip_embedding_recommended")) or _metadata_truthy(metadata.get("skip_embedding_recommended"))
    reason = (flags.get("skip_reason") or flags.get("embedding_skip_reason") or metadata.get("skip_reason") or metadata.get("embedding_skip_reason") or "")
    return bool(skip), sanitize_text_for_postgres(str(reason or ""))


def chunk_to_dict(chunk: Chunk, include_content: bool = True) -> dict:
    metadata = chunk.metadata_json or {}
    embedding = getattr(chunk, "embedding", None)
    vector_preview = _vector_values(getattr(embedding, "embedding", None), limit=16) if embedding else []
    full_vector = _vector_values(getattr(embedding, "embedding", None), limit=None) if (embedding and include_content) else []
    skip_embedding, skip_reason = _embedding_skip_info(chunk)
    embedding_index_info = {
        "is_indexed": embedding is not None,
        "model_name": embedding.model_name if embedding else "",
        "dimension": embedding.dimension if embedding else None,
        "source_type": embedding.source_type if embedding else "",
        "is_enabled": embedding.is_enabled if embedding else False,
        "skip_embedding_recommended": skip_embedding,
        "skip_reason": skip_reason,
        "status": "indexed" if embedding is not None else ("skipped" if skip_embedding else "pending"),
        "indexed_content_preview": (embedding.content or "")[:600] if embedding else "",
        "vector_preview": vector_preview,
        "vector_norm": _vector_norm(full_vector or vector_preview),
        "vector_size": len(full_vector) if full_vector else (embedding.dimension if embedding else 0),
    }
    data = {
        "id": chunk.id,
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "knowledge_id": chunk.knowledge_id or chunk.document_id,
        "knowledge_base_id": chunk.knowledge_base_id,
        "chunk_index": chunk.chunk_index,
        "seq": chunk.chunk_index,
        "token_count": chunk.token_count,
        "is_enabled": chunk.is_enabled,
        "status": chunk.status,
        "start_at": chunk.start_at,
        "end_at": chunk.end_at,
        "pre_chunk_id": chunk.pre_chunk_id,
        "next_chunk_id": chunk.next_chunk_id,
        "chunk_type": chunk.chunk_type or "text",
        "parent_chunk_id": chunk.parent_chunk_id,
        "metadata": metadata,
        "metadata_json": metadata,
        "image_info": _normalize_image_info(chunk.image_info or {}),
        "relation_chunks": chunk.relation_chunks or [],
        "indirect_relation_chunks": chunk.indirect_relation_chunks or [],
        "context_header": chunk.context_header or "",
        "content_hash": chunk.content_hash or "",
        "flags": chunk.flags or {},
        "embedding_index_info": embedding_index_info,
        "skip_embedding_recommended": skip_embedding,
        "skip_reason": skip_reason,
        "embedding_status": embedding_index_info["status"],
        "preview": chunk.content[:240],
        "content_preview": chunk.content[:240],
        "created_at": chunk.created_at,
        "updated_at": chunk.updated_at,
    }
    if include_content:
        data["content"] = chunk.content
    else:
        data["content_preview"] = chunk.content[:180]
    return data


def split_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    return [item["content"] for item in split_text_with_offsets(text, chunk_size, overlap)]


def split_text_with_offsets(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[dict]:
    text = sanitize_text_for_postgres(text)
    size = max(chunk_size or settings.default_chunk_size or 800, 1)
    overlap_size = max(overlap if overlap is not None else settings.default_chunk_overlap, 0)
    if overlap_size >= size:
        overlap_size = max(size // 5, 0)

    chunks: list[dict] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + size, text_length)
        content = sanitize_text_for_postgres(text[start:end])
        if content:
            chunks.append({"content": content, "start_at": start, "end_at": end})
        if end >= text_length:
            break
        start = max(end - overlap_size, start + 1)
    return chunks


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _plain_text_from_html(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<\s*/\s*(tr|p|div|li|table|caption|h[1-6])\s*>", "\n", value, flags=re.I)
    text = re.sub(r"<\s*/?\s*(td|th)[^>]*>", "\t", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return sanitize_text_for_postgres(text.strip())


def _first_text(mapping: dict[str, Any] | None, keys: list[str]) -> str:
    if not mapping:
        return ""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_text_for_postgres(value.strip())
    return ""



def _walk_json_strings(value: Any):
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            yield sanitize_text_for_postgres(value.strip())
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_json_strings(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk_json_strings(item)
        return


def _first_text_deep(mapping: dict[str, Any] | None, keys: list[str]) -> str:
    """Find a text field in nested parser metadata/image_info."""
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_text_for_postgres(value.strip())
    for value in mapping.values():
        if isinstance(value, dict):
            found = _first_text_deep(value, keys)
            if found:
                return found
    return ""


def _looks_like_ocr_text(text: str, metadata: dict[str, Any] | None = None) -> bool:
    """Best-effort detection for OCR-derived text blocks.

    RAGFlow/DeepDoc may expose scanned-page OCR as normal text blocks, not
    image blocks.  We mark only OCR-looking text so regular text-layer PDFs are
    not polluted with OCR metadata.

    Generic indicators (high punctuation density, repeated short lines) are
    checked first.  Template-specific patterns are loaded via the template
    registry so the detection is not overfit to one dataset.
    """
    value = sanitize_text_for_postgres(text or "")
    meta = metadata or {}
    if not value:
        return False
    source = "\n".join([value, " ".join(_walk_json_strings(meta)) if isinstance(meta, dict) else ""])

    # ── Generic OCR indicators ───────────────────────────────────────
    # High density of alphabetic chars in very short lines suggests OCR line boxes
    lines = value.splitlines()
    short_lines = sum(1 for line in lines if 3 <= len(line.strip()) <= 40)
    total_non_empty = sum(1 for line in lines if line.strip())
    if total_non_empty >= 5 and short_lines / max(total_non_empty, 1) > 0.6:
        return True

    # ── Template-specific patterns ───────────────────────────────────
    from app.services.ocr.templates import _TEMPLATES

    for tid, cls in _TEMPLATES.items():
        instance = cls()
        for pattern in instance.looks_like_ocr_patterns():
            if re.search(pattern, source, flags=re.I):
                return True

    # ── Legacy patterns (backward compat) ────────────────────────────
    legacy = [
        r"invoice\s*number",
        r"invoicenumber",
        r"INV[-_ ]?[0-9O0U]{2,4}[-_ ]?[0-9O0U]{1,3}",
    ]
    return any(re.search(pattern, source, flags=re.I) for pattern in legacy)


def _light_clean_ocr_text(text: str) -> str:
    """Conservative OCR cleanup using generic rules only.

    No dataset-specific patterns.  No O/0, I/1, L/1, 5/S substitutions.
    Template-specific repairs are applied only in the normalised layer.
    """
    return repair_text(text)


def _normalize_ocr_for_recovery(text: str, template_id: str | None = None) -> str:
    """Field-level normalisation for evaluation/recovery only.

    Uses generic identifier normalisation first, then applies template-
    specific normalisation if a template_id is provided.
    Do not use this as chunk.content.
    """
    value = repair_text(text)
    if not value:
        return ""
    normalized = normalize_identifiers(value)
    tmpl = get_template(template_id)
    if tmpl is not None:
        normalized = tmpl.normalize_identifiers(normalized)
    return sanitize_text_for_postgres(normalized.strip())


def _ocr_layer_metadata(raw_text: str, *, enabled: bool = False) -> dict[str, str]:
    """Build OCR layer metadata using the generic ocr module."""
    return layered_ocr_metadata(raw_text, enabled=enabled)


def _infer_page_no_from_text(text: str | None) -> int | None:
    text = sanitize_text_for_postgres(text or "")
    if not text:
        return None
    patterns = [
        r"raster\s+page\s+([0-9]{1,4})",
        r"page\s+([0-9]{1,4})\s*/\s*[0-9]{1,4}",
        r"第\s*([0-9]{1,4})\s*页",
        r"CN-ANCHOR-([0-9]{1,4})-",
        r"EN-CN-MIX-([0-9]{1,4})",
        r"INV-([0-9]{1,4})-[0-9]{1,3}",
        r"line\s+([0-9]{1,4})-[0-9]{1,3}",
        r"SCAN-CALLOUT-([0-9]{1,4})-",
        r"file\s+anchor\s+page-([0-9]{1,4})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            try:
                value = int(m.group(1))
                if 1 <= value <= 10000:
                    return value
            except Exception:
                pass
    return None


def _page_no_from_sources(*sources: Any) -> int | None:
    for source in sources:
        if source is None:
            continue
        if isinstance(source, int):
            return source if source > 0 else None
        if isinstance(source, str):
            if source.isdigit() and int(source) > 0:
                return int(source)
            inferred = _infer_page_no_from_text(source)
            if inferred is not None:
                return inferred
            continue
        if isinstance(source, dict):
            for key in ("page_no", "source_page", "page", "page_index"):
                value = source.get(key)
                if isinstance(value, int):
                    return value if value > 0 else None
                if isinstance(value, str) and value.isdigit():
                    return int(value)
            nested_text = "\n".join(_walk_json_strings(source))
            inferred = _infer_page_no_from_text(nested_text)
            if inferred is not None:
                return inferred
    return None


def _block_page_no(block: ParsedBlock, content_hint: str = "") -> int | None:
    meta = block.metadata or {}
    image_info = meta.get("image_info") if isinstance(meta.get("image_info"), dict) else {}
    return _page_no_from_sources(block.page_no, meta, image_info, block.text, content_hint, block.context_header)


def _block_ocr_text(block: ParsedBlock) -> str:
    meta = block.metadata or {}
    image_info = meta.get("image_info") if isinstance(meta.get("image_info"), dict) else {}
    for source in (meta, image_info):
        value = _first_text_deep(source, ["ocr_raw_text", "ocr_cleaned_text", "ocr_text"])
        if value:
            return value
    return ""


def _short_preview(text: str, limit: int = 600) -> str:
    text = sanitize_text_for_postgres(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _compose_semantic_parts(parts: list[tuple[str, str]], *, fallback: str = "") -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for label, value in parts:
        value = sanitize_text_for_postgres(value or "").strip()
        if not value:
            continue
        normalized = re.sub(r"\s+", " ", value).strip().lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        lines.append(f"{label}：{value}" if label else value)
    if not lines and fallback:
        lines.append(fallback)
    return sanitize_text_for_postgres("\n".join(lines))


def _block_index_content(block: ParsedBlock, file_type: str) -> str:
    """Build semantic index content for visual/table blocks.

    This function must not include URLs, media file names or local paths. Those
    values belong in image_info / metadata, not chunk.content.
    """
    meta = block.metadata or {}
    if block.block_type == "table":
        table_text = block.text or _plain_text_from_html(block.table_html)
        subtype = _first_text_deep(meta, ["table_subtype"])
        label = "表格内容"
        if subtype in {"pseudo_table", "layout_text"}:
            label = "疑似表格/排版文本"
        elif subtype == "key_value_table":
            label = "键值表内容"
        return clean_chunk_content_for_storage(
            _compose_semantic_parts(
                [
                    ("表格标题", _first_text_deep(meta, ["caption", "table_caption", "title", "image_caption"])),
                    (label, table_text),
                    ("OCR文本", _first_text_deep(meta, ["ocr_cleaned_text", "ocr_raw_text", "ocr_text"])),
                ],
                fallback="",
            ),
            chunk_type="table",
        )
    if block.block_type == "image":
        ocr_preview = _short_preview(_first_text_deep(meta, ["ocr_cleaned_text", "ocr_raw_text", "ocr_text"]), 600)
        return clean_chunk_content_for_storage(
            _compose_semantic_parts(
                [
                    ("图片标题", _first_text_deep(meta, ["caption", "figure_caption", "title", "image_caption"])),
                    ("图片描述", _first_text_deep(meta, ["vlm_description", "description", "image_description"])),
                    ("OCR摘录", ocr_preview),
                ],
                fallback="",
            ),
            chunk_type="image",
        )
    return clean_chunk_content_for_storage(block.text, chunk_type="text")

def _vector_values(value: Any, limit: int | None = None) -> list[float]:
    if value is None:
        return []
    try:
        if hasattr(value, "to_list"):
            values = value.to_list()
        elif hasattr(value, "tolist"):
            values = value.tolist()
        else:
            values = list(value)
        if limit is not None:
            values = values[:limit]
        return [float(v) for v in values]
    except Exception:
        return []


def _vector_norm(values: list[float]) -> float | None:
    if not values:
        return None
    try:
        return math.sqrt(sum(v * v for v in values))
    except Exception:
        return None


def update_knowledge_base_chunk_count(db: Session, knowledge_base_id: str) -> None:
    count = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.knowledge_base_id == knowledge_base_id)) or 0
    kb = db.get(KnowledgeBase, knowledge_base_id)
    if kb is not None:
        kb.chunk_count = count


def _block_image_info(block: ParsedBlock) -> dict:
    info = dict(block.metadata or {})
    if block.image_path:
        info.setdefault("path", block.image_path)
    if block.image_url:
        relative, url = _normalize_file_url(block.image_url)
        info["relative_path"] = relative
        info["url"] = url
    nested = info.get("image_info")
    if isinstance(nested, dict):
        nested_url = nested.get("url") or nested.get("relative_path")
        if nested_url and not info.get("url"):
            relative, url = _normalize_file_url(str(nested_url))
            info["relative_path"] = relative
            info["url"] = url
    page_no = _block_page_no(block, block.index_text() or block.text)
    if page_no is not None:
        info.setdefault("page_no", page_no)
        info.setdefault("source_page", page_no)
    if block.bbox:
        info.setdefault("bbox", list(block.bbox))
    return sanitize_json_for_postgres(_normalize_image_info(info))


def _make_chunk(
    *,
    document: Document,
    chunk_id: str,
    content: str,
    chunk_index: int,
    file_type: str,
    chunk_type: str,
    start_at: int | None = None,
    end_at: int | None = None,
    parent_chunk_id: str | None = None,
    context_header: str = "",
    image_info: dict | None = None,
    flags: dict | None = None,
    metadata: dict | None = None,
) -> Chunk:
    clean_content = clean_chunk_content_for_storage(content, chunk_type=chunk_type or "text")
    if (chunk_type or "text") in {"text", "parent_text"}:
        clean_content = normalize_chunk_text(clean_content)
    return Chunk(
        id=chunk_id,
        document_id=document.id,
        knowledge_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        content=clean_content,
        chunk_index=chunk_index,
        token_count=len(clean_content),
        is_enabled=True,
        status="ready",
        start_at=start_at,
        end_at=end_at,
        chunk_type=chunk_type or "text",
        parent_chunk_id=parent_chunk_id,
        content_hash=content_hash(clean_content),
        image_info=sanitize_json_for_postgres(image_info or {}),
        relation_chunks=[],
        indirect_relation_chunks=[],
        context_header=normalize_context_header(context_header or ""),
        flags=sanitize_json_for_postgres(flags or {}),
        metadata_json=sanitize_json_for_postgres({
            "source": "parser",
            "parser": "multimodal" if chunk_type in {"image", "table"} else "basic",
            "file_type": file_type,
            **(metadata or {}),
        }),
    )


def _link_neighbors(chunks: list[Chunk], chunk_types: set[str]) -> None:
    filtered = [chunk for chunk in chunks if chunk.chunk_type in chunk_types]
    for index, chunk in enumerate(filtered):
        if index > 0:
            chunk.pre_chunk_id = filtered[index - 1].id
        if index < len(filtered) - 1:
            chunk.next_chunk_id = filtered[index + 1].id


def _is_code_block_chunk(chunk: Chunk) -> bool:
    meta = chunk.metadata_json or {}
    return (chunk.chunk_type or "text") == "text" and (
        meta.get("content_kind") == "code_block" or sanitize_text_for_postgres(chunk.content).strip().startswith("```")
    )


def _refresh_chunk_content_fields(chunk: Chunk) -> None:
    chunk.content = sanitize_text_for_postgres(chunk.content or "")
    chunk.token_count = len(chunk.content)
    chunk.content_hash = content_hash(chunk.content)


def _finalize_chunk_skip_flags(chunk: Chunk) -> None:
    flags, metadata = clear_stale_embedding_skip_flags(
        content=chunk.content,
        chunk_type=chunk.chunk_type or "text",
        flags=chunk.flags or {},
        metadata=chunk.metadata_json or {},
    )
    chunk.flags = sanitize_json_for_postgres(flags)
    chunk.metadata_json = sanitize_json_for_postgres(metadata)


def _merge_orphan_text_into_neighbor_chunk(target: Chunk, text: str, *, reason: str, prepend: bool = False) -> None:
    """Attach a short orphan text/caption to a neighboring semantic chunk.

    This prevents URL/caption/short-tail fragments from becoming standalone
    chunks while preserving their useful semantics as context on image/table/text
    chunks.
    """
    text = sanitize_text_for_postgres(text or "").strip()
    if not text:
        return
    if prepend:
        target.content = sanitize_text_for_postgres(f"{text}\n{target.content}")
    else:
        target.content = sanitize_text_for_postgres(f"{target.content}\n{text}")
    meta = clear_embedding_skip_metadata(target.metadata_json)
    absorbed = meta.get("absorbed_orphan_texts")
    if not isinstance(absorbed, list):
        absorbed = []
    absorbed.append({"reason": reason, "text": text[:300]})
    meta["absorbed_orphan_texts"] = absorbed
    meta["absorbed_orphan_short_text"] = True
    target.metadata_json = sanitize_json_for_postgres(meta)
    flags = clear_embedding_skip_metadata(target.flags)
    flags["absorbed_orphan_short_text"] = True
    target.flags = sanitize_json_for_postgres(flags)
    _refresh_chunk_content_fields(target)


def _post_process_orphan_short_text_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Merge or hide short orphan text chunks before persistence/indexing."""
    if not chunks:
        return []
    result: list[Chunk] = []
    for index, chunk in enumerate(chunks):
        ctype = chunk.chunk_type or "text"
        meta = chunk.metadata_json or {}
        if ctype in {"text", "parent_text"}:
            chunk.content = normalize_chunk_text(chunk.content)
            _refresh_chunk_content_fields(chunk)
            if not chunk.content or is_url_or_path_only_text(chunk.content):
                # Pure URL/path/page-source noise should not persist as a parent
                # or child chunk.  Resource URLs live in image_info/metadata.
                continue
        if ctype == "text" and is_leading_punctuation(chunk.content):
            prev_chunk = result[-1] if result else None
            if prev_chunk is not None and (prev_chunk.chunk_type or "text") == "text" and prev_chunk.parent_chunk_id == chunk.parent_chunk_id:
                # Preferred: merge with previous chunk instead of just stripping punctuation
                prev_chunk.content = sanitize_text_for_postgres(f"{prev_chunk.content}\n{chunk.content}")
                prev_chunk.end_at = max(prev_chunk.end_at or 0, chunk.end_at or 0)
                _refresh_chunk_content_fields(prev_chunk)
                continue
            else:
                # Last resort: strip leading punctuation (should be rare after sentence-unit refactor)
                chunk.content = strip_invalid_leading_punctuation(chunk.content)
                if not chunk.content:
                    continue
                _refresh_chunk_content_fields(chunk)
        if ctype != "text" or _is_code_block_chunk(chunk) or not is_orphan_short_text_chunk(chunk.content, meta):
            _finalize_chunk_skip_flags(chunk)
            result.append(chunk)
            continue

        prev_chunk = result[-1] if result else None
        next_chunk = chunks[index + 1] if index + 1 < len(chunks) else None
        if next_chunk is not None and _is_code_block_chunk(next_chunk) and next_chunk.parent_chunk_id == chunk.parent_chunk_id:
            next_meta = clear_embedding_skip_metadata(next_chunk.metadata_json)
            existing = sanitize_text_for_postgres(next_meta.get("lead_in_text") or "").strip()
            text = sanitize_text_for_postgres(chunk.content).strip()
            next_meta["lead_in_text"] = "\n".join(part for part in [existing, text] if part)
            next_meta["absorbed_orphan_short_text"] = True
            next_chunk.metadata_json = sanitize_json_for_postgres(next_meta)
            next_chunk.start_at = min(next_chunk.start_at or chunk.start_at or 0, chunk.start_at or 0)
            continue
        if prev_chunk is not None and _is_code_block_chunk(prev_chunk) and prev_chunk.parent_chunk_id == chunk.parent_chunk_id:
            prev_meta = clear_embedding_skip_metadata(prev_chunk.metadata_json)
            existing = sanitize_text_for_postgres(prev_meta.get("tail_note_text") or "").strip()
            text = sanitize_text_for_postgres(chunk.content).strip()
            prev_meta["tail_note_text"] = "\n".join(part for part in [existing, text] if part)
            prev_meta["absorbed_orphan_short_text"] = True
            prev_chunk.metadata_json = sanitize_json_for_postgres(prev_meta)
            prev_chunk.end_at = max(prev_chunk.end_at or 0, chunk.end_at or 0)
            continue
        if (
            prev_chunk is not None
            and (prev_chunk.chunk_type or "text") == "text"
            and not _is_code_block_chunk(prev_chunk)
            and prev_chunk.parent_chunk_id == chunk.parent_chunk_id
            and (prev_chunk.context_header or "") == (chunk.context_header or "")
        ):
            prev_chunk.content = sanitize_text_for_postgres(f"{prev_chunk.content}\n{chunk.content}")
            prev_chunk.end_at = max(prev_chunk.end_at or 0, chunk.end_at or 0)
            prev_meta = clear_embedding_skip_metadata(prev_chunk.metadata_json)
            prev_meta["absorbed_orphan_short_text"] = True
            prev_chunk.metadata_json = sanitize_json_for_postgres(prev_meta)
            _refresh_chunk_content_fields(prev_chunk)
            continue

        # If a short orphan sits next to an image/table/equation in the same
        # parent, treat it as caption/context for that semantic block instead
        # of persisting a standalone short child chunk.
        media_types = {"image", "table", "equation"}
        if prev_chunk is not None and (prev_chunk.chunk_type or "") in media_types and prev_chunk.parent_chunk_id == chunk.parent_chunk_id:
            _merge_orphan_text_into_neighbor_chunk(prev_chunk, chunk.content, reason="short_orphan_after_media", prepend=False)
            prev_chunk.end_at = max(prev_chunk.end_at or 0, chunk.end_at or 0)
            continue
        if next_chunk is not None and (next_chunk.chunk_type or "") in media_types and next_chunk.parent_chunk_id == chunk.parent_chunk_id:
            _merge_orphan_text_into_neighbor_chunk(next_chunk, chunk.content, reason="short_orphan_before_media", prepend=True)
            next_chunk.start_at = min(next_chunk.start_at or chunk.start_at or 0, chunk.start_at or 0)
            continue

        # Last resort: do not keep an isolated short text chunk.  Its text is
        # already present in the parent_text context; keeping it as a child would
        # pollute retrieval and the frontend chunk tree.
        continue

    for index, chunk in enumerate(result):
        chunk.chunk_index = index
    return result



_WEKNORA_MD_TYPES = {"md", "markdown"}
_MD_TABLE_DELIMITER_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _markdown_source_text(text: str | None) -> str:
    """Make Markdown PostgreSQL-safe without destroying source layout.

    ``sanitize_text_for_postgres`` intentionally collapses repeated spaces and
    blank lines. That is useful for normal text, but it corrupts indented code,
    directory trees and other Markdown structures before WeKnora can protect
    them. PostgreSQL text only requires NUL removal here, so keep all other
    spacing and line boundaries intact.
    """
    if text is None:
        return ""
    return str(text).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


def _is_pure_markdown_table(text: str) -> bool:
    """Return True when a child is a standalone Markdown pipe table."""
    lines = [line for line in sanitize_text_for_postgres(text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    return "|" in lines[0] and bool(_MD_TABLE_DELIMITER_RE.match(lines[1]))


def _is_pure_fenced_code(text: str) -> bool:
    value = sanitize_text_for_postgres(text or "").strip()
    return bool(re.match(r"^(?:```|~~~)[\s\S]*?(?:```|~~~)$", value))


def _rebuild_markdown_weknora_parent_child(
    document: Document,
    parsed_document: ParsedDocument,
    file_type: str,
    cfg,
) -> tuple[list[Chunk], int]:
    """Reuse WeKnora's two-pass parent-child logic for Markdown documents.

    This intentionally bypasses the old ``heading -> section -> parent`` path,
    because that path materialized one parent for nearly every Markdown heading.
    The source text is split exactly in WeKnora order:

    document -> large parent candidates -> child re-split -> materialize parent
    only when the candidate was actually subdivided.
    """
    source_text = _markdown_source_text(parsed_document.content)
    if not source_text.strip():
        source_text = _markdown_source_text(
            "\n\n".join(
                block.index_text() or block.text or ""
                for block in sorted(parsed_document.blocks or [], key=lambda item: item.order_index)
                if block.index_text() or block.text
            )
        )
    if not source_text.strip():
        return [], 0

    runtime_cfg = ChunkerConfig(
        chunk_size=max(getattr(cfg, "chunk_size", 512) or 512, 1),
        chunk_overlap=max(getattr(cfg, "chunk_overlap", 80) or 0, 0),
        strategy=getattr(cfg, "strategy", "auto") or "auto",
        separators=list(getattr(cfg, "separators", []) or []),
        enable_parent_child=True,
        parent_chunk_size=max(getattr(cfg, "parent_chunk_size", 4096) or 4096, 1),
        child_chunk_size=max(getattr(cfg, "child_chunk_size", 384) or 384, 1),
        child_chunk_overlap=max(getattr(cfg, "child_chunk_overlap", 64) or 0, 0),
        token_limit=max(getattr(cfg, "token_limit", 0) or 0, 0),
        languages=list(getattr(cfg, "languages", []) or []),
        enable_structured_parent_child=False,
        enable_table_row_split=False,
        enable_image_ocr_child=False,
    )
    result = split_parent_child_result(source_text, runtime_cfg)

    parent_ids = {index: new_id() for index in range(len(result.parents))}
    parent_chunks: dict[int, Chunk] = {}
    children_by_parent: dict[int, list[Any]] = {}
    unparented_children: list[Any] = []

    for child in result.children:
        if child.parent_index is None:
            unparented_children.append(child)
        else:
            children_by_parent.setdefault(child.parent_index, []).append(child)

    for parent_index, parent in enumerate(result.parents):
        child_count = len(children_by_parent.get(parent_index, []))
        parent_chunk = _make_chunk(
            document=document,
            chunk_id=parent_ids[parent_index],
            content=parent.content,
            chunk_index=0,
            file_type=file_type,
            chunk_type="parent_text",
            start_at=parent.start,
            end_at=parent.end,
            context_header=parent.context_header,
            flags={
                "weknora_parent_child": True,
                "direct_weknora_reuse": True,
                "retrieval_role": "context_only",
            },
            metadata={
                **(parent.metadata or {}),
                "parser": "weknora_parent_child",
                "chunk_strategy": "weknora_two_pass",
                "chunk_role": "parent",
                "children_count_planned": child_count,
                "parent_materialization_rule": "subdivided_only",
            },
        )
        # WeKnora stores the exact source slice. Do not run Know_RAG's text
        # normalizer here because it can collapse Markdown table/code newlines.
        parent_chunk.content = _markdown_source_text(parent.content)
        parent_chunk.token_count = len(parent_chunk.content)
        parent_chunk.content_hash = content_hash(parent_chunk.content)
        parent_chunks[parent_index] = parent_chunk

    # Emit chunks in source/child order. The parent is inserted immediately
    # before the first child that references it, matching the frontend tree.
    emitted_parents: set[int] = set()
    new_chunks: list[Chunk] = []
    for child in sorted(result.children, key=lambda item: item.seq):
        parent_index = child.parent_index
        if parent_index is not None and parent_index not in emitted_parents:
            parent_chunk = parent_chunks[parent_index]
            parent_chunk.chunk_index = len(new_chunks)
            new_chunks.append(parent_chunk)
            emitted_parents.add(parent_index)

        content = _markdown_source_text(child.content)
        if not content.strip():
            continue
        chunk_type = "table" if _is_pure_markdown_table(content) else "text"
        content_kind = "code_block" if _is_pure_fenced_code(content) else ("table" if chunk_type == "table" else "text")
        image_info = {"table_markdown": content} if chunk_type == "table" else None
        child_chunk = _make_chunk(
            document=document,
            chunk_id=new_id(),
            content=content,
            chunk_index=len(new_chunks),
            file_type=file_type,
            chunk_type=chunk_type,
            start_at=child.start,
            end_at=child.end,
            parent_chunk_id=parent_ids.get(parent_index) if parent_index is not None else None,
            context_header=child.context_header,
            image_info=image_info,
            flags={
                "weknora_parent_child": True,
                "direct_weknora_reuse": True,
                "parent_materialized": parent_index is not None,
                "content_kind": content_kind,
            },
            metadata={
                **(child.metadata or {}),
                "parser": "weknora_parent_child",
                "chunk_strategy": "weknora_two_pass",
                "chunk_role": "child",
                "parent_materialized": parent_index is not None,
                "content_kind": content_kind,
            },
        )
        child_chunk.content = content
        child_chunk.token_count = len(content)
        child_chunk.content_hash = content_hash(content)
        new_chunks.append(child_chunk)

    logger.info(
        "WeKnora Markdown parent-child split: document_id=%s parents=%s children=%s "
        "unparented_children=%s parent_size=%s child_size=%s",
        document.id,
        len(result.parents),
        len(result.children),
        len(unparented_children),
        runtime_cfg.parent_chunk_size,
        runtime_cfg.child_chunk_size,
    )
    return new_chunks, len(new_chunks)


def _rebuild_fallback_flat_text_from_blocks(
    document: Document,
    parsed_document: ParsedDocument,
    file_type: str,
    cfg,
) -> tuple[list[Chunk], int]:
    """Flat text fallback when the canonical WeKnora path yields zero chunks.

    Used only when blocks exist but the canonical plan produces nothing — so the
    document is not silently marked parsed with chunk_count=0. Produces plain text
    children (no parent_text) tagged with a clear fallback provenance.
    """
    blocks = parsed_document.blocks or []
    if not blocks:
        content = parsed_document.content or ""
        if not content:
            return [], 0
        blocks = [ParsedBlock(block_type="text", text=content, order_index=0)]

    flat_cfg = ChunkerConfig(
        chunk_size=max(getattr(cfg, "chunk_size", 512) or 512, 256),
        chunk_overlap=max(getattr(cfg, "chunk_overlap", 80) or 0, 0),
        strategy=getattr(cfg, "strategy", "auto") or "auto",
        separators=list(getattr(cfg, "separators", []) or []),
        enable_parent_child=False,
        parent_chunk_size=0,
        child_chunk_size=0,
        child_chunk_overlap=0,
        token_limit=max(getattr(cfg, "token_limit", 0) or 0, 0),
        languages=list(getattr(cfg, "languages", []) or []),
        enable_structured_parent_child=False,
        enable_table_row_split=False,
        enable_image_ocr_child=False,
    )

    new_chunks: list[Chunk] = []
    global_offset = 0
    for block in sorted(blocks, key=lambda b: getattr(b, "order_index", 0)):
        text = sanitize_text_for_postgres(
            (block.index_text() if hasattr(block, "index_text") else block.text) or block.text or ""
        )
        if not text or not text.strip():
            continue
        if is_low_information_text(text):
            continue
        for parsed in split_document(text, file_type, flat_cfg):
            content = sanitize_text_for_postgres(parsed.content)
            if not content or is_low_information_text(content):
                continue
            block_meta = {
                "block_type": getattr(block, "block_type", "text"),
                "block_order": getattr(block, "order_index", 0),
                "source": "fallback_flat_text_after_canonical_empty",
                "parser": "fallback_flat_text_after_canonical_empty",
                "file_type": file_type,
                "chunk_strategy": "fallback_flat_text",
                "chunk_role": "child",
                "fallback_reason": "canonical_weknora_produced_zero_chunks",
                **(parsed.metadata or {}),
            }
            new_chunks.append(
                _make_chunk(
                    document=document,
                    chunk_id=new_id(),
                    content=content,
                    chunk_index=len(new_chunks),
                    file_type=file_type,
                    chunk_type="text",
                    start_at=global_offset + parsed.start,
                    end_at=global_offset + parsed.end,
                    context_header=parsed.context_header or getattr(block, "context_header", "") or "",
                    flags={
                        "fallback_flat_text": True,
                        "chunk_strategy": "fallback_flat_text",
                    },
                    metadata=block_meta,
                )
            )
        global_offset += len(text) + 2

    _link_neighbors(new_chunks, {"text"})
    return new_chunks, len(new_chunks)


def _rebuild_canonical_weknora_parent_child(
    document: Document,
    parsed_document: ParsedDocument,
    file_type: str,
    cfg,
    *,
    artifact_out: dict[str, Any] | None = None,
) -> tuple[list[Chunk], int]:
    """Unified parent/child path for every parser output.

    All file types are converted to Canonical Markdown first.  The exact same
    WeKnora two-pass splitter is then used for Markdown, MinerU/DeepDoc PDF,
    DOCX, PPTX, spreadsheets, HTML, TXT/JSON and image documents.  Image blocks
    are represented as ``asset://`` references during splitting and are
    materialized afterwards as image children containing caption + nearby prose
    + VLM description + OCR, with raw media metadata in ``image_info``.
    """
    canonical = build_canonical_document(
        parsed_document,
        file_type=file_type,
        document_title=document.title,
    )
    block_count = len(parsed_document.blocks or [])
    if not canonical.markdown_content.strip():
        logger.warning(
            "canonical markdown is empty — chunk rebuild returning 0 chunks. "
            "document_id=%s file_type=%s blocks=%s canonical_source_format=%s assets=%s",
            document.id,
            file_type,
            block_count,
            getattr(canonical, "source_format", "?"),
            len(getattr(canonical, "assets", []) or []),
        )
        if block_count:
            fallback_chunks, fallback_inserted = _rebuild_fallback_flat_text_from_blocks(
                document, parsed_document, file_type, cfg
            )
            if fallback_inserted:
                logger.info(
                    "canonical-empty fallback produced flat text chunks: "
                    "document_id=%s inserted=%s",
                    document.id, fallback_inserted,
                )
                return fallback_chunks, fallback_inserted
        return [], 0

    materialize_parents = bool(getattr(cfg, "enable_parent_child", False))
    flat_chunk_size = max(getattr(cfg, "chunk_size", 512) or 512, 1)
    flat_chunk_overlap = max(getattr(cfg, "chunk_overlap", 80) or 0, 0)
    effective_child_size = (
        max(getattr(cfg, "child_chunk_size", 384) or 384, 1)
        if materialize_parents
        else flat_chunk_size
    )
    effective_child_overlap = (
        max(getattr(cfg, "child_chunk_overlap", 64) or 0, 0)
        if materialize_parents
        else flat_chunk_overlap
    )
    runtime_cfg = ChunkerConfig(
        chunk_size=flat_chunk_size,
        chunk_overlap=flat_chunk_overlap,
        strategy=getattr(cfg, "strategy", "auto") or "auto",
        separators=list(getattr(cfg, "separators", []) or []),
        enable_parent_child=True,
        parent_chunk_size=max(getattr(cfg, "parent_chunk_size", 4096) or 4096, 1),
        child_chunk_size=effective_child_size,
        child_chunk_overlap=effective_child_overlap,
        token_limit=max(getattr(cfg, "token_limit", 0) or 0, 0),
        languages=list(getattr(cfg, "languages", []) or []),
        enable_structured_parent_child=False,
        enable_table_row_split=False,
        enable_image_ocr_child=False,
    )
    plan = build_canonical_parent_child_plan(canonical, runtime_cfg)
    relation_graph = None
    phase3_applied = False
    if settings.document_relation_graph_enabled:
        relation_graph = build_relation_graph(
            assets=plan.assets,
            provenance_nodes=plan.provenance_nodes,
            figure_links=plan.figure_links,
        )
        if settings.document_asset_group_enabled:
            try:
                layout_graph = build_page_layout_graph(
                    assets=plan.assets,
                    provenance_nodes=plan.provenance_nodes,
                )
                asset_groups = resolve_asset_groups(layout_graph)
                member_edges = build_member_edges(asset_groups)
                shared_edges = resolve_shared_context_edges(
                    layout_graph,
                    asset_groups,
                    assets=plan.assets,
                )
                relation_graph = merge_group_relations(
                    relation_graph,
                    groups=asset_groups,
                    edges=[*member_edges, *shared_edges],
                )
                phase3_applied = True
            except Exception:
                logger.warning(
                    "Phase 3 asset-group resolution failed open: document_id=%s",
                    document.id,
                    exc_info=True,
                )
    if artifact_out is not None:
        link_status_counts: dict[str, int] = {}
        for link in plan.figure_links.links:
            link_status_counts[link.status] = link_status_counts.get(link.status, 0) + 1
        resolution_counts: dict[str, int] = {}
        for node in plan.provenance_nodes:
            resolution_counts[node.resolution] = resolution_counts.get(node.resolution, 0) + 1
        artifact_out.update(
            {
                "parse_revision": new_id(),
                "schema_version": 2 if relation_graph is not None else 1,
                "algorithm_version": (
                    (
                        "canonical-relation-graph-p3-v1"
                        if phase3_applied
                        else "canonical-relation-graph-p2-v1"
                    )
                    if relation_graph is not None
                    else "canonical-provenance-p2.5-v1"
                ),
                "source_format": canonical.source_format,
                "canonical_hash": hashlib.sha256(
                    plan.markdown_content.encode("utf-8")
                ).hexdigest(),
                "canonical_markdown": plan.markdown_content,
                "provenance_json": {
                    "version": 1,
                    "coordinate_space": "normalized_canonical_markdown",
                    "nodes": [node.to_metadata() for node in plan.provenance_nodes],
                },
                "assets_json": {
                    "version": 1,
                    "items": [asset.to_metadata() for asset in plan.assets.values()],
                },
                "figure_links_json": plan.figure_links.to_metadata(),
                "relation_graph_json": relation_graph.to_dict() if relation_graph is not None else {},
                "stats_json": {
                    "canonical_chars": len(plan.markdown_content),
                    "provenance_node_count": len(plan.provenance_nodes),
                    "provenance_resolution_counts": resolution_counts,
                    "asset_count": len(plan.assets),
                    "figure_link_count": len(plan.figure_links.links),
                    "figure_link_status_counts": link_status_counts,
                    "relation_edge_count": len(relation_graph.edges) if relation_graph is not None else 0,
                    "relation_group_count": len(relation_graph.groups) if relation_graph is not None else 0,
                },
            }
        )

    parent_ids = {
        parent.parent_index: new_id()
        for parent in plan.parents
        if materialize_parents
    }
    parent_chunks: dict[int, Chunk] = {}
    for parent in plan.parents if materialize_parents else []:
        parent_id = parent_ids[parent.parent_index]
        parent_chunk = _make_chunk(
            document=document,
            chunk_id=parent_id,
            content=parent.content,
            chunk_index=0,
            file_type=file_type,
            chunk_type="parent_text",
            start_at=parent.start,
            end_at=parent.end,
            context_header=parent.context_header,
            flags={
                "weknora_parent_child": True,
                "canonical_markdown": True,
                "retrieval_role": "context_only",
            },
            metadata={
                **(parent.metadata or {}),
                "parser": "canonical_weknora_parent_child",
                "chunk_strategy": "canonical_markdown_weknora_two_pass",
                "chunk_role": "parent",
                "canonical_source": canonical.metadata.get("canonical_source"),
                "canonical_source_format": canonical.source_format,
                "canonical_asset_count": len(canonical.assets),
                "parent_materialization_rule": (
                    "two_or_more_surviving_children_or_single_page_primary_visual"
                ),
                "coordinate_space": "normalized_canonical_markdown",
            },
        )
        # Canonical Markdown is structure-preserving; bypass text normalizers.
        parent_chunk.content = _markdown_source_text(parent.content)
        parent_chunk.token_count = len(parent_chunk.content)
        parent_chunk.content_hash = content_hash(parent_chunk.content)
        parent_chunks[parent.parent_index] = parent_chunk

    new_chunks: list[Chunk] = []
    emitted_parents: set[int] = set()
    for child in plan.children:
        parent_index = child.parent_index if materialize_parents else None
        if parent_index is not None and parent_index in parent_chunks and parent_index not in emitted_parents:
            parent_chunk = parent_chunks[parent_index]
            parent_chunk.chunk_index = len(new_chunks)
            new_chunks.append(parent_chunk)
            emitted_parents.add(parent_index)

        chunk = _make_chunk(
            document=document,
            chunk_id=new_id(),
            content=child.content,
            chunk_index=len(new_chunks),
            file_type=file_type,
            chunk_type=child.chunk_type,
            start_at=child.start,
            end_at=child.end,
            parent_chunk_id=parent_ids.get(parent_index) if parent_index is not None else None,
            context_header=child.context_header,
            image_info=child.image_info,
            flags={
                **(child.flags or {}),
                "weknora_parent_child": True,
                "canonical_markdown": True,
                "parent_materialized": parent_index is not None,
                "coordinate_space": "normalized_canonical_markdown",
                "source_position_is_exact": False,
            },
            metadata={
                **(child.metadata or {}),
                "parser": "canonical_weknora_parent_child",
                "chunk_strategy": "canonical_markdown_weknora_two_pass",
                "chunk_role": "child",
                "canonical_source": canonical.metadata.get("canonical_source"),
                "canonical_source_format": canonical.source_format,
                "canonical_asset_count": len(canonical.assets),
                "parent_materialized": parent_index is not None,
                "coordinate_space": "normalized_canonical_markdown",
                "source_position_is_exact": False,
            },
        )
        # Preserve Markdown/code/tree layout exactly. Image child content is
        # already a semantic text payload and is safe to keep verbatim too.
        chunk.content = _markdown_source_text(child.content).strip()
        chunk.token_count = len(chunk.content)
        chunk.content_hash = content_hash(chunk.content)
        new_chunks.append(chunk)

    new_chunks = _apply_figure_bindings(
        new_chunks,
        plan,
        enabled=settings.document_relation_materialization_enabled,
        relation_graph=relation_graph,
    )
    if relation_graph is not None and settings.document_relation_materialization_enabled:
        new_chunks = materialize_relation_summaries(new_chunks, relation_graph)
    for chunk in new_chunks:
        meta = dict(chunk.metadata_json or {})
        meta["source_position_is_exact"] = True
        chunk.metadata_json = meta

    parse_revision = str((artifact_out or {}).get("parse_revision") or "")
    if parse_revision:
        for chunk in new_chunks:
            chunk.metadata_json = sanitize_json_for_postgres(
                {
                    **(chunk.metadata_json or {}),
                    "canonical_parse_revision": parse_revision,
                }
            )

    logger.info(
        "Canonical WeKnora parent-child split: document_id=%s file_type=%s "
        "canonical_chars=%s assets=%s parents=%s children=%s parent_size=%s child_size=%s",
        document.id,
        file_type,
        len(plan.markdown_content),
        len(plan.assets),
        len(plan.parents),
        len(plan.children),
        runtime_cfg.parent_chunk_size,
        runtime_cfg.child_chunk_size,
    )

    if not new_chunks and block_count:
        logger.warning(
            "canonical markdown non-empty but 0 children materialized — "
            "falling back to flat text chunks. document_id=%s file_type=%s "
            "blocks=%s canonical_chars=%s",
            document.id, file_type, block_count, len(plan.markdown_content),
        )
        fallback_chunks, fallback_inserted = _rebuild_fallback_flat_text_from_blocks(
            document, parsed_document, file_type, cfg
        )
        if fallback_inserted:
            if artifact_out is not None:
                artifact_out.clear()
            return fallback_chunks, fallback_inserted

    return new_chunks, len(new_chunks)


def _enforce_parent_child_integrity(chunks: list[Chunk]) -> list[Chunk]:
    """Guarantee valid persisted parents after all cleanup stages.

    WeKnora only creates a parent when subdivision actually happened. Know_RAG
    has additional cleanup stages, so this final guard prevents those stages
    from leaving 0-child or ordinary 1-child parents behind.  Page-scoped
    visual parents are the deliberate exception: their lightweight parent
    preserves an authoritative page boundary even with one surviving child.
    """
    parents = {chunk.id: chunk for chunk in chunks if (chunk.chunk_type or "") == "parent_text"}
    if not parents:
        return chunks
    children_by_parent: dict[str, list[Chunk]] = {parent_id: [] for parent_id in parents}
    for chunk in chunks:
        if chunk.parent_chunk_id in children_by_parent:
            children_by_parent[chunk.parent_chunk_id].append(chunk)

    drop_parent_ids = {
        parent_id
        for parent_id, children in children_by_parent.items()
        if not (
            len(children) >= 2
            or (
                len(children) == 1
                and (
                    bool(
                        (parents[parent_id].metadata_json or {}).get(
                            "singleton_primary_visual_parent"
                        )
                    )
                    or bool(
                        (parents[parent_id].metadata_json or {}).get(
                            "visual_page_scoped_parent"
                        )
                    )
                    or bool(
                        (children[0].metadata_json or {}).get(
                            "singleton_primary_visual_parent"
                        )
                    )
                    or bool(
                        (children[0].metadata_json or {}).get(
                            "visual_page_scoped_parent"
                        )
                    )
                )
            )
        )
    }
    if not drop_parent_ids:
        return chunks

    for chunk in chunks:
        if chunk.parent_chunk_id in drop_parent_ids:
            chunk.parent_chunk_id = None
            meta = dict(chunk.metadata_json or {})
            meta["parent_materialized"] = False
            meta["parent_flattened_reason"] = "fewer_than_two_surviving_children"
            chunk.metadata_json = sanitize_json_for_postgres(meta)

    repaired = [chunk for chunk in chunks if chunk.id not in drop_parent_ids]
    for index, chunk in enumerate(repaired):
        chunk.chunk_index = index
    logger.warning(
        "Flattened %s parent chunks with fewer than two surviving children",
        len(drop_parent_ids),
    )
    return repaired


# ─────────────────────────────────────────────────────────────────────────────
# Structured parent-child chunking (Stage 1)
#
# Goal: without changing the database schema, build real parent containers first
# and attach text / image(chart) / table / equation children to the nearest
# parent.  This path is used only when cfg.enable_parent_child is enabled; the
# legacy chunking path is preserved for the non-parent-child mode.
# ─────────────────────────────────────────────────────────────────────────────

_NOISE_BLOCK_TYPES = {"header", "footer", "page_number"}
_IMAGE_LIKE_RAW_TYPES = {"image", "figure", "fig", "chart", "graph", "plot"}
_TABLE_LIKE_RAW_TYPES = {"table", "table_body"}
_EQUATION_LIKE_RAW_TYPES = {"equation", "formula", "inline_equation", "interline_equation", "display_equation"}
_HEADING_RAW_TYPES = {"title", "heading", "section_title"}
_CAPTION_KEYWORDS = {
    "image": ("图", "figure", "fig.", "fig ", "chart", "plot", "graph"),
    "table": ("表", "table"),
    "equation": ("公式", "equation", "formula", "eq."),
}
DROP_HEADING_CHILD_CHUNKS = True
MERGE_SHORT_TEXT_BLOCKS = True
CODE_BLOCK_RAW_TYPES = {"code", "code_block", "fenced_code", "pre", "source_code"}
CODE_LEAD_IN_SUFFIXES = ("：", ":", "如下", "如下：", "主要入口：", "示例：", "流程：", "管道：", "实现：")
CODE_TAIL_NOTE_RE = re.compile(r"^\s*(返回|输出|完成后|用于|说明|结果|写入|生成|得到)\b", re.I)



def _structure_content_kind(text: str, context_header: str = "") -> str:
    """Classify structure-like text without changing persisted chunk_type."""
    source = "\n".join(part for part in [context_header, text] if part)
    if not is_structure_like_text(source):
        return "text"
    lowered = source.lower()
    if any(marker in lowered for marker in ("项目结构", "目录结构", "backend/", "frontend/", "app/services", "核心服务")):
        return "project_structure"
    if looks_like_directory_tree(text):
        return "directory_tree"
    return "code_structure"


# ── Code block detection (delegated to code_context_merger) ──

def _is_fenced_code_text(text: str) -> bool:
    return _is_fenced_code_text_new(text)


def _is_code_block_block(block: ParsedBlock) -> bool:
    return _is_code_block_block_new(block)


def looks_like_code_lead_in(text: str) -> bool:
    return _looks_like_code_lead_in_new(text)


def looks_like_code_tail_note(text: str) -> bool:
    return _looks_like_code_tail_note_new(text)


def is_orphan_short_text(content: str, metadata: dict | None = None) -> bool:
    return _is_orphan_short_text_new(content, metadata) if metadata is not None else is_orphan_short_text_chunk(content)


def _append_block_meta_text(block: ParsedBlock, key: str, text: str) -> None:
    value = sanitize_text_for_postgres(text or "").strip()
    if not value:
        return
    meta = dict(block.metadata or {})
    existing = sanitize_text_for_postgres(meta.get(key) or "").strip()
    meta[key] = "\n".join(part for part in [existing, value] if part)
    meta["content_kind"] = "code_block"
    meta["merged_short_text_context"] = True
    if key == "lead_in_text":
        meta["has_lead_in"] = True
    if key == "tail_note_text":
        meta["has_tail_note"] = True
    block.metadata = sanitize_json_for_postgres(meta)


def _merge_short_text_around_code_blocks(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    """Merge orphan short text blocks into adjacent code blocks before chunking."""
    if not blocks:
        return []
    result: list[ParsedBlock] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block.block_type != "text" or _is_code_block_block(block) or not is_orphan_short_text(block.text, block.metadata):
            result.append(block)
            index += 1
            continue

        prev_block = result[-1] if result else None
        next_block = blocks[index + 1] if index + 1 < len(blocks) else None
        prev_is_code = prev_block is not None and _is_code_block_block(prev_block)
        next_is_code = next_block is not None and _is_code_block_block(next_block)
        text = sanitize_text_for_postgres(block.text).strip()

        if next_is_code and (looks_like_code_lead_in(text) or not prev_is_code or not looks_like_code_tail_note(text)):
            _append_block_meta_text(next_block, "lead_in_text", text)
            next_meta = clear_embedding_skip_metadata(next_block.metadata or {})
            next_meta["merged_into_code_block"] = True
            next_block.metadata = sanitize_json_for_postgres(next_meta)
            index += 1
            continue
        if prev_is_code:
            _append_block_meta_text(prev_block, "tail_note_text", text)
            prev_meta = clear_embedding_skip_metadata(prev_block.metadata or {})
            prev_meta["merged_into_code_block"] = True
            prev_block.metadata = sanitize_json_for_postgres(prev_meta)
            index += 1
            continue
        if prev_block is not None and prev_block.block_type == "text" and not _is_code_block_block(prev_block) and (prev_block.context_header or "") == (block.context_header or ""):
            prev_block.text = sanitize_text_for_postgres(f"{prev_block.text}\n{text}")
            prev_meta = clear_embedding_skip_metadata(prev_block.metadata or {})
            prev_meta["absorbed_orphan_short_text"] = True
            prev_block.metadata = sanitize_json_for_postgres(prev_meta)
            index += 1
            continue
        if next_block is not None and next_block.block_type == "text" and not _is_code_block_block(next_block) and (next_block.context_header or "") == (block.context_header or ""):
            next_block.text = sanitize_text_for_postgres(f"{text}\n{next_block.text}")
            next_meta = clear_embedding_skip_metadata(next_block.metadata or {})
            next_meta["absorbed_orphan_short_text"] = True
            next_block.metadata = sanitize_json_for_postgres(next_meta)
            index += 1
            continue

        block_meta = clear_embedding_skip_metadata(block.metadata or {})
        block_meta.update({
            "low_information": True,
            "orphan_short_chunk": True,
            "skip_embedding_recommended": True,
            "skip_reason": "orphan_short_chunk",
        })
        block.metadata = sanitize_json_for_postgres(block_meta)
        result.append(block)
        index += 1

    for order, item in enumerate(result):
        item.order_index = order
    return result



# ── Block-type classifiers (delegated to section_builder) ──

def _raw_block_type(block: ParsedBlock) -> str:
    return _raw_block_type_new(block)


def _is_noise_block(block: ParsedBlock) -> bool:
    return _is_noise_block_new(block)


def _is_equation_block(block: ParsedBlock) -> bool:
    return _is_equation_block_new(block)


def _is_heading_block(block: ParsedBlock) -> bool:
    return _is_heading_block_new(block)


def _heading_level(block: ParsedBlock) -> int:
    return _heading_level_new(block)


def _heading_path_text(path: list[str]) -> str:
    return " > ".join(normalize_context_header(item) for item in path if item)


def _block_context_title(block: ParsedBlock) -> str:
    meta = block.metadata or {}
    return sanitize_text_for_postgres(
        block.context_header
        or _first_text_deep(meta, ["context_header", "caption", "figure_caption", "table_caption", "title", "image_caption"])
        or ""
    )


def _block_text_for_context(block: ParsedBlock, file_type: str = "") -> str:
    if block.block_type in {"image", "table"}:
        return _block_index_content(block, file_type)
    return sanitize_text_for_postgres(block.index_text() or block.text or block.context_header or "")


def _caption_kind_for_block(block: ParsedBlock) -> str:
    raw_type = _raw_block_type(block)
    if _is_equation_block(block):
        return "equation"
    if block.block_type == "table" or raw_type in _TABLE_LIKE_RAW_TYPES:
        return "table"
    if block.block_type == "image" or raw_type in _IMAGE_LIKE_RAW_TYPES:
        return "image"
    return "text"


def _looks_like_caption(text_value: str, kind: str) -> bool:
    value = sanitize_text_for_postgres(text_value or "").strip()
    if not value or len(value) > 260:
        return False
    if is_caption_text(value):
        return True
    lowered = value.lower()
    keywords = _CAPTION_KEYWORDS.get(kind, ())
    return any(keyword in lowered for keyword in keywords)


def _nearby_text_context(section: dict[str, Any], block: ParsedBlock, file_type: str, *, window: int = 2) -> dict[str, Any]:
    blocks = section.get("blocks") or []
    try:
        index = blocks.index(block)
    except ValueError:
        return {}

    kind = _caption_kind_for_block(block)
    before_items: list[ParsedBlock] = []
    after_items: list[ParsedBlock] = []
    for candidate in reversed(blocks[max(0, index - window):index]):
        if candidate is block or _is_noise_block(candidate):
            continue
        text_value = _block_text_for_context(candidate, file_type)
        if text_value:
            before_items.append(candidate)
    before_items = list(reversed(before_items))
    for candidate in blocks[index + 1:index + 1 + window]:
        if candidate is block or _is_noise_block(candidate):
            continue
        text_value = _block_text_for_context(candidate, file_type)
        if text_value:
            after_items.append(candidate)

    caption_block: ParsedBlock | None = None
    for candidate in [*after_items, *reversed(before_items)]:
        text_value = _block_text_for_context(candidate, file_type)
        if _looks_like_caption(text_value, kind):
            caption_block = candidate
            break

    before_text = sanitize_text_for_postgres("\n".join(_block_text_for_context(item, file_type) for item in before_items if _block_text_for_context(item, file_type)))
    after_text = sanitize_text_for_postgres("\n".join(_block_text_for_context(item, file_type) for item in after_items if _block_text_for_context(item, file_type)))
    caption_text = _block_text_for_context(caption_block, file_type) if caption_block else ""
    return {
        "nearby_context_before": _short_preview(before_text, 800),
        "nearby_context_after": _short_preview(after_text, 800),
        "bound_caption": sanitize_text_for_postgres(caption_text),
        "bound_caption_block_order": caption_block.order_index if caption_block else None,
        "bound_caption_kind": kind if caption_block else "",
    }


def _augment_child_content(content: str, neighbor_info: dict[str, Any], *, label: str = "内容") -> str:
    """Return semantic content only; keep nearby context in metadata.

    Older code stored display wrappers like ``绑定标题：`` / ``前文：`` /
    ``后文：`` in chunk.content.  That polluted embedding and BM25.  The
    caption/nearby fields are still persisted through metadata by callers; the
    content itself is kept clean.
    """
    caption = sanitize_text_for_postgres(neighbor_info.get("bound_caption") or "")
    content = sanitize_text_for_postgres(content or "")
    parts = [caption, content] if caption and caption not in content else [content]
    return clean_chunk_content_for_storage("\n".join(part for part in parts if part), chunk_type="text")

def _normalized_child_type(block: ParsedBlock) -> str:
    return _normalized_child_type_new(block)


def _block_parent_text(block: ParsedBlock, file_type: str) -> str:
    """Build compact parent text; full table/image/OCR content stays in children."""
    raw_type = _raw_block_type(block)
    meta = block.metadata or {}
    raw_text = block.index_text() or block.text or ""
    if _is_code_block_block(block):
        lead = sanitize_text_for_postgres(meta.get("lead_in_text") or "")
        tail = sanitize_text_for_postgres(meta.get("tail_note_text") or "")
        preview = _short_preview(raw_text, 1200)
        return sanitize_text_for_postgres("\n\n".join(part for part in [lead, preview, tail] if part))
    if _structure_content_kind(raw_text, block.context_header) != "text":
        content = clean_chunk_content_for_storage(raw_text, chunk_type="text")
        return _short_preview(content, 1800)
    if block.block_type == "table" or raw_type in _TABLE_LIKE_RAW_TYPES:
        caption = _first_text_deep(meta, ["caption", "table_caption", "title", "image_caption"])
        table_text = clean_chunk_content_for_storage(block.text or _plain_text_from_html(block.table_html), chunk_type="table")
        preview = _short_preview(table_text, 320)
        return _compose_semantic_parts([("表格标题", caption), ("表格摘要", preview)], fallback=preview)
    if block.block_type == "image" or raw_type in _IMAGE_LIKE_RAW_TYPES:
        caption = _first_text_deep(meta, ["caption", "figure_caption", "title", "image_caption"])
        desc = _first_text_deep(meta, ["vlm_description", "description", "image_description"])
        ocr = _short_preview(_block_ocr_text(block), 180)
        page = _block_page_no(block, block.text or block.context_header)
        return _compose_semantic_parts(
            [
                ("图片标题", caption),
                ("图片描述", desc),
                ("OCR摘录", ocr),
                ("页码", str(page) if page is not None else ""),
            ],
            fallback=caption or desc or (f"第 {page} 页图片" if page is not None else ""),
        )
    content = clean_chunk_content_for_storage(block.index_text() or block.text, chunk_type="text")
    if not content:
        return ""
    if raw_type in _EQUATION_LIKE_RAW_TYPES:
        return content
    return content


def _compose_code_block_content(block: ParsedBlock) -> str:
    """Build code child content with absorbed lead-in/tail-note context."""
    meta = block.metadata or {}
    lead = sanitize_text_for_postgres(meta.get("lead_in_text") or "")
    tail = sanitize_text_for_postgres(meta.get("tail_note_text") or "")
    code = sanitize_text_for_postgres(block.text or block.index_text() or "")
    return clean_chunk_content_for_storage("\n\n".join(part for part in [lead, code, tail] if part), chunk_type="text")

def _section_page_range(blocks: list[ParsedBlock], file_type: str = "") -> tuple[int | None, int | None]:
    pages: list[int] = []
    for block in blocks:
        page = _block_page_no(block, block.index_text() or block.text or _block_index_content(block, file_type))
        if page is not None:
            pages.append(page)
    if not pages:
        return None, None
    return min(pages), max(pages)


def _section_content_len(blocks: list[ParsedBlock], file_type: str) -> int:
    total = 0
    for block in blocks:
        total += len(_block_parent_text(block, file_type)) + 2
    return total


def _child_split_config(cfg) -> ChunkerConfig:
    return ChunkerConfig(
        chunk_size=max(getattr(cfg, "child_chunk_size", None) or getattr(cfg, "chunk_size", 512), 1),
        chunk_overlap=max(getattr(cfg, "child_chunk_overlap", None) or 0, 0),
        strategy=getattr(cfg, "strategy", "auto") or "auto",
        separators=getattr(cfg, "separators", []) or [],
        enable_parent_child=False,
        parent_chunk_size=getattr(cfg, "parent_chunk_size", 4096) or 4096,
        child_chunk_size=getattr(cfg, "child_chunk_size", 384) or 384,
        child_chunk_overlap=getattr(cfg, "child_chunk_overlap", 64) or 64,
        token_limit=getattr(cfg, "token_limit", 0) or 0,
        languages=getattr(cfg, "languages", []) or [],
        enable_structured_parent_child=getattr(cfg, "enable_structured_parent_child", True),
        enable_table_row_split=getattr(cfg, "enable_table_row_split", False),
        enable_image_ocr_child=getattr(cfg, "enable_image_ocr_child", True),
    )


def _parent_context_header(section: dict[str, Any]) -> str:
    heading = normalize_context_header(_heading_path_text(section.get("heading_path") or []))
    if heading:
        return heading
    title = sanitize_text_for_postgres(section.get("title") or "")
    if title:
        return normalize_context_header(title)
    page_start = section.get("page_start")
    if page_start:
        return f"第 {page_start} 页内容"
    return "文档内容"


def _build_parent_content(section: dict[str, Any], file_type: str, document: Document) -> str:
    """Build clean parent content.

    Parent text should be a semantic context block, not a display template with
    ``文档：`` / ``章节：`` / ``内容：`` labels.
    """
    heading = _parent_context_header(section)
    body_lines: list[str] = []
    for block in section.get("blocks") or []:
        if _is_noise_block(block):
            continue
        text = _block_parent_text(block, file_type)
        if not text:
            continue
        if is_low_information_text(text):
            continue
        body_lines.append(text)
    body = sanitize_text_for_postgres("\n\n".join(body_lines))
    if not body:
        return ""
    if heading:
        base_heading = strip_continuation_suffix(heading)
        # Context headers live in chunk.context_header/metadata.  If parser text
        # already begins with the same synthetic header, keep only one body copy.
        repeated_heading = f"{base_heading}（续）（续）" if base_heading else ""
        for candidate in (repeated_heading, heading, base_heading):
            if candidate and body.startswith(candidate):
                body = body[len(candidate):].lstrip("\n\r ：:")
                break
    return sanitize_text_for_postgres(body)

def _make_structured_sections(blocks: list[ParsedBlock], file_type: str, cfg) -> list[dict[str, Any]]:
    """Group parsed blocks into parent sections.

    Headings update context only and are not kept as standalone child blocks.
    Low-information section labels such as ``引 言`` and ``参考文献`` are also
    kept out of section blocks, preventing short chunk pollution.
    """
    sections: list[dict[str, Any]] = []
    heading_stack: list[str] = []
    current: dict[str, Any] | None = None
    parent_max = max(getattr(cfg, "parent_chunk_size", 4096) or 4096, 512)

    def start_section(title: str = "", strategy: str = "synthetic") -> dict[str, Any]:
        nonlocal current
        current = {
            "title": normalize_context_header(title or ""),
            "heading_path": [normalize_context_header(item) for item in heading_stack],
            "blocks": [],
            "strategy": strategy,
            "page_start": None,
            "page_end": None,
            "skipped_heading_child_count": 0,
            "skipped_low_info_text_count": 0,
        }
        sections.append(current)
        return current

    def ensure_section(block: ParsedBlock) -> dict[str, Any]:
        nonlocal current
        if current is None:
            title = _block_context_title(block)
            current = start_section(title=title, strategy="synthetic")
        return current

    def update_section_page(sec: dict[str, Any], block: ParsedBlock) -> None:
        page = _block_page_no(block, block.index_text() or block.text or _block_index_content(block, file_type))
        if page is not None:
            sec["page_start"] = page if sec.get("page_start") is None else min(sec["page_start"], page)
            sec["page_end"] = page if sec.get("page_end") is None else max(sec["page_end"], page)

    for block in sorted(blocks, key=lambda item: item.order_index):
        if _is_noise_block(block):
            continue

        raw_text_for_hint = block.index_text() or block.text or block.context_header or ""
        text_hint = clean_chunk_content_for_storage(raw_text_for_hint, chunk_type="text")
        normalized_type = _normalized_child_type(block)

        # Headings are structural context, not retrieval evidence.  Handle them
        # before the generic low-information gate; otherwise short Markdown
        # headings (for example ``# 第一章``) are dropped before they can bind
        # the following table to the correct section.
        if _is_heading_block(block) and text_hint:
            level = _heading_level(block)
            while len(heading_stack) >= level:
                heading_stack.pop()
            heading_stack.append(normalize_context_header(text_hint))
            sec = start_section(title=text_hint, strategy="heading")
            sec["skipped_heading_child_count"] += 1
            update_section_page(sec, block)
            continue

        # Drop text-only transport/page noise before it can create a parent or
        # a standalone child.  Examples: www.xxx.com, /files/... paths, raw
        # image filenames, and text that becomes empty after URL/path stripping.
        if normalized_type == "text" and (
            not text_hint
            or is_url_or_path_only_text(raw_text_for_hint)
            or should_skip_text_chunk(text_hint, chunk_type="text", metadata=block.metadata or {})
        ):
            if current is not None:
                current["skipped_low_info_text_count"] += 1
                update_section_page(current, block)
            continue

        if normalized_type == "text" and text_hint and is_low_information_text(text_hint):
            sec = ensure_section(block)
            sec["skipped_low_info_text_count"] += 1
            update_section_page(sec, block)
            continue

        sec = ensure_section(block)
        block_len = len(_block_parent_text(block, file_type))
        if sec.get("blocks") and _section_content_len(sec["blocks"], file_type) + block_len > parent_max:
            title = _parent_context_header(sec)
            sec = start_section(title=make_continuation_header(title or "文档内容"), strategy="size_split")
        sec["blocks"].append(block)
        update_section_page(sec, block)

    filtered: list[dict[str, Any]] = []
    for section in sections:
        section["blocks"] = _merge_short_text_around_code_blocks(section.get("blocks") or [])
        blocks_in_section = section.get("blocks") or []
        if not blocks_in_section:
            continue
        has_semantic_block = False
        for block in blocks_in_section:
            ctype = _normalized_child_type(block)
            text = _block_parent_text(block, file_type)
            if ctype in {"image", "table"} or (text and not is_low_information_text(text)):
                has_semantic_block = True
                break
        if has_semantic_block:
            filtered.append(section)
    return filtered

def _structured_block_meta(
    block: ParsedBlock,
    *,
    parent_id: str,
    parent_context: str,
    section: dict[str, Any],
    block_page_no: int | None,
    block_meta: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_type = _raw_block_type(block)
    meta = {
        **block_meta,
        "chunk_role": "child",
        "parent_chunk_id": parent_id,
        "parent_context_header": parent_context,
        "parent_strategy": section.get("strategy") or "synthetic",
        "heading_path": section.get("heading_path") or [],
        "raw_block_type": raw_type,
        "normalized_block_type": _normalized_child_type(block),
        "source_block_order": block.order_index,
        "page_start": section.get("page_start"),
        "page_end": section.get("page_end"),
    }
    if block_page_no is not None:
        meta["page_no"] = block_page_no
        meta.setdefault("source_page", block_page_no)
    if block.bbox:
        meta["bbox"] = list(block.bbox)
    if _is_equation_block(block):
        meta["subtype"] = "equation"
        meta["is_equation"] = True
    if raw_type in _IMAGE_LIKE_RAW_TYPES:
        meta["visual_subtype"] = "chart" if raw_type in {"chart", "graph", "plot"} else raw_type
    structure_kind = _structure_content_kind(block.index_text() or block.text or "", parent_context)
    if structure_kind != "text":
        meta["content_kind"] = structure_kind
        meta["structure_like"] = True
        meta["preserve_line_boundaries"] = True
    if extra:
        meta.update(extra)
    return meta


def _rebuild_document_chunks_structured_parent_child(
    db: Session,
    document: Document,
    parsed_document: ParsedDocument,
    file_type: str,
    cfg,
) -> tuple[list[Chunk], int]:
    blocks = parsed_document.blocks or [ParsedBlock(block_type="text", text=parsed_document.content, order_index=0)]
    sections = _make_structured_sections(blocks, file_type, cfg)
    child_cfg = _child_split_config(cfg)
    new_chunks: list[Chunk] = []
    inserted = 0
    global_offset = 0

    def merge_short_parsed_chunks(parsed_chunks):
        """Merge tiny tail/head chunks produced by recursive split.

        A sentence-aware splitter still may produce a short final sentence.  It
        should be attached to adjacent content rather than becoming an isolated
        retrieval unit.
        """
        merged: list[Any] = []
        for parsed in parsed_chunks:
            content = clean_chunk_content_for_storage(parsed.content, chunk_type="text")
            if not content:
                continue
            if is_low_information_text(content):
                continue
            if len(content) < MIN_CHILD_TEXT_CHARS and merged:
                prev = merged[-1]
                prev.content = sanitize_text_for_postgres(f"{prev.content}\n{content}")
                prev.end = parsed.end
                prev.metadata = {**(prev.metadata or {}), **(parsed.metadata or {}), "merged_short_tail": True}
                continue
            merged.append(parsed)
        if len(merged) >= 2:
            first_content = clean_chunk_content_for_storage(merged[0].content, chunk_type="text")
            if len(first_content) < MIN_CHILD_TEXT_CHARS:
                merged[1].content = sanitize_text_for_postgres(f"{first_content}\n{merged[1].content}")
                merged[1].start = merged[0].start
                merged[1].metadata = {**(merged[1].metadata or {}), "merged_short_head": True}
                merged.pop(0)
        return merged

    for section_index, section in enumerate(sections):
        parent_id = new_id()
        parent_context = _parent_context_header(section)
        parent_content = _build_parent_content(section, file_type, document)
        if not parent_content or (len(parent_content) < MIN_PARENT_TEXT_CHARS and is_low_information_text(parent_content)):
            continue

        page_start = section.get("page_start")
        page_end = section.get("page_end")
        child_types: list[str] = []
        skipped_heading_child_count = int(section.get("skipped_heading_child_count") or 0)
        skipped_low_info_text_count = int(section.get("skipped_low_info_text_count") or 0)

        for block in section.get("blocks") or []:
            ctype = _normalized_child_type(block)
            raw_type = _raw_block_type(block)
            if _is_equation_block(block):
                child_types.append("equation")
            elif ctype == "image" and raw_type in {"chart", "graph", "plot"}:
                child_types.append("chart")
            else:
                child_types.append(ctype)

        parent_meta = {
            "source": "parser",
            "parser": "structured_parent_child",
            "file_type": file_type,
            "chunk_role": "parent",
            "parent_strategy": section.get("strategy") or "synthetic",
            "section_index": section_index,
            "heading_path": section.get("heading_path") or [],
            "page_start": page_start,
            "page_end": page_end,
            "child_block_orders": [b.order_index for b in section.get("blocks") or []],
            "child_types": sorted(set(child_types)),
            "children_count_planned": len(section.get("blocks") or []),
            "skipped_heading_child_count": skipped_heading_child_count,
            "skipped_low_info_text_count": skipped_low_info_text_count,
            "short_text_policy": "merge_or_skip",
            "min_child_text_chars": MIN_CHILD_TEXT_CHARS,
            "chunk_strategy": getattr(cfg, "strategy", "auto") or "auto",
            "token_limit": getattr(cfg, "token_limit", 0) or 0,
            "languages": getattr(cfg, "languages", []) or [],
            "separators": getattr(cfg, "separators", []) or [],
        }
        parent_flags = {
            "is_parent_chunk": True,
            "structured_parent_child": True,
            "has_image_child": any(t in {"image", "chart"} for t in child_types),
            "has_table_child": "table" in child_types,
            "has_equation_child": "equation" in child_types,
        }
        new_chunks.append(
            _make_chunk(
                document=document,
                chunk_id=parent_id,
                content=parent_content,
                chunk_index=inserted,
                file_type=file_type,
                chunk_type="parent_text",
                start_at=global_offset,
                end_at=global_offset + len(parent_content),
                context_header=parent_context,
                flags=parent_flags,
                metadata=parent_meta,
            )
        )
        inserted += 1
        global_offset += len(parent_content) + 2

        text_buffer_parts: list[str] = []
        text_buffer_blocks: list[ParsedBlock] = []
        text_buffer_meta: dict[str, Any] = {}
        text_buffer_start_offset = global_offset

        def flush_text_buffer() -> None:
            nonlocal inserted, global_offset, text_buffer_parts, text_buffer_blocks, text_buffer_meta, text_buffer_start_offset
            if not text_buffer_parts:
                return
            combined = clean_chunk_content_for_storage("\n\n".join(text_buffer_parts), chunk_type="text")
            source_blocks = list(text_buffer_blocks)
            source_orders = [b.order_index for b in source_blocks]
            if not combined or is_low_information_text(combined):
                text_buffer_parts = []
                text_buffer_blocks = []
                text_buffer_meta = {}
                text_buffer_start_offset = global_offset
                return

            parsed_chunks = merge_short_parsed_chunks(split_document(combined, file_type, child_cfg))
            for parsed in parsed_chunks:
                content = clean_chunk_content_for_storage(parsed.content, chunk_type="text")
                if not content or is_low_information_text(content):
                    continue
                skip_recommended = False
                first_block = source_blocks[0] if source_blocks else ParsedBlock(block_type="text", text=content, order_index=0)
                first_page = _block_page_no(first_block, first_block.index_text() or first_block.text or content)
                source_pages = [
                    _block_page_no(b, b.index_text() or b.text or "")
                    for b in source_blocks
                ]
                source_pages = [p for p in source_pages if p is not None]
                meta = _structured_block_meta(
                    first_block,
                    parent_id=parent_id,
                    parent_context=parent_context,
                    section=section,
                    block_page_no=first_page,
                    block_meta={
                        **text_buffer_meta,
                        "source_block_orders": source_orders,
                        "merged_text_blocks": len(source_blocks) > 1,
                        "source_page_start": min(source_pages) if source_pages else None,
                        "source_page_end": max(source_pages) if source_pages else None,
                        "short_text_policy": "merge_or_skip",
                        "min_child_text_chars": MIN_CHILD_TEXT_CHARS,
                        "skip_embedding_recommended": skip_recommended,
                        "skip_reason": None,
                    },
                    extra={**(parsed.metadata or {})},
                )
                new_chunks.append(
                    _make_chunk(
                        document=document,
                        chunk_id=new_id(),
                        content=content,
                        chunk_index=inserted,
                        file_type=file_type,
                        chunk_type="text",
                        start_at=text_buffer_start_offset + parsed.start,
                        end_at=text_buffer_start_offset + parsed.end,
                        parent_chunk_id=parent_id,
                        context_header=parsed.context_header or parent_context,
                        flags={
                            "structured_parent_child": True,
                            "merged_text_blocks": len(source_blocks) > 1,
                            "skip_embedding_recommended": skip_recommended,
                        },
                        metadata=meta,
                    )
                )
                inserted += 1
            global_offset += len(combined) + 2
            text_buffer_parts = []
            text_buffer_blocks = []
            text_buffer_meta = {}
            text_buffer_start_offset = global_offset

        for block in section.get("blocks") or []:
            block_page_no = _block_page_no(block, block.index_text() or block.text or _block_index_content(block, file_type))
            block_meta = {
                "block_type": block.block_type,
                "block_order": block.order_index,
                **(block.metadata or {}),
            }
            if block_page_no is not None:
                block_meta["page_no"] = block_page_no
                block_meta.setdefault("source_page", block_page_no)
            if block.bbox:
                block_meta["bbox"] = list(block.bbox)

            normalized_type = _normalized_child_type(block)
            neighbor_info = _nearby_text_context(section, block, file_type)
            if neighbor_info.get("bound_caption"):
                block_meta.setdefault("caption", neighbor_info.get("bound_caption"))
                block_meta.setdefault("bound_caption", neighbor_info.get("bound_caption"))

            if normalized_type in {"text", "equation"}:
                raw_text = clean_chunk_content_for_storage(block.text or block.index_text(), chunk_type="text")
                if not raw_text or is_low_information_text(raw_text):
                    skipped_low_info_text_count += 1
                    continue
                if _is_code_block_block(block):
                    flush_text_buffer()
                    code_content = _compose_code_block_content(block)
                    if not code_content:
                        continue
                    code_meta_extra = {
                        "content_kind": "code_block",
                        "has_lead_in": bool((block.metadata or {}).get("lead_in_text")),
                        "has_tail_note": bool((block.metadata or {}).get("tail_note_text")),
                        "lead_in_text": (block.metadata or {}).get("lead_in_text") or "",
                        "tail_note_text": (block.metadata or {}).get("tail_note_text") or "",
                        "merged_into_code_block": bool((block.metadata or {}).get("merged_into_code_block")),
                    }
                    meta = _structured_block_meta(
                        block,
                        parent_id=parent_id,
                        parent_context=parent_context,
                        section=section,
                        block_page_no=block_page_no,
                        block_meta={**block_meta, **code_meta_extra},
                        extra=code_meta_extra,
                    )
                    new_chunks.append(
                        _make_chunk(
                            document=document,
                            chunk_id=new_id(),
                            content=code_content,
                            chunk_index=inserted,
                            file_type=file_type,
                            chunk_type="text",
                            start_at=global_offset,
                            end_at=global_offset + len(code_content),
                            parent_chunk_id=parent_id,
                            context_header=block.context_header or parent_context,
                            flags={
                                "structured_parent_child": True,
                                "content_kind": "code_block",
                                "has_lead_in": code_meta_extra["has_lead_in"],
                                "has_tail_note": code_meta_extra["has_tail_note"],
                            },
                            metadata=meta,
                        )
                    )
                    inserted += 1
                    global_offset += len(code_content) + 2
                    continue
                structure_kind = _structure_content_kind(raw_text, block.context_header or parent_context)
                if structure_kind != "text":
                    flush_text_buffer()
                    parsed_structure_chunks = split_structure_by_lines(
                        raw_text,
                        child_cfg,
                        context_header=block.context_header or parent_context,
                        base_seq=0,
                        metadata={
                            "source": "parser",
                            "strategy": getattr(child_cfg, "strategy", "auto") or "auto",
                            "heading_path": section.get("heading_path") or [],
                            "content_kind": structure_kind,
                            "structure_like": True,
                            "preserve_line_boundaries": True,
                        },
                        source_section=section.get("title") or parent_context,
                    )
                    for parsed in parsed_structure_chunks:
                        content = clean_chunk_content_for_storage(parsed.content, chunk_type="text")
                        if not content:
                            continue
                        meta = _structured_block_meta(
                            block,
                            parent_id=parent_id,
                            parent_context=parent_context,
                            section=section,
                            block_page_no=block_page_no,
                            block_meta={
                                **block_meta,
                                "content_kind": structure_kind,
                                "structure_like": True,
                                "preserve_line_boundaries": True,
                            },
                            extra={**(parsed.metadata or {}), **{k: v for k, v in neighbor_info.items() if v}},
                        )
                        new_chunks.append(
                            _make_chunk(
                                document=document,
                                chunk_id=new_id(),
                                content=content,
                                chunk_index=inserted,
                                file_type=file_type,
                                chunk_type="text",
                                start_at=global_offset + parsed.start,
                                end_at=global_offset + parsed.end,
                                parent_chunk_id=parent_id,
                                context_header=parsed.context_header or block.context_header or parent_context,
                                flags={
                                    "structured_parent_child": True,
                                    "structure_like": True,
                                    "preserve_line_boundaries": True,
                                },
                                metadata=meta,
                            )
                        )
                        inserted += 1
                    global_offset += len(raw_text) + 2
                    continue
                if _is_equation_block(block):
                    flush_text_buffer()
                    equation_text = clean_chunk_content_for_storage(raw_text, chunk_type="text")
                    if not equation_text or is_low_information_text(equation_text):
                        continue
                    meta = _structured_block_meta(
                        block,
                        parent_id=parent_id,
                        parent_context=parent_context,
                        section=section,
                        block_page_no=block_page_no,
                        block_meta=block_meta,
                        extra={"is_equation": True, "subtype": "equation"},
                    )
                    new_chunks.append(
                        _make_chunk(
                            document=document,
                            chunk_id=new_id(),
                            content=equation_text,
                            chunk_index=inserted,
                            file_type=file_type,
                            chunk_type="equation",
                            start_at=global_offset,
                            end_at=global_offset + len(equation_text),
                            parent_chunk_id=parent_id,
                            context_header=block.context_header or parent_context,
                            flags={"structured_parent_child": True, "is_equation": True},
                            metadata=meta,
                        )
                    )
                    inserted += 1
                    global_offset += len(equation_text) + 2
                    continue

                ocr_layers = _ocr_layer_metadata(raw_text, enabled=_looks_like_ocr_text(raw_text, block_meta))
                text_for_buffer = clean_chunk_content_for_storage(ocr_layers.get("ocr_cleaned_text") or raw_text, chunk_type="text")
                if not text_for_buffer or is_low_information_text(text_for_buffer):
                    skipped_low_info_text_count += 1
                    continue
                text_buffer_parts.append(text_for_buffer)
                text_buffer_blocks.append(block)
                text_buffer_meta.update({**block_meta, **ocr_layers, **{k: v for k, v in neighbor_info.items() if v}})
                continue

            flush_text_buffer()

            if normalized_type == "table":
                content = clean_chunk_content_for_storage(_block_index_content(block, file_type), chunk_type="table")
                if not content or should_skip_text_chunk(content, chunk_type="table", metadata=block_meta):
                    # Do not create placeholder-only table chunks such as "表格".
                    # The original table_html/image metadata is not lost; it is
                    # still available in the parsed block, but without semantic
                    # table text it should not become a retrievable/vectorized
                    # evidence chunk.
                    continue
                image_info = {"table_html": block.table_html} if block.table_html else {}
                if block.image_path or block.image_url or block.bbox:
                    image_info.update(_block_image_info(block))
                meta = _structured_block_meta(
                    block,
                    parent_id=parent_id,
                    parent_context=parent_context,
                    section=section,
                    block_page_no=block_page_no,
                    block_meta=block_meta,
                    extra={k: v for k, v in neighbor_info.items() if v},
                )
                new_chunks.append(
                    _make_chunk(
                        document=document,
                        chunk_id=new_id(),
                        content=content,
                        chunk_index=inserted,
                        file_type=file_type,
                        chunk_type="table",
                        start_at=global_offset,
                        end_at=global_offset + len(content),
                        parent_chunk_id=parent_id,
                        context_header=block.context_header or parent_context,
                        image_info=image_info,
                        flags={"structured_parent_child": True, "has_parent_context": True},
                        metadata=meta,
                    )
                )
                inserted += 1
                global_offset += len(content) + 2
                continue

            if normalized_type == "image":
                ocr_text_raw = _block_ocr_text(block)
                ocr_layers = _ocr_layer_metadata(ocr_text_raw, enabled=bool(ocr_text_raw and len(ocr_text_raw.strip()) >= 20))
                ocr_text = clean_chunk_content_for_storage(ocr_layers.get("ocr_cleaned_text") or sanitize_text_for_postgres(ocr_text_raw), chunk_type="text")
                if ocr_text and len(ocr_text.strip()) >= MIN_CHILD_TEXT_CHARS and not is_low_information_text(ocr_text):
                    parsed_ocr_chunks = merge_short_parsed_chunks(split_document(ocr_text, file_type, child_cfg))
                    for parsed in parsed_ocr_chunks:
                        ocr_content = clean_chunk_content_for_storage(parsed.content, chunk_type="text")
                        if not ocr_content or is_low_information_text(ocr_content):
                            continue
                        ocr_meta = _structured_block_meta(
                            block,
                            parent_id=parent_id,
                            parent_context=parent_context,
                            section=section,
                            block_page_no=block_page_no,
                            block_meta=block_meta,
                            extra={
                                **ocr_layers,
                                **{k: v for k, v in neighbor_info.items() if v},
                                **(parsed.metadata or {}),
                                "source": "image_ocr_text",
                                "derived_from_block_type": "image",
                                "derived_image_url": block.image_url,
                                "ocr_text_source": "metadata",
                            },
                        )
                        new_chunks.append(
                            _make_chunk(
                                document=document,
                                chunk_id=new_id(),
                                content=ocr_content,
                                chunk_index=inserted,
                                file_type=file_type,
                                chunk_type="image_ocr_text",
                                start_at=global_offset + parsed.start,
                                end_at=global_offset + parsed.end,
                                parent_chunk_id=parent_id,
                                context_header=parsed.context_header or block.context_header or parent_context,
                                image_info=_block_image_info(block),
                                flags={"is_ocr_text_chunk": True, "derived_from_image": True, "structured_parent_child": True},
                                metadata=ocr_meta,
                            )
                        )
                        inserted += 1
                    global_offset += len(ocr_text) + 2

                content = clean_chunk_content_for_storage(_block_index_content(block, file_type), chunk_type="image")
                placeholder_image = False
                if not content:
                    # Keep the image chunk for UI / citation / image_url usage, but
                    # mark it as non-embedding content.  It can still be returned as
                    # media when related to a useful text hit.
                    content = "图片"
                    placeholder_image = True
                skip_image_embedding = placeholder_image or should_skip_text_chunk(content, chunk_type="image", metadata=block_meta)
                meta = _structured_block_meta(
                    block,
                    parent_id=parent_id,
                    parent_context=parent_context,
                    section=section,
                    block_page_no=block_page_no,
                    block_meta={
                        **block_meta,
                        "skip_embedding_recommended": skip_image_embedding,
                        "low_content_chunk": skip_image_embedding,
                        "skip_reason": "placeholder_image" if skip_image_embedding else None,
                    },
                    extra={**ocr_layers, **{k: v for k, v in neighbor_info.items() if v}},
                )
                new_chunks.append(
                    _make_chunk(
                        document=document,
                        chunk_id=new_id(),
                        content=content,
                        chunk_index=inserted,
                        file_type=file_type,
                        chunk_type="image",
                        start_at=global_offset,
                        end_at=global_offset + len(content),
                        parent_chunk_id=parent_id,
                        context_header=block.context_header or parent_context,
                        image_info=_block_image_info(block),
                        flags={
                            "has_image": True,
                            "has_ocr_text": bool(_block_ocr_text(block)),
                            "has_vlm_description": bool((block.metadata or {}).get("vlm_description")),
                            "index_content_enriched": True,
                            "structured_parent_child": True,
                            "has_parent_context": True,
                            "skip_embedding_recommended": skip_image_embedding,
                            "low_content_chunk": skip_image_embedding,
                            "skip_reason": "placeholder_image" if skip_image_embedding else None,
                        },
                        metadata=meta,
                    )
                )
                inserted += 1
                global_offset += len(content) + 2

        flush_text_buffer()

    return new_chunks, inserted



def _apply_figure_bindings(
    chunks: list["Chunk"],
    plan: Any,
    *,
    enabled: bool = True,
    relation_graph: Any | None = None,
) -> list["Chunk"]:
    """Compatibility wrapper for the Phase 1 occurrence materializer."""
    if not enabled:
        return chunks
    return materialize_figure_bindings(chunks, plan, relation_graph=relation_graph)


def _replace_canonical_artifact(
    db: Session,
    document: Document,
    payload: dict[str, Any],
) -> None:
    """Compatibility wrapper for the Phase 2 persistence adapter."""
    replace_canonical_artifact(db, document, payload)


def rebuild_document_chunks(
    db: Session,
    document: Document,
    text: str | ParsedDocument,
    *,
    cfg_override=None,
    file_type_override: str | None = None,
) -> int:
    file_type = sanitize_text_for_postgres(
        (file_type_override or document.file_type).lower().lstrip(".")
    )
    cfg = cfg_override or runtime_config_from_db(db)
    parsed_document = text if isinstance(text, ParsedDocument) else ParsedDocument.from_text(sanitize_text_for_postgres(text))
    blocks = parsed_document.blocks or [ParsedBlock(block_type="text", text=parsed_document.content, order_index=0)]

    new_chunks: list[Chunk] = []
    inserted = 0
    global_offset = 0
    # Canonical Markdown is the single production splitting input for every
    # format.  The setting controls parent materialization only; it no longer
    # selects a completely different parsing/chunking algorithm.
    use_canonical_parent_child = True

    canonical_artifact: dict[str, Any] = {}
    new_chunks, inserted = _rebuild_canonical_weknora_parent_child(
        document,
        parsed_document,
        file_type,
        cfg,
        artifact_out=canonical_artifact,
    )
    if not use_canonical_parent_child:  # retained legacy implementation for compatibility only
        for block in sorted(blocks, key=lambda item: item.order_index):
            block_page_no = _block_page_no(block, block.index_text() or block.text)
            block_meta = {
                "block_type": block.block_type,
                "block_order": block.order_index,
                **(block.metadata or {}),
            }
            if block_page_no is not None:
                block_meta["page_no"] = block_page_no
                block_meta.setdefault("source_page", block_page_no)
            if block.bbox:
                block_meta["bbox"] = list(block.bbox)

            if block.block_type == "text":
                block_text_raw = sanitize_text_for_postgres(block.text)
                if not block_text_raw:
                    continue
                ocr_layers = _ocr_layer_metadata(
                    block_text_raw,
                    enabled=_looks_like_ocr_text(block_text_raw, block_meta),
                )
                # content uses light-cleaned OCR if present, never the aggressive normalized text
                block_text = ocr_layers.get("ocr_cleaned_text") or block_text_raw
                parsed_chunks = split_document(block_text, file_type, cfg)
                parent_ids: dict[int, str] = {}
                for parsed in parsed_chunks:
                    if parsed.chunk_type == "parent_text":
                        parent_ids[parsed.seq] = new_id()
                for parsed in parsed_chunks:
                    content = sanitize_text_for_postgres(parsed.content)
                    if not content:
                        continue
                    chunk_id = parent_ids.get(parsed.seq) if parsed.chunk_type == "parent_text" else new_id()
                    parsed_meta = parsed.metadata or {}
                    structure_like = bool(parsed_meta.get("structure_like")) or _structure_content_kind(content, parsed.context_header or block.context_header) != "text"
                    new_chunks.append(
                        _make_chunk(
                            document=document,
                            chunk_id=chunk_id or new_id(),
                            content=content,
                            chunk_index=inserted,
                            file_type=file_type,
                            chunk_type=parsed.chunk_type or "text",
                            start_at=global_offset + parsed.start,
                            end_at=global_offset + parsed.end,
                            parent_chunk_id=parent_ids.get(parsed.parent_index) if parsed.parent_index is not None else None,
                            context_header=parsed.context_header or block.context_header or "",
                            flags={
                                "structure_like": structure_like,
                                "preserve_line_boundaries": bool(parsed_meta.get("preserve_line_boundaries")) or structure_like,
                            } if structure_like else None,
                            metadata={**block_meta, **ocr_layers, **(parsed.metadata or {})},
                        )
                    )
                    inserted += 1
                global_offset += len(block_text) + 2
                continue

            if block.block_type == "table":
                content = _block_index_content(block, file_type)
                if not content:
                    continue
                image_info = {"table_html": block.table_html} if block.table_html else {}
                if block.image_path or block.image_url or block.bbox:
                    image_info.update(_block_image_info(block))
                new_chunks.append(
                    _make_chunk(
                        document=document,
                        chunk_id=new_id(),
                        content=content,
                        chunk_index=inserted,
                        file_type=file_type,
                        chunk_type="table",
                        start_at=global_offset,
                        end_at=global_offset + len(content),
                        context_header=block.context_header,
                        image_info=image_info,
                        metadata=block_meta,
                    )
                )
                inserted += 1
                global_offset += len(content) + 2
                continue

            if block.block_type == "image":
                ocr_text_raw = _block_ocr_text(block)
                ocr_layers = _ocr_layer_metadata(ocr_text_raw, enabled=bool(ocr_text_raw and len(ocr_text_raw.strip()) >= 20))
                ocr_text = ocr_layers.get("ocr_cleaned_text") or sanitize_text_for_postgres(ocr_text_raw)
                if ocr_text and len(ocr_text.strip()) >= 20:
                    # Scanned PDFs need a real text chunk for exact keyword/id search.
                    # Keep the image chunk for visual/VLM retrieval, but do not rely on
                    # a VLM summary as the only searchable content.
                    # Aggressive OCR normalization is stored only in metadata and is
                    # never used to overwrite chunk.content.
                    parsed_ocr_chunks = split_document(ocr_text, file_type, cfg)
                    parent_ids: dict[int, str] = {}
                    for parsed in parsed_ocr_chunks:
                        if parsed.chunk_type == "parent_text":
                            parent_ids[parsed.seq] = new_id()
                    for parsed in parsed_ocr_chunks:
                        ocr_content = sanitize_text_for_postgres(parsed.content)
                        if not ocr_content:
                            continue
                        chunk_id = parent_ids.get(parsed.seq) if parsed.chunk_type == "parent_text" else new_id()
                        ocr_meta = {
                            **block_meta,
                            **ocr_layers,
                            "source": "image_ocr_text",
                            "derived_from_block_type": "image",
                            "derived_image_url": block.image_url,
                            "ocr_text_source": "metadata",
                        }
                        new_chunks.append(
                            _make_chunk(
                                document=document,
                                chunk_id=chunk_id or new_id(),
                                content=ocr_content,
                                chunk_index=inserted,
                                file_type=file_type,
                                chunk_type="image_ocr_text" if (parsed.chunk_type or "text") == "text" else parsed.chunk_type,
                                start_at=global_offset + parsed.start,
                                end_at=global_offset + parsed.end,
                                parent_chunk_id=parent_ids.get(parsed.parent_index) if parsed.parent_index is not None else None,
                                context_header=parsed.context_header or block.context_header or "OCR 文本",
                                image_info=_block_image_info(block),
                                flags={"is_ocr_text_chunk": True, "derived_from_image": True},
                                metadata={**ocr_meta, **(parsed.metadata or {})},
                            )
                        )
                        inserted += 1
                    global_offset += len(ocr_text) + 2

                content = _block_index_content(block, file_type)
                new_chunks.append(
                    _make_chunk(
                        document=document,
                        chunk_id=new_id(),
                        content=content,
                        chunk_index=inserted,
                        file_type=file_type,
                        chunk_type="image",
                        start_at=global_offset,
                        end_at=global_offset + len(content),
                        context_header=block.context_header,
                        image_info=_block_image_info(block),
                        flags={
                            "has_image": True,
                            "has_ocr_text": bool(_block_ocr_text(block)),
                            "has_vlm_description": bool((block.metadata or {}).get("vlm_description")),
                            "index_content_enriched": True,
                        },
                        metadata={**block_meta, **ocr_layers},
                    )
                )
                inserted += 1
                global_offset += len(content) + 2

    # Canonical parent-child planning already removes structural-only chunks,
    # materializes image assets, and enforces the final >=2-child parent rule.
    # The legacy orphan postprocessor is intentionally skipped here because it
    # can delete a valid child after parent planning and recreate 0/1-child
    # parents. Non-parent-child mode retains the previous cleanup behavior.
    if not use_canonical_parent_child:
        new_chunks = _post_process_orphan_short_text_chunks(new_chunks)
    else:
        new_chunks = _enforce_parent_child_integrity(new_chunks)
        inserted = len(new_chunks)

    if not new_chunks:
        # Build and validate before replacing persisted data.  A zero-chunk
        # result is never a successful replacement for an existing document.
        raise ValueError("文档切分结果为空，已保留原有切片")

    _link_neighbors(new_chunks, {"text"})
    _link_neighbors(new_chunks, {"parent_text"})
    _link_neighbors(new_chunks, {"text", "table", "image", "image_ocr_text", "equation"})

    # Replacement starts only after the complete new chunk set has passed all
    # quality and parent/child integrity checks.  The caller owns the DB
    # transaction, so any later failure restores the previous rows.
    db.query(ChunkEmbedding).filter(ChunkEmbedding.document_id == document.id).delete(synchronize_session=False)
    db.query(Chunk).filter(Chunk.document_id == document.id).delete(synchronize_session=False)
    _replace_canonical_artifact(db, document, canonical_artifact)

    for chunk in new_chunks:
        if isinstance(chunk, Chunk):
            _finalize_chunk_skip_flags(chunk)
        db.add(chunk)
    db.flush()

    update_knowledge_base_chunk_count(db, document.knowledge_base_id)
    return inserted


def get_chunk_tree(db: Session, doc_id: str):
    document = db.get(Document, doc_id)
    if document is None:
        return error_response(f"文档不存在：{doc_id}")

    chunks = db.scalars(
        select(Chunk)
        .where(Chunk.document_id == doc_id)
        .order_by(Chunk.chunk_index.asc())
    ).all()

    parent_nodes: list[dict] = []
    parent_map: dict[str, dict] = {}
    orphan_children: list[dict] = []
    type_counts: dict[str, int] = {}

    for chunk in chunks:
        ctype = chunk.chunk_type or "text"
        type_counts[ctype] = type_counts.get(ctype, 0) + 1

    for chunk in chunks:
        if chunk.chunk_type == "parent_text":
            node = {
                "parent": chunk_to_dict(chunk, include_content=False),
                "children": [],
                "child_type_counts": {},
            }
            parent_nodes.append(node)
            parent_map[chunk.id] = node

    for chunk in chunks:
        if chunk.chunk_type == "parent_text":
            continue
        item = chunk_to_dict(chunk, include_content=False)
        parent_id = chunk.parent_chunk_id or ""
        node = parent_map.get(parent_id)
        if node is None:
            orphan_children.append(item)
            continue
        node["children"].append(item)
        ctype = chunk.chunk_type or "text"
        node["child_type_counts"][ctype] = node["child_type_counts"].get(ctype, 0) + 1

    for node in parent_nodes:
        node["children"].sort(key=lambda value: int(value.get("chunk_index") or 0))
        node["child_count"] = len(node["children"])
        node["has_image_child"] = bool(node["child_type_counts"].get("image"))
        node["has_table_child"] = bool(node["child_type_counts"].get("table"))
        node["has_equation_child"] = any(
            child.get("metadata", {}).get("is_equation") is True or child.get("metadata", {}).get("subtype") == "equation"
            for child in node["children"]
        )

    return success_response(
        {
            "document_id": doc_id,
            "items": parent_nodes,
            "orphans": orphan_children,
            "stats": {
                "total_chunks": len(chunks),
                "parent_count": len(parent_nodes),
                "orphan_count": len(orphan_children),
                "chunk_type_counts": type_counts,
            },
        }
    )

def list_chunks(
    db: Session,
    doc_id: str,
    page: int = 1,
    page_size: int = 20,
    *,
    chunk_type: str = "all",
    index_status: str = "all",
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    document = db.get(Document, doc_id)
    if document is None:
        return error_response(f"文档不存在：{doc_id}")

    filters = [Chunk.document_id == doc_id]
    normalized_type = (chunk_type or "all").strip().lower()
    if normalized_type and normalized_type != "all":
        filters.append(Chunk.chunk_type == normalized_type)

    normalized_index = (index_status or "all").strip().lower()
    embedding_exists = exists().where(
        ChunkEmbedding.chunk_id == Chunk.id,
        ChunkEmbedding.source_type == "chunk",
        ChunkEmbedding.is_enabled.is_(True),
    )
    skip_expr = (Chunk.metadata_json.op("->>")("skip_embedding_recommended") == "true")
    if normalized_index in {"indexed", "missing", "skipped", "pending"}:
        if normalized_index == "indexed":
            filters.append(embedding_exists)
        elif normalized_index == "missing":
            filters.append(~embedding_exists)
        elif normalized_index == "skipped":
            filters.append(~embedding_exists)
            filters.append(skip_expr)
        elif normalized_index == "pending":
            filters.append(~embedding_exists)
            filters.append(~skip_expr)

    total = db.scalar(select(func.count()).select_from(Chunk).where(*filters)) or 0
    items = db.scalars(
        select(Chunk)
        .where(*filters)
        .order_by(Chunk.chunk_index.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return success_response(
        {
            "document_id": doc_id,
            "items": [chunk_to_dict(item, include_content=True) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    )


def get_chunk(db: Session, chunk_id: str):
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        return error_response(f"切片不存在：{chunk_id}")
    return success_response(chunk_to_dict(chunk, include_content=True))


def set_chunk_enabled(db: Session, actor: User, chunk_id: str, enabled: bool):
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        return error_response(f"切片不存在：{chunk_id}")
    try:
        chunk.is_enabled = enabled
        chunk.status = "ready" if enabled else "disabled"
        db.query(ChunkEmbedding).filter(ChunkEmbedding.chunk_id == chunk.id).update(
            {"is_enabled": enabled},
            synchronize_session=False,
        )
        record_audit(
            db,
            actor,
            "enable_chunk" if enabled else "disable_chunk",
            "chunk",
            chunk.id,
            result_summary={
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "knowledge_base_id": chunk.knowledge_base_id,
                "status": chunk.status,
            },
        )
        db.commit()
        db.refresh(chunk)
    except Exception:
        db.rollback()
        return error_response("切片状态更新失败")
    return success_response(chunk_to_dict(chunk), "切片已启用" if enabled else "切片已禁用")


def delete_chunk(db: Session, actor: User, chunk_id: str):
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        return error_response(f"切片不存在：{chunk_id}")
    knowledge_base_id = chunk.knowledge_base_id
    document_id = chunk.document_id
    try:
        db.delete(chunk)
        record_audit(
            db,
            actor,
            "delete_chunk",
            "chunk",
            chunk_id,
            result_summary={
                "chunk_id": chunk_id,
                "document_id": document_id,
                "knowledge_base_id": knowledge_base_id,
            },
        )
        db.flush()
        update_knowledge_base_chunk_count(db, knowledge_base_id)
        db.commit()
    except Exception:
        db.rollback()
        return error_response("切片删除失败")
    return success_response({"id": chunk_id, "deleted": True}, "切片已删除")
