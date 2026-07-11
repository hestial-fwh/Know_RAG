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
    parent_context_header,
)
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
