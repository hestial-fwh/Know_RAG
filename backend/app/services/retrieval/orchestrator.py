"""统一入口负责策略和执行诊断；原切片召回继续由兼容服务维护。"""

from time import perf_counter

from pydantic import ValidationError

from app.core.response import error_response
from app.db.models import KnowledgeBase
from app.services.retrieval.policy_resolver import resolve_policy


async def execute_search(db, actor, kb_id: str, payload: dict):
    from app.services.retrieval_service import _execute_chunk_search

    started = perf_counter()
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        return error_response("知识库不存在")
    profile = getattr(kb, "retrieval_profile", None)
    capabilities, reader = {}, None
    selected = payload.get("strategy") or (
        profile.get("strategy") if isinstance(profile, dict) and profile.get("version") == "new_profile_v2" else None
    )
    if isinstance(selected, dict) and (selected.get("family") == "event_graph" or selected.get("tier") in {"graph", "fusion", "auto"}):
        from app.services.retrieval.associations import EvidenceReader

        # 未开启派生索引的旧库不查询新表，保持升级前的兼容行为。
        if isinstance(profile, dict) and set(profile.get("build_indexes", [])) & {"relations", "events"}:
            reader = EvidenceReader(db, kb_id)
            capabilities = reader.capabilities()
        if reader is not None and selected.get("tier") == "auto":
            from app.services.retrieval.associations import _seeds

            query = str(payload.get("query_text") or payload.get("query") or "")
            capabilities["entity_matches"] = len(_seeds(reader, query, [], 20))
        wants_global = selected.get("graph_method") in {"global", "drift"} or selected.get("tier") == "auto" or (
            selected.get("tier") == "fusion" and bool((selected.get("fusion") or {}).get("include_global"))
        )
        if isinstance(profile, dict) and "communities" in profile.get("build_indexes", []) and wants_global:
            from app.services.knowledge_index.communities import current_reports

            reports, _ = current_reports(db, kb_id)
            capabilities["global"] = bool(reports)
    try:
        plan = resolve_policy(payload, profile if isinstance(profile, dict) else None, capabilities)
    except (ValueError, ValidationError) as exc:
        return error_response(str(exc))

    # 初级只禁用关系候选扩展；父块窗口和图片展示仍由上下文参数独立控制。
    plan.payload["_semantic_only"] = plan.executed.get("tier") == "semantic"
    plan.payload["_v2"] = not plan.legacy
    plan.payload["_strict_channels"] = (
        not plan.legacy and plan.requested.get("fallback_policy") == "error"
    )
    if plan.payload.get("_association_kind") or plan.payload.get("_association_kinds"):
        plan.payload["_association_reader"] = reader
    if plan.payload.get("_global_search") or plan.payload.get("_drift_search"):
        from app.services.retrieval.global_search import execute_global
        from app.services.retrieval.drift_search import DriftPlanningError, execute_drift

        method = "DRIFT" if plan.payload.get("_drift_search") else "Global"
        executor = execute_drift if method == "DRIFT" else execute_global
        try:
            response = await executor(db, actor, kb_id, plan.payload)
        except Exception as exc:
            db.rollback()
            if plan.requested.get("fallback_policy") == "error" or (method == "DRIFT" and not isinstance(exc, DriftPlanningError)):
                return error_response(f"{method}检索失败：{type(exc).__name__}")
            plan.degraded, plan.reason = True, f"{method}检索失败：{type(exc).__name__}；已回退混合检索"
            plan.executed = {"family": "tiered", "tier": "hybrid"}
            if method == "DRIFT":
                # 首次规划失败后的单次回退也受DRIFT总候选预算约束。
                plan.payload["_candidate_k"] = min(plan.payload["_candidate_k"], plan.payload["_drift_budget"]["max_candidates"] // 2)
            response = await _execute_chunk_search(db, actor, kb_id, plan.payload)
            if method == "DRIFT" and response.get("success"):
                fallback_meta = response["data"]["meta"]
                counts = (fallback_meta.get("_candidate_budget") or {}).get("candidate_counts", {})
                fallback_meta["_drift"] = {"provider": "know_rag_drift_v1", "executed": False,
                    "graph_revision": None, "model_calls": exc.model_calls, "searches": 1, "rounds": 0,
                    "candidates_consumed": sum(counts.values()), "stop_reason": "initial_plan_failure",
                    "coverage_incomplete": True, "aggregation_supported": False,
                    "trace": [{"round": 0, "query": str(payload.get("query_text") or payload.get("query") or ""),
                        "derived_query": False, "channels": fallback_meta.get("_executed_channels", []), "fallback": True}]}
    else:
        response = await _execute_chunk_search(db, actor, kb_id, plan.payload)
    if not response.get("success"):
        return response
    data = response["data"]

    # Global 只做背景层：不替换主结果，支持原文仅在未被覆盖时低权重并入。
    if plan.payload.get("_global_background"):
        from app.services.retrieval.global_search import retrieve_global_background

        top_k = int(plan.payload.get("top_k") or plan.payload.get("match_count") or 5)
        summaries, support, background_meta = await retrieve_global_background(
            db, kb_id,
            str(plan.payload.get("query_text") or plan.payload.get("query") or ""),
            plan.payload.get("_association_budget") or {},
            top_k, bool(plan.payload.get("include_content", True)),
        )
        results = data.get("results") or []
        existing = {item.get("chunk_id") for item in results}
        for item in support:
            if item.get("chunk_id") not in existing:
                results.append(item)
                existing.add(item.get("chunk_id"))
        data["results"] = results
        data["background"] = summaries
        data["meta"]["_global_background"] = background_meta
        if (plan.payload.get("_fusion_settings") or {}).get("include_event_timeline"):
            from app.services.llm_context_builder import build_event_lines

            data["event_lines"] = build_event_lines(results)

    diagnostic = plan.diagnostics()
    diagnostic["elapsed_ms"] = round((perf_counter() - started) * 1000, 2)
    diagnostic["result_count"] = len(data.get("results") or [])
    if response.get("message"):
        diagnostic["degraded"] = True
        diagnostic["degradation_reason"] = "；".join(filter(None, (
            plan.reason, response["message"],
        )))
    diagnostic["channels"] = data["meta"].pop("_executed_channels", [])
    global_meta = data["meta"].pop("_global", None)
    if global_meta is not None:
        diagnostic["global"] = global_meta
    background_meta = data["meta"].pop("_global_background", None)
    if background_meta is not None:
        diagnostic["global_background"] = background_meta
    drift_meta = data["meta"].pop("_drift", None)
    if drift_meta is not None:
        diagnostic["drift"] = drift_meta
    candidate_budget = data["meta"].pop("_candidate_budget", None)
    if candidate_budget is not None:
        diagnostic["candidate_budget"] = candidate_budget
    association = data["meta"].pop("_association", None)
    if association is not None:
        diagnostic["association"] = association
        if not association.get("executed"):
            diagnostic["executed_strategy"] = {"family": "tiered", "tier": "hybrid"}
    fusion = data["meta"].pop("_fusion", None)
    if fusion is not None:
        diagnostic["fusion"] = fusion
        if background_meta is not None:
            diagnostic["fusion"]["channel_status"]["global"] = background_meta.get("status")
    if not plan.legacy and not (association or {}).get("executed") and diagnostic["channels"] != ["vector", "keyword"]:
        channels = diagnostic["channels"]
        if channels == ["vector"]:
            diagnostic["executed_strategy"] = {"family": "tiered", "tier": "semantic"}
        elif channels == ["keyword"]:
            diagnostic["executed_strategy"] = {"family": "keyword"}
    data["meta"]["retrieval"] = diagnostic
    return response
