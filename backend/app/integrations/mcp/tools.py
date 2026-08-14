"""MCP tool implementations for Know_RAG.

Keep this file thin.  It adapts MCP tool inputs to the stable Know_RAG
llm-context HTTP API and returns clean context payloads.
"""
from __future__ import annotations

from typing import Any

from app.integrations.mcp.http_client import KnowRagMcpClientError, get_default_client


def _safe_error(message: str) -> dict[str, Any]:
    return {"success": False, "message": message, "data": None}


def _unwrap_data(api_response: dict[str, Any]) -> dict[str, Any]:
    data = api_response.get("data") if isinstance(api_response, dict) else None
    return data if isinstance(data, dict) else {}


async def list_knowledge_bases(page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """List available Know_RAG knowledge bases."""
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)
    try:
        response = await get_default_client().list_knowledge_bases(page=page, page_size=page_size)
        data = _unwrap_data(response)
        items = data.get("items") or []
        return {
            "success": True,
            "items": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "description": item.get("description") or "",
                    "document_count": item.get("document_count") or 0,
                    "chunk_count": item.get("chunk_count") or 0,
                }
                for item in items
            ],
            "total": data.get("total", len(items)),
            "page": data.get("page", page),
            "page_size": data.get("page_size", page_size),
        }
    except KnowRagMcpClientError as exc:
        return _safe_error(str(exc))


async def search_context(
    query_text: str,
    knowledge_base_id: str,
    # ── Return mode ────────────────────────────────────────────────────────
    return_mode: str = "evidence_groups",
    # ── Retrieval ──────────────────────────────────────────────────────────
    retrieval_mode: str = "hybrid",
    match_count: int = 10,
    score_threshold: float | None = None,
    vector_threshold: float | None = None,
    keyword_threshold: float | None = None,
    # ── Parent context (backward compat — maps to window params) ────────────
    include_parent_context: bool = True,
    parent_context_mode: str = "standard",
    parent_context_max_chars: int = 2000,
    neighbor_context_max_chars: int = 300,
    parent_children_limit: int = 10,
    max_parent_contexts: int = 5,
    merge_hits_by_parent: bool = True,
    # ── Selective context window ───────────────────────────────────────────
    include_context_window: bool = True,
    context_expansion_strategy: str = "rule_based",
    parent_window_max_chars: int = 1200,
    parent_window_soft_overflow_ratio: float = 1.1,
    max_extra_text_chunks_per_parent: int = 2,
    include_media_chunks: bool = True,
    media_chunk_limit: int = 2,
    # ── LLM context ────────────────────────────────────────────────────────
    max_context_chars: int = 8000,
    max_sources: int = 8,
    include_context_text: bool = False,
    # ── Media ──────────────────────────────────────────────────────────────
    include_images: bool = True,
    include_image_urls: bool = False,
    include_tables: bool = True,
    include_equations: bool = True,
) -> dict[str, Any]:
    """Search Know_RAG and return structured evidence groups.

    Default ``return_mode`` is ``evidence_groups``, which returns a rankable
    ``groups[]`` array where each group is a direct hit + neighbors + related
    images (deduplicated by asset).  ``chunks[]`` remains as a flattened
    fallback for backward compatibility.
    """
    query_text = (query_text or "").strip()
    knowledge_base_id = (knowledge_base_id or "").strip()
    if not query_text:
        return _safe_error("query_text 不能为空")
    if not knowledge_base_id:
        return _safe_error("knowledge_base_id 不能为空")

    payload: dict[str, Any] = {
        "query_text": query_text,
        "return_mode": return_mode or "evidence_groups",
        "retrieval_mode": retrieval_mode or "hybrid",
        "match_count": int(match_count or 10),
        "include_parent_context": bool(include_parent_context),
        "parent_context_mode": parent_context_mode or "standard",
        "parent_context_max_chars": int(parent_context_max_chars or 2000),
        "neighbor_context_max_chars": int(neighbor_context_max_chars or 300),
        "parent_children_limit": int(parent_children_limit or 10),
        "max_parent_contexts": int(max_parent_contexts or 5),
        "merge_hits_by_parent": bool(merge_hits_by_parent),
        # Selective context params.
        "include_context_window": bool(include_context_window),
        "context_expansion_strategy": context_expansion_strategy or "rule_based",
        "parent_window_max_chars": int(parent_window_max_chars or 1200),
        "parent_window_soft_overflow_ratio": float(parent_window_soft_overflow_ratio or 1.1),
        "max_extra_text_chunks_per_parent": int(max_extra_text_chunks_per_parent or 2),
        "include_media_chunks": bool(include_media_chunks),
        "media_chunk_limit": int(media_chunk_limit or 2),
        # LLM context params.
        "max_context_chars": int(max_context_chars or 8000),
        "max_sources": int(max_sources or 8),
        "include_context_text": bool(include_context_text),
        # Media.
        "include_images": bool(include_images),
        "include_image_urls": bool(include_image_urls),
        "include_tables": bool(include_tables),
        "include_equations": bool(include_equations),
    }
    if score_threshold is not None:
        payload["score_threshold"] = float(score_threshold)
    if vector_threshold is not None:
        payload["vector_threshold"] = float(vector_threshold)
    if keyword_threshold is not None:
        payload["keyword_threshold"] = float(keyword_threshold)

    try:
        response = await get_default_client().build_llm_context(
            knowledge_base_id=knowledge_base_id,
            payload=payload,
        )
        data = _unwrap_data(response)

        # Primary output: groups[] for evidence-groups mode; chunks[]/media[]
        # are flattened fallbacks for backward compatibility.
        groups = data.get("groups") or []
        chunks = data.get("chunks") or []
        media = data.get("media") or []

        result: dict[str, Any] = {
            "success": True,
            "query_text": data.get("query_text") or query_text,
            "groups": groups,
            "chunks": chunks,
            "media": media,
            "meta": data.get("meta") or {},
            "warnings": data.get("warnings") or [],
        }

        # Backward-compat fields, only when the backend actually populated them
        # (legacy / chunks return modes). evidence_groups mode has none.
        for key in ("context_text", "sources", "images", "tables", "equations"):
            val = data.get(key)
            if val:
                result[key] = val

        return result
    except KnowRagMcpClientError as exc:
        return _safe_error(str(exc))
