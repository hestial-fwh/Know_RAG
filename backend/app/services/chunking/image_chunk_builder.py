"""Build image and image OCR child drafts from parsed image blocks."""

from __future__ import annotations

from typing import Any, Callable

from app.services.chunker import split_document
from app.services.chunking.chunk_draft import ChildDraft, ParentDraft
from app.services.chunking.metadata_builder import build_image_child_metadata, build_image_ocr_metadata
from app.services.chunking.section_builder import (
    _block_index_content,
    _block_ocr_text,
    block_page_no,
    raw_block_type,
)
from app.services.content_cleaning import clean_chunk_content_for_storage, is_low_information_text, should_skip_text_chunk
from app.utils.text import sanitize_text_for_postgres


def _image_info(block: Any) -> dict[str, Any]:
    info = dict(getattr(block, "metadata", None) or {})
    if getattr(block, "image_path", ""):
        info.setdefault("path", getattr(block, "image_path", ""))
    if getattr(block, "image_url", ""):
        info.setdefault("url", getattr(block, "image_url", ""))
    page = block_page_no(block, getattr(block, "index_text", lambda: "")() or getattr(block, "text", ""))
    if page is not None:
        info.setdefault("page_no", page)
        info.setdefault("source_page", page)
    if getattr(block, "bbox", None):
        info.setdefault("bbox", list(getattr(block, "bbox")))
    return info


def build_image_ocr_children(
    *,
    block: Any,
    section: dict[str, Any],
    parent: ParentDraft,
    file_type: str,
    child_cfg: Any,
    block_meta: dict[str, Any],
    ocr_layers: dict[str, Any],
    neighbor_info: dict[str, Any] | None = None,
    merge_short_parsed_chunks: Callable[[list[Any]], list[Any]],
    start_at: int | None = None,
) -> tuple[list[ChildDraft], int]:
    raw = _block_ocr_text(block)
    text = clean_chunk_content_for_storage(
        ocr_layers.get("ocr_cleaned_text") or sanitize_text_for_postgres(raw),
        chunk_type="text",
    )
    if not text or len(text.strip()) < 120 or is_low_information_text(text):
        return [], 0

    page = block_page_no(block, getattr(block, "index_text", lambda: "")() or getattr(block, "text", "") or text)
    bbox = list(getattr(block, "bbox", None) or []) or None
    children: list[ChildDraft] = []
    for parsed in merge_short_parsed_chunks(split_document(text, file_type, child_cfg)):
        content = clean_chunk_content_for_storage(parsed.content, chunk_type="text")
        if not content or is_low_information_text(content):
            continue
        metadata = build_image_ocr_metadata(
            parent_id=parent.draft_ref,
            parent_context=parent.context_header or "",
            section=section,
            block_meta=block_meta,
            block_page_no=page,
            bbox=bbox,
            image_url=getattr(block, "image_url", ""),
            extra={
                **ocr_layers,
                **{k: v for k, v in (neighbor_info or {}).items() if v},
                **(parsed.metadata or {}),
            },
        )
        child_start = None if start_at is None else start_at + parsed.start
        child_end = None if start_at is None else start_at + parsed.end
        children.append(
            ChildDraft(
                content=content,
                chunk_type="image_ocr_text",
                parent_ref=parent.draft_ref,
                start_at=child_start,
                end_at=child_end,
                context_header=parsed.context_header or getattr(block, "context_header", "") or parent.context_header,
                image_info=_image_info(block),
                flags={"is_ocr_text_chunk": True, "derived_from_image": True, "structured_parent_child": True},
                metadata=metadata,
                page_no=page,
                bbox=bbox,
                source_block_order=getattr(block, "order_index", 0),
                source_block_orders=[getattr(block, "order_index", 0)],
                normalized_block_type="image",
                raw_block_type=raw_block_type(block),
                derived_from_block_type="image",
                derived_image_url=getattr(block, "image_url", ""),
                ocr_text_source="metadata",
            )
        )
    return children, len(text) + 2


def build_image_child(
    *,
    block: Any,
    section: dict[str, Any],
    parent: ParentDraft,
    file_type: str,
    block_meta: dict[str, Any],
    ocr_layers: dict[str, Any],
    neighbor_info: dict[str, Any] | None = None,
    start_at: int | None = None,
) -> ChildDraft:
    content = clean_chunk_content_for_storage(_block_index_content(block, file_type), chunk_type="image")
    placeholder_image = False
    if not content:
        content = "图片"
        placeholder_image = True
    skip_embedding = placeholder_image or should_skip_text_chunk(content, chunk_type="image", metadata=block_meta)
    page = block_page_no(block, getattr(block, "index_text", lambda: "")() or getattr(block, "text", "") or content)
    bbox = list(getattr(block, "bbox", None) or []) or None
    rtype = raw_block_type(block)
    visual_subtype = "chart" if rtype in {"chart", "graph", "plot"} else rtype
    metadata = build_image_child_metadata(
        parent_id=parent.draft_ref,
        parent_context=parent.context_header or "",
        section=section,
        block_meta={**block_meta, "skip_embedding_recommended": skip_embedding, "low_content_chunk": skip_embedding, "skip_reason": "placeholder_image" if skip_embedding else None},
        block_page_no=page,
        bbox=bbox,
        visual_subtype=visual_subtype,
        extra={**ocr_layers, **{k: v for k, v in (neighbor_info or {}).items() if v}},
    )
    end_at = None if start_at is None else start_at + len(content)
    return ChildDraft(
        content=content,
        chunk_type="image",
        parent_ref=parent.draft_ref,
        start_at=start_at,
        end_at=end_at,
        context_header=getattr(block, "context_header", "") or parent.context_header,
        image_info=_image_info(block),
        flags={
            "has_image": True,
            "has_ocr_text": bool(_block_ocr_text(block)),
            "has_vlm_description": bool((getattr(block, "metadata", None) or {}).get("vlm_description")),
            "index_content_enriched": True,
            "structured_parent_child": True,
            "has_parent_context": True,
            "skip_embedding_recommended": skip_embedding,
            "low_content_chunk": skip_embedding,
            "skip_reason": "placeholder_image" if skip_embedding else None,
        },
        metadata=metadata,
        page_no=page,
        bbox=bbox,
        source_block_order=getattr(block, "order_index", 0),
        source_block_orders=[getattr(block, "order_index", 0)],
        normalized_block_type="image",
        raw_block_type=rtype,
        skip_embedding_recommended=skip_embedding,
    )
