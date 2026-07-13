"""Build child chunk drafts for structured parent-child rebuilds."""

from __future__ import annotations

import re
from typing import Any

from app.services.chunker import split_document
from app.services.chunker.models import ChunkerConfig
from app.services.chunker.protected_patterns import is_structure_like_text, looks_like_directory_tree
from app.services.chunker.recursive_splitter import split_structure_by_lines
from app.services.chunking.chunk_draft import ChildDraft, ParentDraft
from app.services.chunking.code_context_merger import compose_code_block_content, is_code_block_block
from app.services.chunking.image_chunk_builder import build_image_child, build_image_ocr_children
from app.services.chunking.metadata_builder import (
    build_code_block_metadata,
    build_equation_metadata,
    build_text_child_metadata,
)
from app.services.chunking.section_builder import (
    _block_index_content,
    block_page_no,
    block_text_for_context,
    is_noise_block,
    normalized_child_type,
    raw_block_type,
)
from app.services.chunking.table_chunk_builder import build_table_child
from app.services.content_cleaning import (
    MIN_CHILD_TEXT_CHARS,
    clean_chunk_content_for_storage,
    is_low_information_text,
)
from app.services.ocr import layered_ocr_metadata
from app.utils.text import sanitize_text_for_postgres


_IMAGE_LIKE_RAW_TYPES = {"image", "figure", "fig", "chart", "graph", "plot"}
_CAPTION_KEYWORDS = {
    "image": ("图", "figure", "fig.", "fig ", "chart", "plot", "graph"),
    "table": ("表", "table"),
    "equation": ("公式", "equation", "formula", "eq."),
}


def _structure_content_kind(text: str, context_header: str = "") -> str:
    source = "\n".join(part for part in [context_header, text] if part)
    if not is_structure_like_text(source):
        return "text"
    lowered = source.lower()
    if any(marker in lowered for marker in ("项目结构", "目录结构", "backend/", "frontend/", "app/services", "核心服务")):
        return "project_structure"
    if looks_like_directory_tree(text):
        return "directory_tree"
    return "code_structure"


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


def _looks_like_ocr_text(text: str, metadata: dict[str, Any] | None = None) -> bool:
    value = sanitize_text_for_postgres(text or "")
    meta = metadata or {}
    if not value:
        return False
    source = "\n".join([value, " ".join(_walk_json_strings(meta)) if isinstance(meta, dict) else ""])
    lines = value.splitlines()
    short_lines = sum(1 for line in lines if 3 <= len(line.strip()) <= 40)
    total_non_empty = sum(1 for line in lines if line.strip())
    if total_non_empty >= 5 and short_lines / max(total_non_empty, 1) > 0.6:
        return True

    from app.services.ocr.templates import _TEMPLATES

    for cls in _TEMPLATES.values():
        instance = cls()
        for pattern in instance.looks_like_ocr_patterns():
            if re.search(pattern, source, flags=re.I):
                return True
    legacy = [
        r"invoice\s*number",
        r"invoicenumber",
        r"INV[-_ ]?[0-9O0U]{2,4}[-_ ]?[0-9O0U]{1,3}",
    ]
    return any(re.search(pattern, source, flags=re.I) for pattern in legacy)


def _ocr_layer_metadata(raw_text: str, *, enabled: bool = False) -> dict[str, str]:
    return layered_ocr_metadata(raw_text, enabled=enabled)


def _child_split_config(cfg: Any) -> ChunkerConfig:
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


def _caption_kind_for_block(block: Any) -> str:
    rtype = raw_block_type(block)
    if normalized_child_type(block) == "table":
        return "table"
    if normalized_child_type(block) == "image":
        return "image"
    if normalized_child_type(block) == "equation":
        return "equation"
    return "text"


def _looks_like_caption(text_value: str, kind: str) -> bool:
    from app.services.chunker.heading_utils import is_caption_text

    value = sanitize_text_for_postgres(text_value or "").strip()
    if not value or len(value) > 260:
        return False
    if is_caption_text(value):
        return True
    lowered = value.lower()
    return any(keyword in lowered for keyword in _CAPTION_KEYWORDS.get(kind, ()))


def nearby_text_context(section: dict[str, Any], block: Any, file_type: str, *, window: int = 2) -> dict[str, Any]:
    blocks = section.get("blocks") or []
    try:
        index = blocks.index(block)
    except ValueError:
        return {}

    kind = _caption_kind_for_block(block)
    before_items: list[Any] = []
    after_items: list[Any] = []
    for candidate in reversed(blocks[max(0, index - window):index]):
        if candidate is block or is_noise_block(candidate):
            continue
        if block_text_for_context(candidate, file_type):
            before_items.append(candidate)
    before_items = list(reversed(before_items))
    for candidate in blocks[index + 1:index + 1 + window]:
        if candidate is block or is_noise_block(candidate):
            continue
        if block_text_for_context(candidate, file_type):
            after_items.append(candidate)

    caption_block: Any | None = None
    for candidate in [*after_items, *reversed(before_items)]:
        text_value = block_text_for_context(candidate, file_type)
        if _looks_like_caption(text_value, kind):
            caption_block = candidate
            break

    before_text = sanitize_text_for_postgres("\n".join(block_text_for_context(item, file_type) for item in before_items if block_text_for_context(item, file_type)))
    after_text = sanitize_text_for_postgres("\n".join(block_text_for_context(item, file_type) for item in after_items if block_text_for_context(item, file_type)))
    caption_text = block_text_for_context(caption_block, file_type) if caption_block else ""
    return {
        "nearby_context_before": before_text[:800],
        "nearby_context_after": after_text[:800],
        "bound_caption": sanitize_text_for_postgres(caption_text),
        "bound_caption_block_order": getattr(caption_block, "order_index", None) if caption_block else None,
        "bound_caption_kind": kind if caption_block else "",
    }


def _block_meta(block: Any, page: int | None) -> dict[str, Any]:
    meta = {
        "block_type": getattr(block, "block_type", "text"),
        "block_order": getattr(block, "order_index", 0),
        "raw_block_type": raw_block_type(block),
        **(getattr(block, "metadata", None) or {}),
    }
    if page is not None:
        meta["page_no"] = page
        meta.setdefault("source_page", page)
    if getattr(block, "bbox", None):
        meta["bbox"] = list(getattr(block, "bbox"))
    return meta


def merge_short_parsed_chunks(parsed_chunks: list[Any]) -> list[Any]:
    merged: list[Any] = []
    for parsed in parsed_chunks:
        content = clean_chunk_content_for_storage(parsed.content, chunk_type="text")
        if not content or is_low_information_text(content):
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


class ChildBuilder:
    """Convert SectionDraft-like dicts plus ParentDraft into ChildDrafts."""

    def __init__(self, cfg: Any, file_type: str):
        self.cfg = cfg
        self.file_type = file_type
        self.child_cfg = _child_split_config(cfg)

    def build_children(self, *, section: dict[str, Any], parent: ParentDraft, start_offset: int = 0) -> tuple[list[ChildDraft], int]:
        children: list[ChildDraft] = []
        global_offset = start_offset
        text_buffer_parts: list[str] = []
        text_buffer_blocks: list[Any] = []
        text_buffer_meta: dict[str, Any] = {}
        text_buffer_start_offset = global_offset

        def flush_text_buffer() -> None:
            nonlocal global_offset, text_buffer_parts, text_buffer_blocks, text_buffer_meta, text_buffer_start_offset
            if not text_buffer_parts:
                return
            combined = clean_chunk_content_for_storage("\n\n".join(text_buffer_parts), chunk_type="text")
            source_blocks = list(text_buffer_blocks)
            source_orders = [getattr(block, "order_index", 0) for block in source_blocks]
            if not combined or is_low_information_text(combined):
                text_buffer_parts = []
                text_buffer_blocks = []
                text_buffer_meta = {}
                text_buffer_start_offset = global_offset
                return

            for parsed in merge_short_parsed_chunks(split_document(combined, self.file_type, self.child_cfg)):
                content = clean_chunk_content_for_storage(parsed.content, chunk_type="text")
                if not content or is_low_information_text(content):
                    continue
                skip_recommended = False
                first_block = source_blocks[0] if source_blocks else None
                first_page = block_page_no(first_block, getattr(first_block, "index_text", lambda: "")() or getattr(first_block, "text", "") or content) if first_block else None
                source_pages = [
                    block_page_no(block, getattr(block, "index_text", lambda: "")() or getattr(block, "text", "") or "")
                    for block in source_blocks
                ]
                source_pages = [page for page in source_pages if page is not None]
                meta = build_text_child_metadata(
                    parent_id=parent.draft_ref,
                    parent_context=parent.context_header or "",
                    section=section,
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
                    block_page_no=first_page,
                    extra={**(parsed.metadata or {})},
                )
                children.append(
                    ChildDraft(
                        content=content,
                        chunk_type="text",
                        parent_ref=parent.draft_ref,
                        start_at=text_buffer_start_offset + parsed.start,
                        end_at=text_buffer_start_offset + parsed.end,
                        context_header=parsed.context_header or parent.context_header,
                        flags={
                            "structured_parent_child": True,
                            "merged_text_blocks": len(source_blocks) > 1,
                            "skip_embedding_recommended": skip_recommended,
                        },
                        metadata=meta,
                        page_no=first_page,
                        source_block_order=source_orders[0] if source_orders else 0,
                        source_block_orders=source_orders,
                        merged_text_blocks=len(source_blocks) > 1,
                        skip_embedding_recommended=skip_recommended,
                    )
                )
            global_offset += len(combined) + 2
            text_buffer_parts = []
            text_buffer_blocks = []
            text_buffer_meta = {}
            text_buffer_start_offset = global_offset

        for block in section.get("blocks") or []:
            page = block_page_no(block, getattr(block, "index_text", lambda: "")() or getattr(block, "text", "") or _block_index_content(block, self.file_type))
            meta = _block_meta(block, page)
            neighbor_info = nearby_text_context(section, block, self.file_type)
            if neighbor_info.get("bound_caption"):
                meta.setdefault("caption", neighbor_info.get("bound_caption"))
                meta.setdefault("bound_caption", neighbor_info.get("bound_caption"))
            ntype = normalized_child_type(block)

            if ntype in {"text", "equation"}:
                raw_text = clean_chunk_content_for_storage(getattr(block, "text", "") or getattr(block, "index_text", lambda: "")(), chunk_type="text")
                if not raw_text or (ntype == "text" and is_low_information_text(raw_text)):
                    continue

                if is_code_block_block(block):
                    flush_text_buffer()
                    code_content = compose_code_block_content(block)
                    if not code_content:
                        continue
                    code_extra = {
                        "content_kind": "code_block",
                        "has_lead_in": bool((getattr(block, "metadata", None) or {}).get("lead_in_text")),
                        "has_tail_note": bool((getattr(block, "metadata", None) or {}).get("tail_note_text")),
                        "lead_in_text": (getattr(block, "metadata", None) or {}).get("lead_in_text") or "",
                        "tail_note_text": (getattr(block, "metadata", None) or {}).get("tail_note_text") or "",
                        "merged_into_code_block": bool((getattr(block, "metadata", None) or {}).get("merged_into_code_block")),
                    }
                    code_meta = build_code_block_metadata(
                        parent_id=parent.draft_ref,
                        parent_context=parent.context_header or "",
                        section=section,
                        block_meta={**meta, **code_extra},
                        block_page_no=page,
                        **code_extra,
                    )
                    children.append(
                        ChildDraft(
                            content=code_content,
                            chunk_type="text",
                            parent_ref=parent.draft_ref,
                            start_at=global_offset,
                            end_at=global_offset + len(code_content),
                            context_header=getattr(block, "context_header", "") or parent.context_header,
                            flags={"structured_parent_child": True, "content_kind": "code_block", "has_lead_in": code_extra["has_lead_in"], "has_tail_note": code_extra["has_tail_note"]},
                            metadata=code_meta,
                            page_no=page,
                            bbox=list(getattr(block, "bbox", None) or []) or None,
                            source_block_order=getattr(block, "order_index", 0),
                            source_block_orders=[getattr(block, "order_index", 0)],
                            normalized_block_type="text",
                            raw_block_type=raw_block_type(block),
                        )
                    )
                    global_offset += len(code_content) + 2
                    continue

                structure_kind = _structure_content_kind(raw_text, getattr(block, "context_header", "") or parent.context_header or "")
                if structure_kind != "text":
                    flush_text_buffer()
                    parsed_structure_chunks = split_structure_by_lines(
                        raw_text,
                        self.child_cfg,
                        context_header=getattr(block, "context_header", "") or parent.context_header or "",
                        base_seq=0,
                        metadata={
                            "source": "parser",
                            "strategy": getattr(self.child_cfg, "strategy", "auto") or "auto",
                            "heading_path": section.get("heading_path") or [],
                            "content_kind": structure_kind,
                            "structure_like": True,
                            "preserve_line_boundaries": True,
                        },
                        source_section=section.get("title") or parent.context_header,
                    )
                    for parsed in parsed_structure_chunks:
                        content = clean_chunk_content_for_storage(parsed.content, chunk_type="text")
                        if not content:
                            continue
                        structure_meta = build_text_child_metadata(
                            parent_id=parent.draft_ref,
                            parent_context=parent.context_header or "",
                            section=section,
                            block_meta={**meta, "content_kind": structure_kind, "structure_like": True, "preserve_line_boundaries": True},
                            block_page_no=page,
                            extra={**(parsed.metadata or {}), **{k: v for k, v in neighbor_info.items() if v}},
                        )
                        children.append(
                            ChildDraft(
                                content=content,
                                chunk_type="text",
                                parent_ref=parent.draft_ref,
                                start_at=global_offset + parsed.start,
                                end_at=global_offset + parsed.end,
                                context_header=parsed.context_header or getattr(block, "context_header", "") or parent.context_header,
                                flags={"structured_parent_child": True, "structure_like": True, "preserve_line_boundaries": True},
                                metadata=structure_meta,
                                page_no=page,
                                bbox=list(getattr(block, "bbox", None) or []) or None,
                                source_block_order=getattr(block, "order_index", 0),
                                source_block_orders=[getattr(block, "order_index", 0)],
                            )
                        )
                    global_offset += len(raw_text) + 2
                    continue

                if ntype == "equation":
                    flush_text_buffer()
                    equation_meta = build_equation_metadata(
                        parent_id=parent.draft_ref,
                        parent_context=parent.context_header or "",
                        section=section,
                        block_meta=meta,
                        block_page_no=page,
                        bbox=list(getattr(block, "bbox", None) or []) or None,
                        extra={"is_equation": True, "subtype": "equation"},
                    )
                    children.append(
                        ChildDraft(
                            content=raw_text,
                            chunk_type="equation",
                            parent_ref=parent.draft_ref,
                            start_at=global_offset,
                            end_at=global_offset + len(raw_text),
                            context_header=getattr(block, "context_header", "") or parent.context_header,
                            flags={"structured_parent_child": True, "is_equation": True},
                            metadata=equation_meta,
                            page_no=page,
                            bbox=list(getattr(block, "bbox", None) or []) or None,
                            source_block_order=getattr(block, "order_index", 0),
                            source_block_orders=[getattr(block, "order_index", 0)],
                            normalized_block_type="equation",
                            raw_block_type=raw_block_type(block),
                        )
                    )
                    global_offset += len(raw_text) + 2
                    continue

                ocr_layers = _ocr_layer_metadata(raw_text, enabled=_looks_like_ocr_text(raw_text, meta))
                text_for_buffer = clean_chunk_content_for_storage(ocr_layers.get("ocr_cleaned_text") or raw_text, chunk_type="text")
                if not text_for_buffer or is_low_information_text(text_for_buffer):
                    continue
                text_buffer_parts.append(text_for_buffer)
                text_buffer_blocks.append(block)
                text_buffer_meta.update({**meta, **ocr_layers, **{k: v for k, v in neighbor_info.items() if v}})
                continue

            flush_text_buffer()

            if ntype == "table":
                child = build_table_child(
                    block=block,
                    section=section,
                    parent=parent,
                    file_type=self.file_type,
                    block_meta=meta,
                    neighbor_info=neighbor_info,
                    start_at=global_offset,
                )
                if child is not None:
                    children.append(child)
                    global_offset += len(child.content) + 2
                continue

            if ntype == "image":
                ocr_text_raw = _block_index_content(block, self.file_type)
                from app.services.chunking.section_builder import _block_ocr_text

                ocr_raw = _block_ocr_text(block)
                ocr_layers = _ocr_layer_metadata(ocr_raw, enabled=bool(ocr_raw and len(ocr_raw.strip()) >= 20))
                ocr_children, consumed = build_image_ocr_children(
                    block=block,
                    section=section,
                    parent=parent,
                    file_type=self.file_type,
                    child_cfg=self.child_cfg,
                    block_meta=meta,
                    ocr_layers=ocr_layers,
                    neighbor_info=neighbor_info,
                    merge_short_parsed_chunks=merge_short_parsed_chunks,
                    start_at=global_offset,
                )
                children.extend(ocr_children)
                global_offset += consumed
                image_child = build_image_child(
                    block=block,
                    section=section,
                    parent=parent,
                    file_type=self.file_type,
                    block_meta=meta,
                    ocr_layers=ocr_layers,
                    neighbor_info=neighbor_info,
                    start_at=global_offset,
                )
                children.append(image_child)
                global_offset += len(image_child.content) + 2

        flush_text_buffer()
        return children, global_offset
