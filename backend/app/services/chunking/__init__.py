"""Structured chunk construction from ParsedBlock → ChunkDraft → DB.

This subpackage extracts pure-logic chunk construction from chunk_service.py,
enabling unit tests without database dependencies.

Modules:
- chunk_draft: Pure dataclasses (ChunkDraft, ParentDraft, ChildDraft, SectionDraft)
- metadata_builder: Unified metadata / flags construction
- section_builder: ParsedBlock[] → SectionDraft[]
"""

from app.services.chunking.chunk_draft import ChunkDraft, ChildDraft, ParentDraft, SectionDraft

__all__ = [
    "ChunkDraft",
    "ChildDraft",
    "ParentDraft",
    "SectionDraft",
]
