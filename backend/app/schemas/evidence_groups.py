"""Pydantic response models for the evidence-groups context payload.

These describe the machine-readable contract of
``build_evidence_groups_payload`` (``return_mode="evidence_groups"``). The
builder validates its assembled payload against :class:`EvidenceGroupsResponse`
before returning, so the schema and the runtime output cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceChunkPayload(BaseModel):
    """A single chunk (hit or neighbor) inside an evidence group."""

    model_config = ConfigDict(extra="ignore")

    item_no: str
    group_no: int
    chunk_id: str
    parent_chunk_id: str | None = None
    document_id: str = ""
    document_title: str = ""
    file_name: str = ""
    page_no: int | None = None
    page_range: str = ""
    source_pages: list[int] = Field(default_factory=list)
    source_spans: list[dict[str, Any]] = Field(default_factory=list)
    page_location_status: str = "unavailable"
    chunk_type: str = "text"
    context_header: str = ""
    content: str = ""
    direct_hit: bool
    context_reason: str
    retrieval_score: float | None = None
    overlap_trimmed: bool = False
    trimmed_chars: int = 0
    overlap_source_chunk_ids: list[str] = Field(default_factory=list)


class EvidenceMediaPayload(BaseModel):
    """A single image/table media entry, deduplicated by ``asset_id``."""

    model_config = ConfigDict(extra="ignore")

    item_no: str
    group_no: int
    asset_id: str | None = None
    chunk_id: str
    parent_chunk_id: str | None = None
    document_id: str = ""
    document_title: str = ""
    file_name: str = ""
    page_no: int | None = None
    page_range: str = ""
    source_pages: list[int] = Field(default_factory=list)
    source_spans: list[dict[str, Any]] = Field(default_factory=list)
    page_location_status: str = "unavailable"
    chunk_type: str = "image"
    context_header: str = ""
    content: str = ""
    primary_reason: str
    relation_sources: list[str] = Field(default_factory=list)
    figure_ids: list[str] | None = None
    figure_id_source: Any | None = None
    related_group_nos: list[int] | None = None
    image_url: str | None = None


class EvidenceGroup(BaseModel):
    """One evidence group: direct hits + neighbors + related media."""

    model_config = ConfigDict(extra="ignore")

    group_no: int
    group_id: str
    parent_chunk_id: str | None = None
    document_id: str = ""
    document_url: str = ""
    score: float = 0.0
    direct_hit_count: int = 0
    eligible_child_count: int = 0
    hit_ratio: float = 0.0
    strong_relation_count: int = 0
    has_direct_image: bool = False
    chunks: list[EvidenceChunkPayload] = Field(default_factory=list)
    media: list[EvidenceMediaPayload] = Field(default_factory=list)


class EvidenceGroupsMeta(BaseModel):
    """Top-level metadata for an evidence-groups response."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = "evidence_groups.v1"
    top_k: int = 0
    group_count: int = 0
    return_mode: str = "evidence_groups"
    include_context_window: bool = True
    include_media_chunks: bool = True
    hit_count: int = 0
    retrieval: dict[str, Any] | None = None
    background_withheld: int = 0


class BackgroundSummary(BaseModel):
    """Global 派生摘要：只作背景，不占用证据编号，不允许伪装成原文引用。"""

    model_config = ConfigDict(extra="ignore")

    summary_id: str
    text: str
    report_ids: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    score: int = 0
    derived: bool = True
    citation_allowed: bool = False


class EventLineItem(BaseModel):
    """事件线：由已返回证据的事件路径派生，仅用于组织上下文。"""

    model_config = ConfigDict(extra="ignore")

    event_id: str
    text: str
    time: str = ""
    chunk_id: str = ""
    support_chunk_ids: list[str] = Field(default_factory=list)
    derived: bool = True
    citation_allowed: bool = False


class EvidenceGroupsResponse(BaseModel):
    """Full payload returned by ``build_evidence_groups_payload``.

    ``retrieval_meta`` and ``warnings`` are populated by
    ``KnowledgeContextService`` after the builder returns, so they default here.
    """

    model_config = ConfigDict(extra="ignore")

    success: bool = True
    query_text: str = ""
    retrieval_id: str = ""
    return_mode: str = "evidence_groups"
    groups: list[EvidenceGroup] = Field(default_factory=list)
    chunks: list[EvidenceChunkPayload] = Field(default_factory=list)
    media: list[EvidenceMediaPayload] = Field(default_factory=list)
    background: list[BackgroundSummary] = Field(default_factory=list)
    event_lines: list[EventLineItem] = Field(default_factory=list)
    meta: EvidenceGroupsMeta = Field(default_factory=EvidenceGroupsMeta)
    retrieval_meta: dict[str, Any] | None = None
    warnings: list[Any] = Field(default_factory=list)
