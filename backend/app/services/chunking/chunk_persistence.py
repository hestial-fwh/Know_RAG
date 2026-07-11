"""Persist ChunkDraft objects as SQLAlchemy Chunk rows."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Chunk, ChunkEmbedding, Document
from app.services.chunker.boundary_utils import is_leading_punctuation, strip_invalid_leading_punctuation
from app.services.chunker.markdown_splitter import is_orphan_short_text_chunk
from app.services.content_cleaning import (
    clean_chunk_content_for_storage,
    clear_embedding_skip_metadata,
    clear_stale_embedding_skip_flags,
    is_url_or_path_only_text,
    normalize_chunk_text,
)
from app.services.chunking.chunk_draft import ChunkDraft
from app.utils.ids import new_id
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres
from app.services.chunker.heading_utils import normalize_context_header

logger = logging.getLogger(__name__)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _is_code_block_chunk(chunk: Chunk) -> bool:
    meta = chunk.metadata_json or {}
    return (chunk.chunk_type or "text") == "text" and (
        meta.get("content_kind") == "code_block" or sanitize_text_for_postgres(chunk.content).strip().startswith("```")
    )


def _refresh_chunk_content_fields(chunk: Chunk) -> None:
    chunk.content = sanitize_text_for_postgres(chunk.content or "")
    chunk.token_count = len(chunk.content)
    chunk.content_hash = content_hash(chunk.content)


def _finalize_chunk_skip_flags(chunk: Chunk) -> None:
    flags, metadata = clear_stale_embedding_skip_flags(
        content=chunk.content,
        chunk_type=chunk.chunk_type or "text",
        flags=chunk.flags or {},
        metadata=chunk.metadata_json or {},
    )
    chunk.flags = sanitize_json_for_postgres(flags)
    chunk.metadata_json = sanitize_json_for_postgres(metadata)


def _merge_orphan_text_into_neighbor_chunk(target: Chunk, text: str, *, reason: str, prepend: bool = False) -> None:
    text = sanitize_text_for_postgres(text or "").strip()
    if not text:
        return
    target.content = sanitize_text_for_postgres(f"{text}\n{target.content}" if prepend else f"{target.content}\n{text}")
    meta = clear_embedding_skip_metadata(target.metadata_json)
    absorbed = meta.get("absorbed_orphan_texts")
    if not isinstance(absorbed, list):
        absorbed = []
    absorbed.append({"reason": reason, "text": text[:300]})
    meta["absorbed_orphan_texts"] = absorbed
    meta["absorbed_orphan_short_text"] = True
    target.metadata_json = sanitize_json_for_postgres(meta)
    flags = clear_embedding_skip_metadata(target.flags)
    flags["absorbed_orphan_short_text"] = True
    target.flags = sanitize_json_for_postgres(flags)
    _refresh_chunk_content_fields(target)


def _post_process_orphan_short_text_chunks(chunks: list[Chunk]) -> list[Chunk]:
    if not chunks:
        return []
    result: list[Chunk] = []
    for index, chunk in enumerate(chunks):
        ctype = chunk.chunk_type or "text"
        meta = chunk.metadata_json or {}
        if ctype in {"text", "parent_text"}:
            chunk.content = normalize_chunk_text(chunk.content)
            _refresh_chunk_content_fields(chunk)
            if not chunk.content or is_url_or_path_only_text(chunk.content):
                continue
        if ctype == "text" and is_leading_punctuation(chunk.content):
            prev_chunk = result[-1] if result else None
            if prev_chunk is not None and (prev_chunk.chunk_type or "text") == "text" and prev_chunk.parent_chunk_id == chunk.parent_chunk_id:
                prev_chunk.content = sanitize_text_for_postgres(f"{prev_chunk.content}\n{chunk.content}")
                prev_chunk.end_at = max(prev_chunk.end_at or 0, chunk.end_at or 0)
                _refresh_chunk_content_fields(prev_chunk)
                continue
            chunk.content = strip_invalid_leading_punctuation(chunk.content)
            if not chunk.content:
                continue
            _refresh_chunk_content_fields(chunk)
        if ctype != "text" or _is_code_block_chunk(chunk) or not is_orphan_short_text_chunk(chunk.content, meta):
            _finalize_chunk_skip_flags(chunk)
            result.append(chunk)
            continue

        prev_chunk = result[-1] if result else None
        next_chunk = chunks[index + 1] if index + 1 < len(chunks) else None
        if next_chunk is not None and _is_code_block_chunk(next_chunk) and next_chunk.parent_chunk_id == chunk.parent_chunk_id:
            next_meta = clear_embedding_skip_metadata(next_chunk.metadata_json)
            existing = sanitize_text_for_postgres(next_meta.get("lead_in_text") or "").strip()
            text = sanitize_text_for_postgres(chunk.content).strip()
            next_meta["lead_in_text"] = "\n".join(part for part in [existing, text] if part)
            next_meta["absorbed_orphan_short_text"] = True
            next_chunk.metadata_json = sanitize_json_for_postgres(next_meta)
            next_chunk.start_at = min(next_chunk.start_at or chunk.start_at or 0, chunk.start_at or 0)
            continue
        if prev_chunk is not None and _is_code_block_chunk(prev_chunk) and prev_chunk.parent_chunk_id == chunk.parent_chunk_id:
            prev_meta = clear_embedding_skip_metadata(prev_chunk.metadata_json)
            existing = sanitize_text_for_postgres(prev_meta.get("tail_note_text") or "").strip()
            text = sanitize_text_for_postgres(chunk.content).strip()
            prev_meta["tail_note_text"] = "\n".join(part for part in [existing, text] if part)
            prev_meta["absorbed_orphan_short_text"] = True
            prev_chunk.metadata_json = sanitize_json_for_postgres(prev_meta)
            prev_chunk.end_at = max(prev_chunk.end_at or 0, chunk.end_at or 0)
            continue
        if (
            prev_chunk is not None
            and (prev_chunk.chunk_type or "text") == "text"
            and not _is_code_block_chunk(prev_chunk)
            and prev_chunk.parent_chunk_id == chunk.parent_chunk_id
            and (prev_chunk.context_header or "") == (chunk.context_header or "")
        ):
            prev_chunk.content = sanitize_text_for_postgres(f"{prev_chunk.content}\n{chunk.content}")
            prev_chunk.end_at = max(prev_chunk.end_at or 0, chunk.end_at or 0)
            prev_meta = clear_embedding_skip_metadata(prev_chunk.metadata_json)
            prev_meta["absorbed_orphan_short_text"] = True
            prev_chunk.metadata_json = sanitize_json_for_postgres(prev_meta)
            _refresh_chunk_content_fields(prev_chunk)
            continue

        media_types = {"image", "table", "equation"}
        if prev_chunk is not None and (prev_chunk.chunk_type or "") in media_types and prev_chunk.parent_chunk_id == chunk.parent_chunk_id:
            _merge_orphan_text_into_neighbor_chunk(prev_chunk, chunk.content, reason="short_orphan_after_media", prepend=False)
            prev_chunk.end_at = max(prev_chunk.end_at or 0, chunk.end_at or 0)
            continue
        if next_chunk is not None and (next_chunk.chunk_type or "") in media_types and next_chunk.parent_chunk_id == chunk.parent_chunk_id:
            _merge_orphan_text_into_neighbor_chunk(next_chunk, chunk.content, reason="short_orphan_before_media", prepend=True)
            next_chunk.start_at = min(next_chunk.start_at or chunk.start_at or 0, chunk.start_at or 0)
            continue

        # Do not persist isolated short text chunks. Their content is already
        # covered by parent_text or should be recovered as neighbor metadata.
        continue

    for index, chunk in enumerate(result):
        chunk.chunk_index = index
    return result


def _make_chunk(document: Document, draft: ChunkDraft, chunk_id: str, file_type: str, id_by_ref: dict[str, str]) -> Chunk:
    chunk_type = draft.chunk_type or "text"
    clean_content = clean_chunk_content_for_storage(draft.content, chunk_type=chunk_type)
    if chunk_type in {"text", "parent_text"}:
        clean_content = normalize_chunk_text(clean_content)
    metadata = {
        "source": "parser",
        "parser": "multimodal" if chunk_type in {"image", "table"} else "basic",
        "file_type": file_type,
        **(draft.metadata or {}),
    }
    flags = dict(draft.flags or {})
    if draft.table_info:
        metadata.setdefault("table_info", draft.table_info)
    flags, metadata = clear_stale_embedding_skip_flags(
        content=clean_content,
        chunk_type=chunk_type,
        flags=flags,
        metadata=metadata,
    )
    return Chunk(
        id=chunk_id,
        document_id=document.id,
        knowledge_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        content=clean_content,
        chunk_index=int(draft.chunk_index or 0),
        token_count=len(clean_content),
        is_enabled=True,
        status="ready",
        start_at=draft.start_at,
        end_at=draft.end_at,
        chunk_type=chunk_type,
        parent_chunk_id=id_by_ref.get(draft.parent_ref or ""),
        pre_chunk_id=id_by_ref.get(draft.prev_ref or ""),
        next_chunk_id=id_by_ref.get(draft.next_ref or ""),
        content_hash=content_hash(clean_content),
        image_info=sanitize_json_for_postgres(draft.image_info or {}),
        relation_chunks=[],
        indirect_relation_chunks=[],
        context_header=normalize_context_header(draft.context_header or ""),
        flags=sanitize_json_for_postgres(flags),
        metadata_json=sanitize_json_for_postgres(metadata),
    )


class ChunkPersistence:
    def persist(
        self,
        *,
        db: Session,
        document: Document,
        drafts: list[ChunkDraft],
        file_type: str,
        delete_existing: bool = True,
    ) -> tuple[list[Chunk], int]:
        if not drafts:
            logger.warning(
                "ChunkPersistence.persist called with 0 drafts — no chunks will be written. "
                "document_id=%s file_type=%s",
                document.id, file_type,
            )
            return [], 0

        if delete_existing:
            db.query(ChunkEmbedding).filter(ChunkEmbedding.document_id == document.id).delete(synchronize_session=False)
            db.query(Chunk).filter(Chunk.document_id == document.id).delete(synchronize_session=False)

        id_by_ref = {draft.draft_ref: new_id() for draft in drafts}
        chunks = [_make_chunk(document, draft, id_by_ref[draft.draft_ref], file_type, id_by_ref) for draft in drafts]
        pre_count = len(chunks)
        chunks = _post_process_orphan_short_text_chunks(chunks)

        if not chunks and pre_count > 0:
            logger.warning(
                "ChunkPersistence: all %s drafts removed by postprocess. "
                "document_id=%s file_type=%s",
                pre_count, document.id, file_type,
            )

        for chunk in chunks:
            _finalize_chunk_skip_flags(chunk)
            db.add(chunk)
        db.flush()
        return chunks, len(chunks)
