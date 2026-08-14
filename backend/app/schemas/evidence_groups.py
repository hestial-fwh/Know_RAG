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
    chunk_type: str = "text"
    context_header: str = ""
    content: str = ""
    direct_hit: bool
    context_reason: str
    retrieval_score: float | None = None


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
    meta: EvidenceGroupsMeta = Field(default_factory=EvidenceGroupsMeta)
    retrieval_meta: dict[str, Any] | None = None
    warnings: list[Any] = Field(default_factory=list)
