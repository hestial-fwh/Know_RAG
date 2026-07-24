from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from app.services.document_parser.page_visual_profile import (
    PageVisualProfile,
    profile_pdf_pages,
)
from app.services.document_parser.pdf_page_renderer import render_pdf_pages
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres


logger = logging.getLogger(__name__)
Renderer = Callable[..., dict[int, dict]]


def _content_from_blocks(blocks: list[ParsedBlock]) -> str:
    return sanitize_text_for_postgres(
        "\n\n".join(
            block.index_text() or block.text or block.table_html
            for block in blocks
            if block.index_text() or block.text or block.table_html
        )
    )


def _ordered_blocks(
    existing: list[ParsedBlock],
    injected_by_page: dict[int, ParsedBlock],
) -> list[ParsedBlock]:
    """Insert page visuals without reordering the parser's original blocks."""
    original = sorted(existing, key=lambda block: block.order_index)
    ordered: list[ParsedBlock] = []
    last_index_by_page = {
        int(block.page_no): index
        for index, block in enumerate(original)
        if block.page_no is not None
    }
    existing_pages = set(last_index_by_page)
    missing_page_visuals = sorted(set(injected_by_page) - existing_pages)
    missing_index = 0

    for index, block in enumerate(original):
        if block.page_no is not None:
            page_no = int(block.page_no)
            while (
                missing_index < len(missing_page_visuals)
                and missing_page_visuals[missing_index] < page_no
            ):
                missing_page = missing_page_visuals[missing_index]
                ordered.append(injected_by_page[missing_page])
                missing_index += 1
        ordered.append(block)
        if (
            block.page_no is not None
            and last_index_by_page[int(block.page_no)] == index
            and int(block.page_no) in injected_by_page
        ):
            ordered.append(injected_by_page[int(block.page_no)])

    for page_no in missing_page_visuals[missing_index:]:
        ordered.append(injected_by_page[page_no])
    for order_index, block in enumerate(ordered):
        block.order_index = order_index
    return ordered


def _profile_summary(profiles: list[PageVisualProfile]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for profile in profiles:
        counts[profile.decision] = counts.get(profile.decision, 0) + 1
    return {
        "profile_version": "page-visual-profile-v1",
        "page_count": len(profiles),
        "decision_counts": counts,
        # DCP-2 needs authoritative facts for normal pages as well as visual
        # candidates. Keeping all compact decisions makes profile replay
        # deterministic without reopening the source PDF downstream.
        "decisions": [profile.to_metadata() for profile in profiles],
    }


def _mark_existing_page_visuals(
    blocks: list[ParsedBlock],
    profiles: list[PageVisualProfile],
) -> None:
    """Mark the parser image that already represents a dominant PDF page."""
    for profile in profiles:
        if profile.decision != "already_present":
            continue
        candidates = [
            block
            for block in blocks
            if block.block_type == "image" and block.page_no == profile.page_no
        ]
        if not candidates:
            continue

        def area(block: ParsedBlock) -> float:
            if not block.bbox:
                return 0.0
            try:
                return max(0.0, float(block.bbox[2]) - float(block.bbox[0])) * max(
                    0.0,
                    float(block.bbox[3]) - float(block.bbox[1]),
                )
            except (TypeError, ValueError):
                return 0.0

        primary = max(candidates, key=area)
        primary.metadata = sanitize_json_for_postgres(
            {
                **(primary.metadata or {}),
                "semantic_role": "page_primary_visual",
                "visual_role": "page_primary_visual",
                "page_visual_status": "already_present",
                "page_visual_score": profile.heuristic_score,
                "score_kind": "heuristic_rule_score",
                "page_visual_reasons": list(profile.reasons),
                "exclude_from_asset_group": True,
            }
        )


def attach_pdf_page_visual_profile(
    path: Path,
    parsed_document: ParsedDocument,
    *,
    dominant_image_threshold: float = 0.70,
) -> ParsedDocument:
    """Attach read-only PDF page facts without rendering or injecting assets."""
    try:
        profiles = profile_pdf_pages(
            path,
            parsed_document,
            dominant_image_threshold=dominant_image_threshold,
        )
        return ParsedDocument(
            content=parsed_document.content,
            blocks=list(parsed_document.blocks or []),
            metadata=sanitize_json_for_postgres(
                {
                    **(parsed_document.metadata or {}),
                    "page_visual_profile_collected": True,
                    "page_visual_preservation_enabled": False,
                    "page_visual_profile": _profile_summary(profiles),
                }
            ),
            source_content=parsed_document.source_content,
        )
    except Exception as exc:
        logger.warning(
            "PDF page visual profiling failed open: path=%s error=%s",
            path,
            exc,
            exc_info=True,
        )
        parsed_document.metadata = sanitize_json_for_postgres(
            {
                **(parsed_document.metadata or {}),
                "page_visual_profile_collected": False,
                "page_visual_profile_degraded": True,
                "page_visual_profile_error": str(exc)[:500],
            }
        )
        return parsed_document


def enrich_pdf_page_visuals(
    path: Path,
    parsed_document: ParsedDocument,
    *,
    knowledge_base_id: str,
    document_id: str,
    dominant_image_threshold: float = 0.70,
    render_dpi: int = 144,
    renderer: Renderer = render_pdf_pages,
) -> ParsedDocument:
    """Add missing dominant PDF page visuals without suppressing parser text."""
    try:
        profiles = profile_pdf_pages(
            path,
            parsed_document,
            dominant_image_threshold=dominant_image_threshold,
        )
        selected = [
            profile.page_no for profile in profiles if profile.decision == "preserve"
        ]
        rendered = renderer(
            path,
            selected,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            dpi=render_dpi,
        )
        existing_blocks = list(parsed_document.blocks or [])
        _mark_existing_page_visuals(existing_blocks, profiles)
        injected: dict[int, ParsedBlock] = {}
        for profile in profiles:
            media = rendered.get(profile.page_no)
            if profile.decision != "preserve" or not media:
                continue
            metadata = {
                "parser": "page_visual_guard",
                "source": "pdf_page_visual_guard",
                "role": "page_primary_visual",
                "semantic_role": "page_primary_visual",
                "visual_role": "page_primary_visual",
                "page_visual_status": "preserved",
                "page_visual_score": profile.heuristic_score,
                "score_kind": "heuristic_rule_score",
                "page_visual_reasons": list(profile.reasons),
                "exclude_from_asset_group": True,
                "page_no": profile.page_no,
                "page_extent": list(profile.page_extent),
                "page_extent_source": "authoritative",
                "page_extent_coordinate_space": profile.coordinate_space,
                "render_dpi": int(render_dpi),
                "relative_path": media.get("relative_path", ""),
                "sha256": media.get("sha256", ""),
                "size": media.get("size", 0),
                "filename": media.get("filename", ""),
            }
            injected[profile.page_no] = ParsedBlock(
                block_type="image",
                text=f"PDF 第 {profile.page_no} 页主视觉",
                page_no=profile.page_no,
                bbox=profile.page_extent,
                image_path=str(media.get("path") or ""),
                image_url=str(media.get("url") or media.get("relative_path") or ""),
                metadata=sanitize_json_for_postgres(metadata),
            )
        blocks = _ordered_blocks(existing_blocks, injected)
        metadata = {
            **(parsed_document.metadata or {}),
            "page_visual_preservation_enabled": True,
            "page_visual_profile": _profile_summary(profiles),
            "page_visual_preserve_candidate_count": len(selected),
            "page_visual_asset_count": len(injected),
            "page_visual_render_failed_count": len(selected) - len(injected),
        }
        return ParsedDocument(
            content=_content_from_blocks(blocks),
            blocks=blocks,
            metadata=sanitize_json_for_postgres(metadata),
            source_content=parsed_document.source_content,
        )
    except Exception as exc:
        logger.warning(
            "PDF page visual preservation failed open: document_id=%s error=%s",
            document_id,
            exc,
            exc_info=True,
        )
        parsed_document.metadata = sanitize_json_for_postgres(
            {
                **(parsed_document.metadata or {}),
                "page_visual_preservation_enabled": True,
                "page_visual_preservation_degraded": True,
                "page_visual_preservation_error": str(exc)[:500],
            }
        )
        return parsed_document


__all__ = ["attach_pdf_page_visual_profile", "enrich_pdf_page_visuals"]
