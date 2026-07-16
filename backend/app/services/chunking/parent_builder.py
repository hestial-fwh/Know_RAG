"""Build parent chunk content and drafts from SectionDraft.

Extracted from chunk_service.py — pure logic, no database dependency.
Uses section_builder for block-level helpers.
"""

from __future__ import annotations

from typing import Any

from app.services.chunker.heading_utils import normalize_context_header, strip_continuation_suffix
from app.services.chunking.section_builder import (
    block_parent_text,
    is_noise_block,
    normalized_child_type,
    parent_context_header,
    raw_block_type,
)
from app.services.chunking.chunk_draft import ParentDraft
from app.services.chunking.metadata_builder import build_parent_flags, build_parent_metadata
from app.services.content_cleaning import is_low_information_text
from app.utils.text import sanitize_text_for_postgres


def build_parent_content(section: dict[str, Any], file_type: str) -> str:
    """Build clean parent content from a section's blocks.

    Parent text should be a semantic context block, not a display template
    with display labels.
    """
    heading = parent_context_header(section)
    body_lines: list[str] = []
    for block in section.get("blocks") or []:
        if is_noise_block(block):
            continue
        text = block_parent_text(block, file_type)
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
        # Avoid duplicating the heading in the body
        repeated_heading = f"{base_heading}（续）（续）" if base_heading else ""
        for candidate in (repeated_heading, heading, base_heading):
            if candidate and body.startswith(candidate):
                body = body[len(candidate):].lstrip("\n\r ：:")
                break

    return sanitize_text_for_postgres(body)


def build_parent(
    *,
    section: dict[str, Any],
    section_index: int,
    file_type: str,
    cfg: Any,
    start_at: int = 0,
) -> ParentDraft | None:
    """Build one database-independent parent draft for a section."""
    content = build_parent_content(section, file_type)
    is_synthetic_parent = not content
    context_header = normalize_context_header(parent_context_header(section))
    blocks = section.get("blocks") or []
    has_semantic_children = any(
        normalized_child_type(b) in {"image", "table", "equation"}
        or raw_block_type(b) in {"code", "code_block", "fenced_code", "pre", "source_code"}
        for b in blocks
    )

    # If parent content is empty but the section has table/image/code blocks,
    # create a synthetic parent so children are not silently discarded.
    if not content:
        if has_semantic_children:
            content = context_header or "文档内容"
        else:
            return None

    child_types = [
        normalized_child_type(block)
        for block in blocks
        if not is_noise_block(block)
    ]
    page_start = section.get("page_start")
    page_end = section.get("page_end")
    metadata = build_parent_metadata(
        section=section,
        section_index=section_index,
        file_type=file_type,
        child_types=child_types,
        page_start=page_start,
        page_end=page_end,
        skipped_heading_child_count=int(section.get("skipped_heading_child_count") or 0),
        skipped_low_info_text_count=int(section.get("skipped_low_info_text_count") or 0),
        chunk_strategy=getattr(cfg, "strategy", "auto") or "auto",
        token_limit=int(getattr(cfg, "token_limit", 0) or 0),
        languages=list(getattr(cfg, "languages", []) or []),
        separators=list(getattr(cfg, "separators", []) or []),
    )
    flags = build_parent_flags(child_types)
    if is_synthetic_parent:
        flags["synthetic_parent"] = True
        metadata.setdefault("parent_source", "synthetic_from_visual_blocks")
    return ParentDraft(
        content=content,
        start_at=start_at,
        end_at=start_at + len(content),
        context_header=context_header,
        metadata=metadata,
        flags=flags,
        section_index=section_index,
        child_block_orders=[
            int(getattr(block, "order_index", 0))
            for block in blocks
        ],
        child_types=sorted(set(child_types)),
        children_count_planned=len(blocks),
    )
