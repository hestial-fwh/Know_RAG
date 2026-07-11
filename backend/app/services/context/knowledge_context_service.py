"""Unified application service for LLM/MCP-ready knowledge context.

This service is the decoupling layer between protocol adapters and Know_RAG's
internal retrieval pipeline.  FastAPI routes, MCP tools and future RAG chat
endpoints should call this service instead of re-implementing:

    hybrid retrieval -> parent grouping -> LLM context builder

The service deliberately does not know anything about MCP protocol, frontend UI,
or remote LLM APIs.  It only returns the clean context payload that those
adapters can use.

Return modes:
    - ``chunks_with_selective_context`` (default): cloud flat-chunks schema with
      rule-based context expansion.
    - ``chunks``: bare hit chunks only, no context expansion.
    - ``debug`` / ``legacy``: original document-centric payload for local
      debugging.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.response import success_response
from app.db.models import User
from app.services.llm_context_builder import build_cloud_flat_chunks_payload, build_llm_context_payload


class KnowledgeContextService:
    """Build clean, bounded, LLM-ready knowledge context for one knowledge base."""

    def __init__(self, db: Session, actor: User | None = None):
        self.db = db
        self.actor = actor

    async def build_for_knowledge_base(self, kb_id: str, payload: dict | None = None) -> dict[str, Any]:
        """Run retrieval and build clean context.

        Dispatch between legacy document-centric payload and modern cloud
        flat-chunks payload based on ``return_mode``.
        """
        request_payload = self._normalize_payload(payload)

        # Local import prevents a module-level circular dependency:
        from app.services import retrieval_service

        retrieval_response = await retrieval_service.hybrid_search(
            self.db,
            self.actor,
            kb_id,
            request_payload,
        )
        if not retrieval_response.get("success"):
            return retrieval_response

        retrieval_data = retrieval_response.get("data") or {}
        return_mode = str(request_payload.get("return_mode") or "chunks_with_selective_context").lower()

        # ── Legacy mode: full backward-compat with old builder ──────────
        if return_mode == "legacy":
            context_payload = build_llm_context_payload(retrieval_data, request_payload)
            context_payload["retrieval_meta"] = retrieval_data.get("meta", {})
            context_payload.setdefault("warnings", [])
            return success_response(context_payload)

        # ── Cloud flat-chunks modes ─────────────────────────────────────
        if return_mode in ("chunks", "chunks_with_selective_context", "debug"):
            context_payload = build_cloud_flat_chunks_payload(
                retrieval_data, request_payload,
            )
            # For debug mode, also run the legacy builder and merge its
            # document-centric fields (documents, sources, etc.).
            if return_mode == "debug":
                legacy = build_llm_context_payload(retrieval_data, request_payload)
                context_payload["documents"] = legacy.get("documents") or []
                context_payload["sources"] = legacy.get("sources") or []
                context_payload["citation_map"] = legacy.get("citation_map") or {}
                context_payload["images"] = legacy.get("images") or []
                context_payload["tables"] = legacy.get("tables") or []
                context_payload["equations"] = legacy.get("equations") or []
                context_payload["context_text"] = legacy.get("context_text") or ""
                context_payload["parent_groups"] = legacy.get("parent_groups") or retrieval_data.get("parent_groups") or []
            context_payload["retrieval_meta"] = retrieval_data.get("meta", {})
            context_payload.setdefault("warnings", [])
            return success_response(context_payload)

        # ── Unknown mode → fallback ─────────────────────────────────────
        context_payload = build_llm_context_payload(retrieval_data, request_payload)
        context_payload["retrieval_meta"] = retrieval_data.get("meta", {})
        context_payload.setdefault("warnings", [])
        return success_response(context_payload)

    def _normalize_payload(self, payload: dict | None) -> dict[str, Any]:
        """Normalize defaults once for FastAPI, MCP and future chat adapters."""
        request_payload: dict[str, Any] = dict(payload or {})

        # Parent context is required for the builder to produce useful evidence.
        request_payload.setdefault("include_context", True)
        request_payload.setdefault("include_parent_context", True)
        request_payload.setdefault("merge_hits_by_parent", True)

        # Default to cloud flat-chunks mode.
        request_payload.setdefault("return_mode", "chunks_with_selective_context")

        # Safe multimodal default.
        request_payload.setdefault("include_images", True)
        request_payload.setdefault("include_image_urls", False)

        # Keep both keys for compatibility.
        if not request_payload.get("query_text") and request_payload.get("query"):
            request_payload["query_text"] = request_payload.get("query")
        if not request_payload.get("query") and request_payload.get("query_text"):
            request_payload["query"] = request_payload.get("query_text")

        return request_payload


async def build_knowledge_context(
    *,
    db: Session,
    actor: User | None,
    kb_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for existing routes and future MCP adapters."""
    service = KnowledgeContextService(db, actor)
    return await service.build_for_knowledge_base(kb_id, payload)
