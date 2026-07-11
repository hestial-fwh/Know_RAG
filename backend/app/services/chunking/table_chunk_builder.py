"""Build table child drafts from parsed table blocks."""

from __future__ import annotations

from typing import Any

from app.services.chunking.chunk_draft import ChildDraft, ParentDraft
from app.services.chunking.metadata_builder import build_table_child_metadata
from app.services.chunking.section_builder import _block_index_content, block_page_no, raw_block_type
from app.services.content_cleaning import clean_chunk_content_for_storage, should_skip_text_chunk


def build_table_child(
    *,
    block: Any,
    section: dict[str, Any],
    parent: ParentDraft,
    file_type: str,
    block_meta: dict[str, Any],
    neighbor_info: dict[str, Any] | None = None,
    start_at: int | None = None,
) -> ChildDraft | None:
    content = clean_chunk_content_for_storage(_block_index_content(block, file_type), chunk_type="table")
    if not content or should_skip_text_chunk(content, chunk_type="table", metadata=block_meta):
        return None

    page = block_page_no(block, getattr(block, "index_text", lambda: "")() or getattr(block, "text", "") or content)
    bbox = list(getattr(block, "bbox", None) or []) or None
    image_info = {"table_html": getattr(block, "table_html", "")} if getattr(block, "table_html", "") else {}
    if getattr(block, "image_path", ""):
        image_info["path"] = getattr(block, "image_path", "")
    if getattr(block, "image_url", ""):
        image_info["url"] = getattr(block, "image_url", "")
    if bbox:
        image_info["bbox"] = bbox

    metadata = build_table_child_metadata(
        parent_id=parent.draft_ref,
        parent_context=parent.context_header or "",
        section=section,
        block_meta=block_meta,
        block_page_no=page,
        bbox=bbox,
        extra={k: v for k, v in (neighbor_info or {}).items() if v},
    )
    end_at = None if start_at is None else start_at + len(content)
    return ChildDraft(
        content=content,
        chunk_type="table",
        parent_ref=parent.draft_ref,
        start_at=start_at,
        end_at=end_at,
        context_header=getattr(block, "context_header", "") or parent.context_header,
        metadata=metadata,
        flags={"structured_parent_child": True, "has_parent_context": True},
        image_info=image_info or None,
        table_info={"table_html": getattr(block, "table_html", "")} if getattr(block, "table_html", "") else None,
        page_no=page,
        bbox=bbox,
        source_block_order=getattr(block, "order_index", 0),
        source_block_orders=[getattr(block, "order_index", 0)],
        normalized_block_type="table",
        raw_block_type=raw_block_type(block),
    )
