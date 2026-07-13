"""Pure dataclasses for chunk construction — no database dependencies.

These represent chunks *before* they are persisted as SQLAlchemy ORM objects.
The separation allows chunk-building logic to be tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class SectionDraft:
    """A group of ParsedBlocks that form one parent context.

    Created by section_builder from heading/size-based grouping.
    """

    title: str = ""
    heading_path: list[str] = field(default_factory=list)
    blocks: list[Any] = field(default_factory=list)  # list[ParsedBlock]
    strategy: str = "synthetic"  # "heading" | "synthetic" | "size_split"
    page_start: int | None = None
    page_end: int | None = None
    skipped_heading_child_count: int = 0
    skipped_low_info_text_count: int = 0


@dataclass
class ChunkDraft:
    """A chunk ready for persistence, independent of SQLAlchemy.

    This is the common base for all chunk types.  Concrete builders
    (parent_builder, child_builder) populate these fields.
    """

    content: str
    chunk_type: str = "text"  # "text" | "parent_text" | "table" | "image" | "image_ocr_text" | "equation"
    start_at: int | None = None
    end_at: int | None = None
    parent_chunk_id: str | None = None
    context_header: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    image_info: dict[str, Any] | None = None
    table_info: dict[str, Any] | None = None
    draft_ref: str = ""
    parent_ref: str | None = None
    prev_ref: str | None = None
    next_ref: str | None = None
    chunk_index: int = 0
    # Internal ordering
    order_index: int = 0

    def __post_init__(self) -> None:
        if not self.draft_ref:
            prefix = "parent" if self.chunk_type == "parent_text" else "child"
            self.draft_ref = f"{prefix}:{uuid4().hex}"


@dataclass
class ParentDraft(ChunkDraft):
    """A parent-text chunk draft.

    chunk_type is always "parent_text".
    """

    chunk_type: str = "parent_text"
    section_index: int = 0
    child_block_orders: list[int] = field(default_factory=list)
    child_types: list[str] = field(default_factory=list)
    children_count_planned: int = 0


@dataclass
class ChildDraft(ChunkDraft):
    """A child chunk draft (text / image / table / equation / code).

    chunk_type is determined by the block's normalized type.
    """

    source_block_order: int = 0
    normalized_block_type: str = "text"  # "text" | "image" | "table" | "equation"
    raw_block_type: str = ""
    bbox: list[float] | None = None
    page_no: int | None = None
    source_page_start: int | None = None
    source_page_end: int | None = None
    # For text-only children: merged from multiple blocks
    merged_text_blocks: bool = False
    source_block_orders: list[int] = field(default_factory=list)
    # For OCR children
    derived_from_block_type: str = ""
    derived_image_url: str = ""
    ocr_text_source: str = ""
    # For code children
    has_lead_in: bool = False
    has_tail_note: bool = False
    lead_in_text: str = ""
    tail_note_text: str = ""
    # Caption / nearby context (goes in metadata, not content)
    nearby_context_before: str = ""
    nearby_context_after: str = ""
    bound_caption: str = ""
    bound_caption_block_order: int | None = None
    bound_caption_kind: str = ""
    skip_embedding_recommended: bool = False
