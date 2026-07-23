from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

MAX_IMAGES_PER_HIT = 3
MAX_TEXTS_PER_HIT = 5


def _ordered_unique_strings(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _requested_ids(metadata: dict[str, Any]) -> tuple[list[str], list[str]]:
    image_ids: list[str] = []
    references = metadata.get("figure_references")
    if isinstance(references, list):
        for reference in references:
            if isinstance(reference, dict):
                image_ids.extend(_ordered_unique_strings(reference.get("image_chunk_ids")))

    bindings: list[dict[str, Any]] = []
    current_bindings = metadata.get("image_bindings")
    if isinstance(current_bindings, list):
        bindings.extend(item for item in current_bindings if isinstance(item, dict))
    legacy_binding = metadata.get("image_binding")
    if isinstance(legacy_binding, dict):
        bindings.append(legacy_binding)
    text_ids: list[str] = []
    for binding in bindings:
        text_ids.extend(_ordered_unique_strings(binding.get("reference_chunk_ids")))

    return list(dict.fromkeys(image_ids)), list(dict.fromkeys(text_ids))


def _expanded_result(chunk: Any, source_result: dict[str, Any]) -> dict[str, Any]:
    chunk_id = str(chunk.id)
    content = chunk.content or ""
    metadata = chunk.metadata_json or {}
    image_info = chunk.image_info or {}
    result = {
        "id": chunk_id,
        "chunk_id": chunk_id,
        "matched_content": content[:1000],
        "display_content": content[:1000],
        "display_preview": content[:300],
        "preview": content[:300],
        "chunk_type": chunk.chunk_type,
        "document_id": chunk.document_id or "",
        "knowledge_id": getattr(chunk, "knowledge_id", None) or chunk.document_id or "",
        "knowledge_base_id": chunk.knowledge_base_id or "",
        "knowledge_title": source_result.get("knowledge_title")
        or source_result.get("title")
        or "",
        "title": source_result.get("title") or source_result.get("knowledge_title") or "",
        "file_name": source_result.get("file_name")
        or source_result.get("knowledge_filename")
        or "",
        "knowledge_filename": source_result.get("knowledge_filename")
        or source_result.get("file_name")
        or "",
        "file_hash": source_result.get("file_hash") or "",
        "knowledge_source": source_result.get("knowledge_source") or "",
        "knowledge_channel": source_result.get("knowledge_channel") or "",
        "parse_status": source_result.get("parse_status") or "",
        "chunk_index": getattr(chunk, "chunk_index", 0) or 0,
        "seq": getattr(chunk, "chunk_index", 0) or 0,
        "pre_chunk_id": getattr(chunk, "pre_chunk_id", None),
        "next_chunk_id": getattr(chunk, "next_chunk_id", None),
        "content_hash": getattr(chunk, "content_hash", None) or "",
        "metadata": metadata,
        "metadata_json": metadata,
        "chunk_metadata": metadata,
        "image_info": image_info,
        "parent_chunk_id": chunk.parent_chunk_id or "",
        "context_header": chunk.context_header or "",
        "score": 0.0,
        "rrf_score": 0.0,
        "vector_score": 0.0,
        "keyword_score": 0.0,
        "vector_rank": None,
        "keyword_rank": None,
        "match_type": "relation_expansion",
        "retrieval_expansion": True,
        "expansion_source": "figure_binding",
        "expansion_source_id": str(source_result.get("id") or source_result.get("chunk_id") or ""),
        "start_at": chunk.start_at,
        "end_at": chunk.end_at,
    }
    if "content" in source_result:
        result["content"] = content
    return result


def expand_figure_bindings(
    db: Session,
    results: list[dict[str, Any]],
    *,
    degradation_messages: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fail-open, ordered expansion scoped per original hit to the same KB/document."""
    requests: list[tuple[int, str, str, str, str]] = []
    all_ids: list[str] = []
    ids_by_scope: dict[tuple[str, str], list[str]] = {}
    for hit_index, result in enumerate(results):
        metadata = result.get("metadata_json") or {}
        if not isinstance(metadata, dict):
            continue
        document_id = str(result.get("document_id") or "")
        knowledge_base_id = str(result.get("knowledge_base_id") or "")
        source_id = str(result.get("id") or "")
        if not document_id or not knowledge_base_id or not source_id:
            continue
        image_ids, text_ids = _requested_ids(metadata)
        for relation_kind, target_ids in (("image", image_ids), ("text", text_ids)):
            for target_id in target_ids:
                requests.append(
                    (
                        hit_index,
                        document_id,
                        knowledge_base_id,
                        relation_kind,
                        target_id,
                    )
                )
                all_ids.append(target_id)
                ids_by_scope.setdefault((document_id, knowledge_base_id), []).append(target_id)
    all_ids = list(dict.fromkeys(all_ids))
    if not all_ids:
        return results

    try:
        from app.db.models import Chunk as ChunkModel

        scope_filters = [
            and_(
                ChunkModel.document_id == document_id,
                ChunkModel.knowledge_base_id == knowledge_base_id,
                ChunkModel.id.in_(list(dict.fromkeys(scope_ids))),
            )
            for (document_id, knowledge_base_id), scope_ids in ids_by_scope.items()
        ]
        rows = (
            db.query(ChunkModel)
            .filter(
                ChunkModel.id.in_(all_ids),
                ChunkModel.is_enabled.is_(True),
                or_(*scope_filters),
            )
            .all()
        )
        rows_by_id = {str(row.id): row for row in rows}
        expanded = list(results)
        seen_ids = {str(result.get("id")) for result in results if result.get("id")}
        applied_counts: dict[tuple[int, str], int] = {}
        limits = {"image": MAX_IMAGES_PER_HIT, "text": MAX_TEXTS_PER_HIT}
        for hit_index, document_id, knowledge_base_id, relation_kind, target_id in requests:
            budget_key = (hit_index, relation_kind)
            if applied_counts.get(budget_key, 0) >= limits[relation_kind]:
                continue
            row = rows_by_id.get(target_id)
            if row is None or target_id in seen_ids:
                continue
            if str(row.document_id or "") != document_id:
                continue
            if str(row.knowledge_base_id or "") != knowledge_base_id:
                continue
            expanded.append(_expanded_result(row, results[hit_index]))
            seen_ids.add(target_id)
            applied_counts[budget_key] = applied_counts.get(budget_key, 0) + 1
        return expanded
    except Exception as exc:
        logger.warning("Figure-binding retrieval expansion failed open: %s", exc)
        if degradation_messages is not None:
            degradation_messages.append("图文关系扩展失败，已保留原始检索结果")
        return results


__all__ = ["expand_figure_bindings"]
