"""Schemas for Know_RAG MCP adapter.

These models are intentionally independent from FastAPI route schemas so the
MCP layer stays stable even when HTTP endpoint details change.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class McpKnowledgeBaseSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    document_count: int = 0
    chunk_count: int = 0


class McpListKnowledgeBasesResult(BaseModel):
    items: list[McpKnowledgeBaseSummary] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class McpSearchContextRequest(BaseModel):
    query_text: str = Field(..., description="User question or search query.")
    knowledge_base_id: str = Field(..., description="Know_RAG knowledge base ID.")

    # ── Return mode ──────────────────────────────────────────────────────
    return_mode: str = Field(
        default="chunks_with_selective_context",
        description="chunks | chunks_with_selective_context | debug | legacy",
    )

    # ── Retrieval params ─────────────────────────────────────────────────
    retrieval_mode: str = "hybrid"
    match_count: int = 10
    score_threshold: float | None = None
    vector_threshold: float | None = None
    keyword_threshold: float | None = None

    # ── Parent context (for backward compat — maps to window params) ─────
    include_parent_context: bool = True
    parent_context_mode: str = "standard"
    parent_context_max_chars: int = 2000
    neighbor_context_max_chars: int = 300
    parent_children_limit: int = 10
    max_parent_contexts: int = 5
    merge_hits_by_parent: bool = True

    # ── Context window params (selective context) ────────────────────────
    include_context_window: bool = True
    context_expansion_strategy: str = "rule_based"
    parent_window_max_chars: int = 1200
    parent_window_soft_overflow_ratio: float = 1.1
    max_extra_text_chunks_per_parent: int = 2
    include_media_chunks: bool = True
    media_chunk_limit: int = 2

    # ── LLM context params ───────────────────────────────────────────────
    max_context_chars: int = 8000
    max_sources: int = 8
    include_context_text: bool = False

    # ── Media ────────────────────────────────────────────────────────────
    include_images: bool = True
    include_image_urls: bool = False
    include_tables: bool = True
    include_equations: bool = True


class McpSearchContextResult(BaseModel):
    """Result for the cloud flat-chunks response schema.

    Primary field is ``chunks[]``.  ``context_text`` is kept for backward
    compatibility but defaults to empty.
    """
    context_text: str = ""
    chunks: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    # Legacy fields (populated only in debug / legacy mode).
    sources: list[dict[str, Any]] = Field(default_factory=list)
    images: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    equations: list[dict[str, Any]] = Field(default_factory=list)
