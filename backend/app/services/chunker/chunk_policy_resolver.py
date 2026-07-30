from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.services.chunker.models import ChunkerConfig
from app.services.content_cleaning import OCR_IMAGE_MODE_NORMAL, OCR_IMAGE_MODE_SHADOW_AUXILIARY
from app.services.document_parser.document_profiler import (
    DocumentProfile,
    PageProfile,
)


POLICY_VERSION = "resolved-chunk-policy-v1"
DCP31_POLICY_VERSION = "dcp3.1-policy-v1"
DCP3_ACTIVE_POLICY_IDS = frozenset(
    {
        "poster-page-v1",
        "scanned-prose-v1",
        "table-row-group-v1",
        "visual-collection-v1",
    }
)


@dataclass(frozen=True)
class BoundaryPolicy:
    policy_id: str
    strategy: str
    preserve_page_boundary: bool
    table_mode: str

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParentPolicy:
    policy_id: str
    strategy: str
    materialize: bool

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContentAdmissionPolicy:
    policy_id: str
    origin_admission: dict[str, str]
    stage: str

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


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
    boundary_policy: BoundaryPolicy
    parent_policy: ParentPolicy
    content_admission_policy: ContentAdmissionPolicy
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
    active_policy_ids: frozenset[str],
    page_profile_v2: Any | None = None,
    visual_page: dict[str, Any] | None = None,
    policy_v2_enabled: bool = False,
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
    image_ocr_mode = OCR_IMAGE_MODE_NORMAL

    if policy_id == "scanned-prose-v1":
        boundary_strategy = "clause_paragraph_page"
        preserve_page_boundary = True
    elif policy_id == "visual-collection-v1":
        boundary_strategy = "page_description"
        preserve_page_boundary = True
        image_ocr_mode = OCR_IMAGE_MODE_SHADOW_AUXILIARY
    elif policy_id == "poster-page-v1":
        boundary_strategy = "page_text_region"
        preserve_page_boundary = True
    elif policy_id == "table-row-group-v1":
        boundary_strategy = "table_header_row_group"
        preserve_page_boundary = True
        table_mode = "repeat_header_row_group"
    elif policy_id == "slide-page-v1":
        boundary_strategy = "slide_title_bullet_group"
        preserve_page_boundary = True
    elif policy_id == "code-technical-v1":
        boundary_strategy = "heading_code_fence_line"

    visual_items = (visual_page or {}).get("visual_items") or []
    if policy_id == "visual-collection-v1":
        parent_strategy = "visual_item" if visual_items else "page_visual"
    elif policy_id == "poster-page-v1":
        parent_strategy = "page_visual"
    elif policy_id == "scanned-prose-v1":
        parent_strategy = "page"
    elif policy_id == "table-row-group-v1":
        parent_strategy = "table"
    elif policy_id == "slide-page-v1":
        parent_strategy = "slide"
    elif policy_id == "code-technical-v1":
        parent_strategy = "section"
    else:
        parent_strategy = "section"
    origin_admission = dict(
        getattr(page_profile_v2, "origin_admission", {}) or {}
    )
    if not origin_admission:
        origin_admission = {
            "native_text": "normal",
            "parser_text": "normal",
            "caption": "normal",
            "vlm_description": "normal",
            "visual_ocr": (
                "shadow"
                if image_ocr_mode == OCR_IMAGE_MODE_SHADOW_AUXILIARY
                else "normal"
            ),
        }
    boundary_policy = BoundaryPolicy(
        policy_id=f"boundary-{boundary_strategy}-v1",
        strategy=boundary_strategy,
        preserve_page_boundary=preserve_page_boundary,
        table_mode=table_mode,
    )
    parent_policy = ParentPolicy(
        policy_id=f"parent-{parent_strategy}-v1",
        strategy=parent_strategy,
        materialize=resolved_materialize_parent,
    )
    content_admission_policy = ContentAdmissionPolicy(
        policy_id="content-admission-block-origin-v1",
        origin_admission=origin_admission,
        stage="pre_reflow_and_index",
    )
    policy_version = DCP31_POLICY_VERSION if policy_v2_enabled else POLICY_VERSION
    return ResolvedChunkPolicy(
        page_no=page.page_no,
        policy_id=policy_id,
        policy_version=policy_version,
        boundary_strategy=boundary_strategy,
        child_size=child_size,
        child_overlap=child_overlap,
        parent_size=parent_size,
        materialize_parent=resolved_materialize_parent,
        preserve_page_boundary=preserve_page_boundary,
        table_mode=table_mode,
        image_ocr_mode=image_ocr_mode,
        shadow_only=policy_id not in active_policy_ids,
        boundary_policy=boundary_policy,
        parent_policy=parent_policy,
        content_admission_policy=content_admission_policy,
        reasons=(
            f"page_kind={page.page_kind}",
            f"semantic_roles={','.join(page.semantic_roles) or 'none'}",
            (
                "dcp3_policy_active"
                if policy_id in active_policy_ids
                else "dcp3_fallback_shadow"
            ),
            (
                "dcp3.1_composable_policy"
                if policy_v2_enabled
                else "dcp3_legacy_policy"
            ),
        ),
    )


def resolve_chunk_policies(
    profile: DocumentProfile,
    cfg: ChunkerConfig,
    *,
    materialize_parent: bool | None = None,
    active_policy_ids: frozenset[str] = frozenset(),
    profile_v2: Any | None = None,
    visual_layout: dict[str, Any] | None = None,
    policy_v2_enabled: bool = False,
) -> tuple[ResolvedChunkPolicy, ...]:
    """Resolve per-page policies and identify the policies active in this phase."""
    v2_pages = {
        int(page.page_no): page
        for page in getattr(profile_v2, "pages", ()) or ()
    }
    visual_pages = {
        int(page["page_no"]): page
        for page in (visual_layout or {}).get("pages") or []
        if isinstance(page, dict) and page.get("page_no") is not None
    }
    return tuple(
        _resolve_page(
            page,
            cfg,
            materialize_parent=materialize_parent,
            active_policy_ids=active_policy_ids,
            page_profile_v2=v2_pages.get(page.page_no),
            visual_page=visual_pages.get(page.page_no),
            policy_v2_enabled=policy_v2_enabled,
        )
        for page in profile.pages
    )


__all__ = [
    "DCP3_ACTIVE_POLICY_IDS",
    "DCP31_POLICY_VERSION",
    "POLICY_VERSION",
    "BoundaryPolicy",
    "ContentAdmissionPolicy",
    "ParentPolicy",
    "ResolvedChunkPolicy",
    "resolve_chunk_policies",
]
