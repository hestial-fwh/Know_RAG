"""Unified metadata and flags construction for chunk drafts.

All functions are pure — no database dependency.  They produce dicts that
can be persisted directly in Chunk.metadata_json / Chunk.flags.
"""

from __future__ import annotations

from typing import Any

from app.services.chunking.chunk_draft import SectionDraft

# ---------------------------------------------------------------------------
# Parent metadata
# ---------------------------------------------------------------------------


def build_parent_metadata(
    *,
    section: dict[str, Any],
    section_index: int,
    file_type: str,
    child_types: list[str],
    page_start: int | None = None,
    page_end: int | None = None,
    skipped_heading_child_count: int = 0,
    skipped_low_info_text_count: int = 0,
    min_child_text_chars: int = 120,
    chunk_strategy: str = "auto",
    token_limit: int = 0,
    languages: list[str] | None = None,
    separators: list[str] | None = None,
) -> dict[str, Any]:
    """Build metadata dict for a parent_text chunk.

    Args:
        section: The SectionDraft (dict form) from section_builder.
        section_index: 0-based index within the document.
        file_type: Original file extension (e.g. "pdf", "docx").
        child_types: List of normalized child types (text/image/table/equation).
    """
    return {
        "source": "parser",
        "parser": "structured_parent_child",
        "file_type": file_type,
        "chunk_role": "parent",
        "parent_strategy": section.get("strategy") or "synthetic",
        "section_index": section_index,
        "heading_path": section.get("heading_path") or [],
        "page_start": page_start,
        "page_end": page_end,
        "child_block_orders": [b.order_index for b in (section.get("blocks") or [])],
        "child_types": sorted(set(child_types)),
        "children_count_planned": len(section.get("blocks") or []),
        "skipped_heading_child_count": skipped_heading_child_count,
        "skipped_low_info_text_count": skipped_low_info_text_count,
        "short_text_policy": "merge_or_skip",
        "min_child_text_chars": min_child_text_chars,
        "chunk_strategy": chunk_strategy,
        "token_limit": token_limit,
        "languages": languages or [],
        "separators": separators or [],
    }


def build_parent_flags(child_types: list[str]) -> dict[str, Any]:
    """Build flags dict for a parent_text chunk."""
    return {
        "is_parent_chunk": True,
        "structured_parent_child": True,
        "has_image_child": any(t in {"image", "chart"} for t in child_types),
        "has_table_child": "table" in child_types,
        "has_equation_child": "equation" in child_types,
    }


# ---------------------------------------------------------------------------
# Base chunk metadata (used by both parent and child)
# ---------------------------------------------------------------------------


def build_base_metadata(
    *,
    file_type: str,
    chunk_type: str,
    source: str = "parser",
) -> dict[str, Any]:
    """Build the minimal base metadata for any chunk type.

    This replaces the inline dict in _make_chunk():
        {"source": "parser", "parser": "...", "file_type": file_type}
    """
    return {
        "source": source,
        "parser": "multimodal" if chunk_type in {"image", "table"} else "basic",
        "file_type": file_type,
    }


# ---------------------------------------------------------------------------
# Child metadata
# ---------------------------------------------------------------------------


def build_child_metadata(
    *,
    block_meta: dict[str, Any],
    parent_id: str,
    parent_context: str,
    section: dict[str, Any],
    block_page_no: int | None = None,
    normalized_block_type: str = "text",
    raw_block_type: str = "",
    bbox: list[float] | None = None,
    is_equation: bool = False,
    visual_subtype: str = "",
    content_kind: str = "",
    structure_like: bool = False,
    preserve_line_boundaries: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build metadata dict for a child chunk.

    Consolidates the logic from _structured_block_meta() into a pure function
    with explicit parameters instead of relying on ParsedBlock internals.
    """
    meta: dict[str, Any] = {
        **block_meta,
        "chunk_role": "child",
        "parent_chunk_id": parent_id,
        "parent_context_header": parent_context,
        "parent_strategy": section.get("strategy") or "synthetic",
        "heading_path": section.get("heading_path") or [],
        "raw_block_type": raw_block_type,
        "normalized_block_type": normalized_block_type,
        "source_block_order": block_meta.get("block_order"),
        "page_start": section.get("page_start"),
        "page_end": section.get("page_end"),
    }

    if block_page_no is not None:
        meta["page_no"] = block_page_no
        meta.setdefault("source_page", block_page_no)

    if bbox:
        meta["bbox"] = list(bbox)

    if is_equation:
        meta["subtype"] = "equation"
        meta["is_equation"] = True

    if visual_subtype:
        meta["visual_subtype"] = visual_subtype

    if content_kind and content_kind != "text":
        meta["content_kind"] = content_kind
        if structure_like:
            meta["structure_like"] = True
            meta["preserve_line_boundaries"] = preserve_line_boundaries

    if extra:
        meta.update(extra)

    return meta


# ---------------------------------------------------------------------------
# Text child metadata (merged from multiple blocks)
# ---------------------------------------------------------------------------


def build_merged_text_child_metadata(
    *,
    parent_id: str,
    parent_context: str,
    section: dict[str, Any],
    source_blocks_meta: dict[str, Any],
    source_block_orders: list[int],
    merged_text_blocks: bool = False,
    source_page_start: int | None = None,
    source_page_end: int | None = None,
    skip_embedding_recommended: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build metadata for a text child derived from one or more ParsedBlocks."""
    meta: dict[str, Any] = {
        **source_blocks_meta,
        "chunk_role": "child",
        "parent_chunk_id": parent_id,
        "parent_context_header": parent_context,
        "parent_strategy": section.get("strategy") or "synthetic",
        "heading_path": section.get("heading_path") or [],
        "source_block_orders": source_block_orders,
        "merged_text_blocks": merged_text_blocks,
        "source_page_start": source_page_start,
        "source_page_end": source_page_end,
        "short_text_policy": "merge_or_skip",
        "skip_embedding_recommended": skip_embedding_recommended,
    }
    if extra:
        meta.update(extra)
    return meta


# ---------------------------------------------------------------------------
# Code child metadata
# ---------------------------------------------------------------------------


def build_code_child_metadata(
    *,
    parent_id: str,
    parent_context: str,
    section: dict[str, Any],
    block_meta: dict[str, Any],
    block_page_no: int | None = None,
    has_lead_in: bool = False,
    has_tail_note: bool = False,
    lead_in_text: str = "",
    tail_note_text: str = "",
    merged_into_code_block: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build metadata for a code block child chunk."""
    code_extra: dict[str, Any] = {
        "content_kind": "code_block",
        "has_lead_in": has_lead_in,
        "has_tail_note": has_tail_note,
        "lead_in_text": lead_in_text,
        "tail_note_text": tail_note_text,
        "merged_into_code_block": merged_into_code_block,
    }
    if extra:
        code_extra.update(extra)
    return build_child_metadata(
        block_meta=block_meta,
        parent_id=parent_id,
        parent_context=parent_context,
        section=section,
        block_page_no=block_page_no,
        normalized_block_type="text",
        raw_block_type=block_meta.get("raw_block_type", "code"),
        extra=code_extra,
    )
