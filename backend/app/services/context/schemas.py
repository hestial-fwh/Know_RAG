"""Schemas for the unified knowledge context service.

These models document the stable contract between application adapters
(FastAPI, MCP, future RAG chat) and the Know_RAG context-building pipeline.
The current FastAPI route may still pass plain dict payloads for backward
compatibility; these schemas are intentionally adapter-agnostic.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class KnowledgeContextRequest(BaseModel):
    query: str | None = None
    query_text: str | None = None

    knowledge_base_id: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list)

    retrieval_mode: str = "hybrid"
    top_k: int | None = None
    match_count: int | None = None

    score_threshold: float | None = None
    vector_threshold: float | None = None
    keyword_threshold: float | None = None

    use_vector: bool | None = None
    use_keyword: bool | None = None
    disable_vector_match: bool | None = None
    disable_keywords_match: bool | None = None

    include_content: bool = True
    include_context: bool | None = True
    include_parent_context: bool | None = True

    parent_context_mode: str | None = "standard"
    parent_context_max_chars: int | None = None
    neighbor_context_max_chars: int | None = None
    parent_children_limit: int | None = None
    max_parent_contexts: int | None = None
    merge_hits_by_parent: bool | None = True

    fusion_strategy: str | None = None
    rrf_k: int | None = None
    rrf_vector_weight: float | None = None
    rrf_keyword_weight: float | None = None

    max_context_chars: int | None = None
    max_sources: int | None = None

    include_images: bool | None = True
    include_image_urls: bool | None = False
    include_tables: bool | None = True
    include_equations: bool | None = True
    include_raw_metadata: bool | None = False


class KnowledgeContextSource(BaseModel):
    source_id: str
    document_title: str = "未知文档"
    file_name: str = ""
    section_title: str = ""
    page_range: str = ""
    matched_child_count: int = 0
    image_count: int = 0
    table_count: int = 0
    equation_count: int = 0
    evidence_count: int = 0
    summary_text: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeContextResult(BaseModel):
    query: str = ""
    context_text: str = ""
    sources: list[KnowledgeContextSource] = Field(default_factory=list)
    images: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    equations: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    retrieval_meta: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
