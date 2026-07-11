"""Structured parent-child chunk rebuild orchestration."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, KnowledgeBase
from app.services.chunking.child_builder import ChildBuilder
from app.services.chunking.chunk_draft import ChildDraft, ChunkDraft, ParentDraft
from app.services.chunking.chunk_linker import ChunkLinker
from app.services.chunking.chunk_persistence import ChunkPersistence
from app.services.chunking.code_context_merger import merge_short_text_around_code_blocks
from app.services.chunking.parent_builder import build_parent
from app.services.chunking.section_builder import (
    make_structured_sections,
    normalized_child_type,
    raw_block_type,
)
from app.services.content_cleaning import (
    MIN_PARENT_TEXT_CHARS,
    clean_chunk_content_for_storage,
    is_low_information_text,
)
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.utils.text import sanitize_text_for_postgres

logger = logging.getLogger(__name__)


def update_knowledge_base_chunk_count(db: Session, knowledge_base_id: str) -> None:
    count = db.query(Chunk).filter(Chunk.knowledge_base_id == knowledge_base_id).count()
    kb = db.get(KnowledgeBase, knowledge_base_id)
    if kb is not None:
        kb.chunk_count = count


def _build_fallback_text_drafts(
    parsed_document: ParsedDocument,
    file_type: str,
    cfg: Any,
) -> list[ChunkDraft]:
    """Fallback: generate flat text child drafts when structured rebuild produces zero drafts.

    Does NOT generate parent_text chunks — only text children with basic metadata.
    """
    from app.services.chunker import split_document
    from app.services.chunker.models import ChunkerConfig as RuntimeChunkerConfig

    blocks = parsed_document.blocks or []
    if not blocks:
        content = parsed_document.content or ""
        if not content:
            return []
        blocks = [ParsedBlock(block_type="text", text=content, order_index=0)]

    fallback_cfg = RuntimeChunkerConfig(
        chunk_size=max(getattr(cfg, "child_chunk_size", None) or getattr(cfg, "chunk_size", 512), 256),
        chunk_overlap=max(getattr(cfg, "child_chunk_overlap", None) or getattr(cfg, "chunk_overlap", 64), 0),
        strategy=getattr(cfg, "strategy", "auto") or "auto",
        separators=getattr(cfg, "separators", []) or [],
        enable_parent_child=False,
        parent_chunk_size=0,
        child_chunk_size=0,
        child_chunk_overlap=0,
        token_limit=getattr(cfg, "token_limit", 0) or 0,
        languages=getattr(cfg, "languages", []) or [],
        enable_structured_parent_child=False,
        enable_table_row_split=False,
        enable_image_ocr_child=False,
    )

    drafts: list[ChunkDraft] = []
    offset = 0
    for block in sorted(blocks, key=lambda b: getattr(b, "order_index", 0)):
        text = (
            getattr(block, "index_text", lambda: "")()
            or getattr(block, "text", "")
            or ""
        )
        if not text:
            continue
        text = sanitize_text_for_postgres(text)
        parsed_chunks = split_document(text, file_type, fallback_cfg)
        for parsed in parsed_chunks:
            content = clean_chunk_content_for_storage(parsed.content, chunk_type="text")
            if not content or is_low_information_text(content):
                continue
            meta = {
                "source": "parser",
                "parser": "fallback_flat_text_after_structured_empty",
                "file_type": file_type,
                "chunk_strategy": "fallback_flat_text",
                "chunk_role": "child",
                "block_type": getattr(block, "block_type", "text"),
                "block_order": getattr(block, "order_index", 0),
                "fallback_reason": "structured_parent_child_produced_zero_drafts",
                **(parsed.metadata or {}),
            }
            drafts.append(
                ChildDraft(
                    content=content,
                    chunk_type="text",
                    parent_ref=None,
                    start_at=offset + parsed.start,
                    end_at=offset + parsed.end,
                    context_header=parsed.context_header or getattr(block, "context_header", "") or "",
                    flags={
                        "fallback_flat_text": True,
                        "chunk_strategy": "fallback_flat_text",
                    },
                    metadata=meta,
                    page_no=getattr(block, "page_no", None),
                    source_block_order=getattr(block, "order_index", 0),
                    source_block_orders=[getattr(block, "order_index", 0)],
                )
            )
        offset += len(text) + 2

    return drafts


def _section_has_semantic_children(section: dict[str, Any]) -> bool:
    """Check whether a section has table/image/code blocks that should not be lost."""
    for block in section.get("blocks") or []:
        ctype = normalized_child_type(block)
        rtype = raw_block_type(block)
        if ctype in {"image", "table", "equation"}:
            return True
        if rtype in {"code", "code_block", "fenced_code", "pre", "source_code"}:
            return True
    return False


class ChunkRebuildService:
    def __init__(self, persistence: ChunkPersistence | None = None):
        self.persistence = persistence or ChunkPersistence()

    def build_drafts(
        self,
        *,
        parsed_document: ParsedDocument,
        file_type: str,
        cfg: Any,
    ) -> list[ChunkDraft]:
        blocks = parsed_document.blocks or [ParsedBlock(block_type="text", text=parsed_document.content, order_index=0)]
        parent_chunk_size = getattr(cfg, "parent_chunk_size", 4096) or 4096
        enable_parent_child = getattr(cfg, "enable_parent_child", False)
        enable_structured = getattr(cfg, "enable_structured_parent_child", True)
        strategy = getattr(cfg, "strategy", "auto") or "auto"
        child_chunk_size = getattr(cfg, "child_chunk_size", 384) or 384

        sections = make_structured_sections(blocks, file_type, parent_chunk_size)

        logger.info(
            "chunk rebuild build_drafts start: document_id=%s blocks=%s sections=%s "
            "enable_parent_child=%s enable_structured=%s strategy=%s "
            "parent_chunk_size=%s child_chunk_size=%s file_type=%s",
            getattr(parsed_document, "document_id", "?"),
            len(blocks),
            len(sections),
            enable_parent_child,
            enable_structured,
            strategy,
            parent_chunk_size,
            child_chunk_size,
            file_type,
        )

        child_builder = ChildBuilder(cfg, file_type)
        parents: list[ParentDraft] = []
        children_by_parent: dict[str, list[ChildDraft]] = {}
        global_offset = 0
        skipped_no_parent = 0
        skipped_low_info_parent = 0
        sections_with_semantic_children_lost = 0

        for section_index, section in enumerate(sections):
            section["blocks"] = merge_short_text_around_code_blocks(section.get("blocks") or [])
            parent = build_parent(
                section=section,
                section_index=section_index,
                file_type=file_type,
                cfg=cfg,
                start_at=global_offset,
            )
            if parent is None:
                skipped_no_parent += 1
                if _section_has_semantic_children(section):
                    sections_with_semantic_children_lost += 1
                    logger.warning(
                        "chunk rebuild: section %s has table/image/code blocks but parent is None — "
                        "section skipped, children lost. heading_path=%s block_count=%s",
                        section_index,
                        section.get("heading_path", []),
                        len(section.get("blocks") or []),
                    )
                continue

            # Only skip if parent content is genuinely too short AND the section
            # has no semantic (table/image/code) children worth preserving.
            parent_too_short = (
                len(parent.content) < MIN_PARENT_TEXT_CHARS
                and is_low_information_text(parent.content)
            )
            if parent_too_short and not _section_has_semantic_children(section):
                skipped_low_info_parent += 1
                continue

            parents.append(parent)
            global_offset = (parent.end_at or global_offset) + 2
            children, global_offset = child_builder.build_children(
                section=section,
                parent=parent,
                start_offset=global_offset,
            )
            children_by_parent[parent.draft_ref] = children

        drafts = [draft for draft in ChunkLinker.link_parent_child_and_neighbors(parents, children_by_parent)]

        logger.info(
            "chunk rebuild build_drafts done: document_id=%s blocks=%s sections=%s "
            "parents=%s children=%s drafts=%s "
            "skipped_no_parent=%s skipped_low_info_parent=%s "
            "sections_with_semantic_children_lost=%s",
            getattr(parsed_document, "document_id", "?"),
            len(blocks),
            len(sections),
            len(parents),
            sum(len(v) for v in children_by_parent.values()),
            len(drafts),
            skipped_no_parent,
            skipped_low_info_parent,
            sections_with_semantic_children_lost,
        )

        if not drafts and blocks:
            logger.warning(
                "structured chunk rebuild produced zero drafts — falling back to flat text chunks. "
                "document_id=%s blocks=%s sections=%s parents=%s",
                getattr(parsed_document, "document_id", "?"),
                len(blocks),
                len(sections),
                len(parents),
            )
            fallback = _build_fallback_text_drafts(parsed_document, file_type, cfg)
            logger.info(
                "fallback flat text drafts: document_id=%s fallback_drafts=%s",
                getattr(parsed_document, "document_id", "?"),
                len(fallback),
            )
            return fallback

        return drafts

    def rebuild_document_chunks(
        self,
        *,
        db: Session,
        document: Document,
        parsed_document: ParsedDocument,
        file_type: str,
        cfg: Any,
    ) -> int:
        try:
            from app.services.keyword_index_service import delete_document_from_index

            delete_document_from_index(document.id)
        except Exception as exc:
            logger.warning(
                "Elasticsearch keyword index cleanup skipped before rebuild: document_id=%s error=%s",
                document.id,
                exc,
            )

        drafts = self.build_drafts(
            parsed_document=parsed_document,
            file_type=file_type,
            cfg=cfg,
        )

        if not drafts:
            logger.warning(
                "chunk rebuild: zero drafts after build_drafts (including fallback). "
                "document_id=%s blocks=%s file_type=%s",
                document.id,
                len(parsed_document.blocks or []),
                file_type,
            )
            return 0

        chunks, inserted = self.persistence.persist(
            db=db,
            document=document,
            drafts=drafts,
            file_type=file_type,
            delete_existing=True,
        )

        logger.info(
            "chunk rebuild persist done: document_id=%s drafts=%s inserted=%s",
            document.id,
            len(drafts),
            inserted,
        )

        try:
            from app.services.keyword_index_service import index_document_chunks

            indexable_chunks = [chunk for chunk in chunks if chunk.chunk_type != "parent_text"]
            index_document_chunks(db, document, indexable_chunks)
        except Exception as exc:
            logger.warning(
                "Elasticsearch keyword indexing skipped after chunk rebuild: document_id=%s error=%s",
                document.id,
                exc,
            )

        update_knowledge_base_chunk_count(db, document.knowledge_base_id)
        return inserted
