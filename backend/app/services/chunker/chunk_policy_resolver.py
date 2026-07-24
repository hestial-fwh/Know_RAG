from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.document_profiler import (
    DocumentProfile,
    PageProfile,
)


POLICY_VERSION = "resolved-chunk-policy-v1"


@dataclass(frozen=True)
class ResolvedChunkPolicy:
    page_no: int
    policy_id: str
    policy_version: str
    boundary_strategy: str
    child_size: int
    child_overlap: int
    parent_size: int
    materialize_parent: bool
    preserve_page_boundary: bool
    table_mode: str
    image_ocr_mode: str
    shadow_only: bool
    reasons: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def _policy_id(page: PageProfile) -> str:
    if page.page_kind == "table_dense":
        return "table-row-group-v1"
    if page.page_kind == "scanned_prose":
        return "scanned-prose-v1"
    if page.page_kind == "slide":
        return "slide-page-v1"
    if page.page_kind == "visual_dominant":
        if page.ocr_layout == "visual_core_text":
            return "poster-page-v1"
        return "visual-collection-v1"
    if page.code_block_count:
        return "code-technical-v1"
    return "structured-text-v1"


def _resolve_page(
    page: PageProfile,
    cfg: ChunkerConfig,
    *,
    materialize_parent: bool | None,
) -> ResolvedChunkPolicy:
    policy_id = _policy_id(page)
    child_size = max(int(cfg.child_chunk_size or cfg.chunk_size or 384), 1)
    child_overlap = max(int(cfg.child_chunk_overlap or cfg.chunk_overlap or 0), 0)
    parent_size = max(int(cfg.parent_chunk_size or 4096), 1)
    resolved_materialize_parent = (
        bool(cfg.enable_parent_child)
        if materialize_parent is None
        else bool(materialize_parent)
    )
    boundary_strategy = "heading_paragraph_sentence"
    preserve_page_boundary = False
    table_mode = "atomic"
    image_ocr_mode = "normal"

    if policy_id == "scanned-prose-v1":
        boundary_strategy = "clause_paragraph_page"
        preserve_page_boundary = True
    elif policy_id == "visual-collection-v1":
        boundary_strategy = "page_description"
        preserve_page_boundary = True
        image_ocr_mode = "shadow_auxiliary"
    elif policy_id == "poster-page-v1":
        boundary_strategy = "page_text_region"
        preserve_page_boundary = True
    elif policy_id == "table-row-group-v1":
        boundary_strategy = "table_header_row_group"
        table_mode = "repeat_header_row_group"
    elif policy_id == "slide-page-v1":
        boundary_strategy = "slide_title_bullet_group"
        preserve_page_boundary = True
    elif policy_id == "code-technical-v1":
        boundary_strategy = "heading_code_fence_line"

    return ResolvedChunkPolicy(
        page_no=page.page_no,
        policy_id=policy_id,
        policy_version=POLICY_VERSION,
        boundary_strategy=boundary_strategy,
        child_size=child_size,
        child_overlap=child_overlap,
        parent_size=parent_size,
        materialize_parent=resolved_materialize_parent,
        preserve_page_boundary=preserve_page_boundary,
        table_mode=table_mode,
        image_ocr_mode=image_ocr_mode,
        shadow_only=True,
        reasons=(
            f"page_kind={page.page_kind}",
            f"semantic_roles={','.join(page.semantic_roles) or 'none'}",
            "dcp2_shadow_only",
        ),
    )


def resolve_chunk_policies(
    profile: DocumentProfile,
    cfg: ChunkerConfig,
    *,
    materialize_parent: bool | None = None,
) -> tuple[ResolvedChunkPolicy, ...]:
    """Resolve per-page policies without changing the active splitter."""
    return tuple(
        _resolve_page(
            page,
            cfg,
            materialize_parent=materialize_parent,
        )
        for page in profile.pages
    )


__all__ = [
    "POLICY_VERSION",
    "ResolvedChunkPolicy",
    "resolve_chunk_policies",
]
