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
from app.services.llm_context_builder import (
    build_cloud_flat_chunks_payload,
    build_evidence_groups_payload,
    build_llm_context_payload,
)


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
            self._attach_retrieval_meta(context_payload, retrieval_data)
            context_payload.setdefault("warnings", [])
            return success_response(context_payload)

        # ── Evidence groups mode ──────────────────────────────────────
        if return_mode == "evidence_groups":
            context_payload = build_evidence_groups_payload(retrieval_data, request_payload)
            self._attach_retrieval_meta(context_payload, retrieval_data)
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
                # 调试页一次查询同时取得召回与Context，避免Global/DRIFT为两种展示重复调用模型。
                context_payload["retrieval_results"] = retrieval_data
            self._attach_retrieval_meta(context_payload, retrieval_data)
            context_payload.setdefault("warnings", [])
            return success_response(context_payload)

        # ── Unknown mode → fallback ─────────────────────────────────────
        context_payload = build_llm_context_payload(retrieval_data, request_payload)
        self._attach_retrieval_meta(context_payload, retrieval_data)
        context_payload.setdefault("warnings", [])
        return success_response(context_payload)

    @staticmethod
    def _attach_retrieval_meta(context_payload: dict, retrieval_data: dict) -> None:
        # MCP 只透传 data.meta；保留旧字段投影，同时保证策略执行事实不会丢失。
        meta = retrieval_data.get("meta", {})
        retrieval = meta.get("retrieval")
        if retrieval and retrieval.get("routing", {}).get("coverage_incomplete"):
            context_payload.setdefault("warnings", []).append("自动路由识别到全量问题，当前Top K证据覆盖不支持完整枚举或精确统计")
        if retrieval and retrieval.get("association", {}).get("paths"):
            # Context 的限长可能丢掉检索命中；只标记实际返回完整的链，保留跨文档分组边界。
            group_map = {}
            returned = {item.get("chunk_id") for item in context_payload.get("chunks", [])}
            for group in context_payload.get("groups", []):
                for item in group.get("chunks", []):
                    returned.add(item.get("chunk_id"))
                    group_map[item.get("chunk_id")] = group.get("group_id")
            paths = [{**path, "context_complete": set(path["support_chunk_ids"]) <= returned,
                      "related_evidence_group_ids": sorted({group_map[cid] for cid in path["support_chunk_ids"]
                                                            if group_map.get(cid)})}
                     for path in retrieval["association"]["paths"]]
            retrieval = {**retrieval, "association": {**retrieval["association"], "paths": paths}}
            meta = {**meta, "retrieval": retrieval}
        if retrieval and retrieval.get("global", {}).get("derived_summaries"):
            # chunk被返回也可能只剩片段；必须逐字找到支持引文，才向调用模型交付派生要点。
            chunks = list(context_payload.get("chunks", []))
            group_map = {}
            for group in context_payload.get("groups", []):
                chunks.extend(group.get("chunks", []))
                for chunk in group.get("chunks", []):
                    group_map[chunk.get("chunk_id")] = group.get("group_id")
            summaries = []
            original = retrieval["global"]["derived_summaries"]
            for summary in original:
                if not all(any(chunk.get("chunk_id") == cite.get("returned_chunk_id", cite["chunk_id"]) and
                    cite["quote"] in str(chunk.get("content") or "") for chunk in chunks)
                    for cite in summary["evidence"]):
                    continue
                summaries.append({**summary, "context_complete": True,
                    "related_evidence_group_ids": sorted({group_map[cite.get("returned_chunk_id", cite["chunk_id"])]
                        for cite in summary["evidence"] if group_map.get(cite.get("returned_chunk_id", cite["chunk_id"]))})})
            withheld = len(original) - len(summaries)
            retrieval = {**retrieval, "global": {**retrieval["global"], "derived_summaries": summaries,
                "withheld_summaries": withheld,
                "coverage_incomplete": bool(withheld or retrieval["global"]["coverage_incomplete"])}}
            meta = {**meta, "retrieval": retrieval}
            if withheld:
                context_payload.setdefault("warnings", []).append("Global部分派生要点因支持引文未完整进入上下文而被移除")
        context_payload["retrieval_meta"] = meta
        if "retrieval" in meta:
            context_payload.setdefault("meta", {})["retrieval"] = meta["retrieval"]
        withheld = (context_payload.get("meta") or {}).get("background_withheld") or 0
        if withheld:
            context_payload.setdefault("warnings", []).append(
                "背景摘要因支持引文未完整进入上下文而被移除"
            )

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
