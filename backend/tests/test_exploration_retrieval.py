"""P5契约回归，使用真实来源/关系/报告与固定模型响应验证预算、失效和调用方出口。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from test_global_retrieval import community_db, indexed, foundation, build_reports
from app.db.models import Chunk, KnowledgeBase
from app.schemas.retrieval_strategy import TieredStrategy
from app.services import retrieval_service as service
from app.services.retrieval import drift_search
from app.services.retrieval.associations import EvidenceReader, search_associations
from app.services.retrieval.policy_resolver import resolve_policy


def strategy(tier="graph", **overrides):
    return {"family": "tiered", "tier": tier, **({"graph_method": "drift"} if tier == "graph" else {}),
            "fallback_policy": "error", **overrides}


async def planner(content, config, prompt):
    point_id = next(iter(json.loads(content)["points"]))
    return {"queries": [{"query": "甲校的实践合作有哪些关系", "point_ids": [point_id]}]}


@pytest.fixture
async def exploration(community_db, monkeypatch):
    await build_reports(community_db)
    with community_db.sessions() as db:
        rows, _ = search_associations(EvidenceReader(db, "kb"), "甲校", [{"chunk_id": "doc-chunk"}],
                                     "relations", TieredStrategy().budget.model_dump(), True)
        direct = next(row for row in rows if row["chunk_id"] == "doc-chunk")
    direct.update(vector_rank=1, keyword_rank=1, vector_score=.9, keyword_score=.8)
    monkeypatch.setattr(service, "get_or_create_retrieval_config", lambda db: SimpleNamespace(**service.DEFAULT_RETRIEVAL_CONFIG))
    monkeypatch.setattr(service, "vector_search", AsyncMock(return_value=([direct], "")))
    monkeypatch.setattr(service, "keyword_search", Mock(return_value=[direct]))
    monkeypatch.setattr(service, "attach_context", Mock())
    monkeypatch.setattr(service, "build_parent_groups", Mock(return_value=[]))
    monkeypatch.setattr(service, "record_audit", Mock())
    monkeypatch.setattr(service, "get_embedding_runtime", lambda db: SimpleNamespace(provider="test", model_name="fixed"))
    original = drift_search.retrieve_drift

    async def controlled(db, actor, kb_id, payload, options):
        return await original(db, actor, kb_id, payload, options, planner)

    monkeypatch.setattr(drift_search, "retrieve_drift", controlled)
    return community_db, original


@pytest.mark.parametrize("query,caps,tier,method,intent", [
    ("人工智能的含义是什么", {}, "semantic", None, "definition"),
    ("ISO9001的定义", {}, "hybrid", None, "definition"),
    ("甲校和实验室有什么合作关系", {"relations": True, "entity_matches": 2}, "graph", "local", "relationship"),
    ("甲校和实验室有什么合作关系", {"relations": True, "entity_matches": 0}, "hybrid", None, "relationship"),
    ("资料的主要主题", {"global": True}, "graph", "global", "overview"),
    ("资料的主要主题", {"global": True, "relations": True}, "fusion", None, "overview"),
    ("甲校的合作事件有哪些", {"relations": True, "events": True, "entity_matches": 2}, "fusion", None, "fusion"),
    ("甲校的合作事件有哪些", {"relations": True, "entity_matches": 2}, "graph", "local", "fusion"),
    ("资料的主要主题", {}, "hybrid", None, "overview"),
    ("深入探索甲校实践合作", {"relations": True, "global": True}, "graph", "drift", "exploration"),
    ("深入探索甲校实践合作", {"relations": True}, "hybrid", None, "exploration"),
    ("所有学校有多少专业", {"relations": True, "global": True}, "hybrid", None, "aggregation"),
    ("短问题", {}, "hybrid", None, "general"),
])
def test_auto_routes_within_family_and_explains_ready_capabilities(query, caps, tier, method, intent):
    plan = resolve_policy({"strategy": strategy("auto"), "query": query}, capabilities=caps)
    assert plan.requested["tier"] == "auto" and plan.executed["tier"] == tier
    assert plan.executed.get("graph_method") == method
    assert plan.routing["intent"] == intent and plan.routing["reason"]
    assert plan.routing["model_calls"] == 0 and plan.executed["family"] == "tiered"


@pytest.mark.parametrize("flag", ["use_keyword", "use_vector", "disable_vector_match", "disable_keywords_match", "retrieval_mode"])
def test_auto_conflicting_low_level_switches_fail(flag):
    with pytest.raises(ValueError, match="自动策略"):
        resolve_policy({"strategy": strategy("auto"), flag: "hybrid" if flag == "retrieval_mode" else True})


def test_drift_requires_minimum_shared_budget_and_does_not_change_legacy():
    with pytest.raises(ValidationError):
        TieredStrategy(tier="graph", graph_method="drift", budget={"max_candidates": 2})
    plan = resolve_policy({"query": "深入探索主题"}, {"version": "legacy_v1"}, {"relations": True, "global": True})
    assert plan.legacy and plan.routing is None


@pytest.mark.asyncio
async def test_drift_reuses_local_paths_and_stops_duplicate_followups(exploration):
    env, _ = exploration
    with env.sessions() as db:
        response = await service.hybrid_search(db, None, "kb", {"query": "深入探索甲校实践合作", "strategy": strategy(), "top_k": 3})
    assert response["success"], response
    data = response["data"]
    info = data["meta"]["retrieval"]["drift"]
    assert info["model_calls"] == 2 and info["searches"] == 2 and info["stop_reason"] == "no_new_queries"
    assert info["candidates_consumed"] <= info["budget"]["max_candidates"]
    assert info["input_chars"] <= info["budget"]["max_context_chars"]
    assert {row["chunk_id"] for row in data["results"]} == {"doc-chunk", "second-chunk"}
    assert any(len(path["support_chunk_ids"]) == 2 for path in data["meta"]["retrieval"]["association"]["paths"])
    assert all(entry["candidates_consumed"] <= entry["allocated_candidates"] for entry in info["trace"])


@pytest.mark.asyncio
async def test_drift_shared_candidate_search_and_final_evidence_limits(exploration):
    env, _ = exploration
    with env.sessions() as db:
        response = await service.hybrid_search(db, None, "kb", {"query": "深入探索甲校", "top_k": 1,
            "strategy": strategy(budget={"max_candidates": 6, "max_searches": 2, "max_rounds": 3})})
    assert response["success"], response
    info = response["data"]["meta"]["retrieval"]["drift"]
    assert info["candidates_consumed"] <= 6 and info["searches"] <= 2
    assert info["stop_reason"] in {"search_budget", "candidate_budget"} and info["coverage_incomplete"]
    assert len(response["data"]["results"]) <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invented_id", "timeout", "no_followups", "partial_failure", "source_change"])
async def test_drift_generation_failures_stop_without_unbounded_recovery(exploration, monkeypatch, failure):
    env, original = exploration
    calls = 0

    async def generate(content, config, prompt):
        nonlocal calls
        calls += 1
        if failure == "invented_id":
            return {"queries": [{"query": "越界问题", "point_ids": ["other-kb-report"]}]}
        if failure == "timeout":
            await asyncio.sleep(1.1)
        if failure == "no_followups":
            return {"queries": []}
        if calls == 2 and failure == "partial_failure":
            raise ValueError("controlled failure")
        if calls == 2 and failure == "source_change":
            with env.sessions() as other:
                other.get(Chunk, "second-chunk").is_enabled = False
                other.commit()
        return await planner(content, config, prompt)

    async def controlled(db, actor, kb_id, payload, options):
        return await original(db, actor, kb_id, payload, options, generate)

    monkeypatch.setattr(drift_search, "retrieve_drift", controlled)
    with env.sessions() as db:
        response = await service.hybrid_search(db, None, "kb", {"query": "深入探索甲校", "top_k": 3,
            "strategy": strategy(fallback_policy="hybrid" if failure in {"partial_failure", "source_change"} else "error",
                                 budget={"timeout_seconds": 1 if failure == "timeout" else 120})})
    if failure in {"invented_id", "source_change"}:
        assert not response["success"]
        assert service.vector_search.await_count <= 2
    else:
        assert response["success"], response
        info = response["data"]["meta"]["retrieval"]["drift"]
        expected = {"timeout": "timeout", "no_followups": "no_followups", "partial_failure": "partial_failure"}
        assert info["stop_reason"] == expected[failure]
        if failure != "no_followups":
            assert info["coverage_incomplete"]
        if failure == "partial_failure":
            assert response["data"]["meta"]["retrieval"]["degraded"]


@pytest.mark.asyncio
async def test_first_plan_failure_can_fallback_once_with_reserved_budget(exploration, monkeypatch):
    env, original = exploration

    async def invalid(*args):
        raise ValueError("controlled failure")

    async def controlled(db, actor, kb_id, payload, options):
        return await original(db, actor, kb_id, payload, options, invalid)

    monkeypatch.setattr(drift_search, "retrieve_drift", controlled)
    with env.sessions() as db:
        response = await service.hybrid_search(db, None, "kb", {"query": "探索甲校", "strategy": strategy(fallback_policy="hybrid", budget={"max_candidates": 3})})
    assert response["success"] and response["data"]["meta"]["retrieval"]["degraded"]
    assert service.vector_search.await_count == 1 and service.keyword_search.call_count == 1
    assert service.vector_search.call_args.args[3] == 1
    info = response["data"]["meta"]["retrieval"]["drift"]
    assert info["model_calls"] == 1 and info["candidates_consumed"] <= 3


@pytest.mark.asyncio
async def test_drift_http_get_post_kb_default_and_mcp_preserve_scope_and_trace(exploration, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import routes_retrieval
    from app.core.security import get_current_user
    from app.db.session import get_db
    from app.integrations.mcp import tools
    from app.services.context.knowledge_context_service import build_knowledge_context

    env, _ = exploration
    with env.sessions() as db:
        kb = db.get(KnowledgeBase, "kb")
        kb.retrieval_profile = {**kb.retrieval_profile, "strategy": strategy("auto")}
        db.commit()
    app = FastAPI()
    app.include_router(routes_retrieval.router)

    def database():
        with env.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: None
    with TestClient(app) as client:
        get = client.get("/knowledge-bases/kb/hybrid-search", params={"query_text": "深入探索甲校", "strategy": json.dumps(strategy()), "top_k": 3}).json()
        assert get["success"], get
        post = client.post("/knowledge-bases/kb/llm-context", json={"query_text": "深入探索甲校", "return_mode": "evidence_groups", "top_k": 3}).json()
        assert post["success"], post
        info = post["data"]["meta"]["retrieval"]
        assert info["routing"]["chosen_strategy"]["graph_method"] == "drift"
        assert {group["document_id"] for group in post["data"]["groups"]} == {"doc", "second"}
        assert any(path["context_complete"] for path in info["association"]["paths"])
        before = service.vector_search.await_count
        debug = client.post("/knowledge-bases/kb/llm-context", json={"query_text": "深入探索甲校", "return_mode": "debug", "top_k": 3}).json()
        assert debug["success"] and debug["data"]["retrieval_results"]["results"]
        # 两个Local子查询来自同一次DRIFT，展示Context不会再启动一轮探索。
        assert service.vector_search.await_count - before == debug["data"]["meta"]["retrieval"]["drift"]["searches"]

    async def context_api(*, knowledge_base_id, payload):
        with env.sessions() as db:
            return await build_knowledge_context(db=db, actor=None, kb_id=knowledge_base_id, payload=payload)

    monkeypatch.setattr(tools, "get_default_client", lambda: SimpleNamespace(build_llm_context=context_api))
    result = await tools.search_context("深入探索甲校", "kb", strategy=strategy(), match_count=3)
    assert result["success"] and result["meta"]["retrieval"]["drift"]["trace"]


def test_auto_aggregation_context_exposes_completeness_warning():
    from app.services.context.knowledge_context_service import KnowledgeContextService

    context = {"meta": {}}
    KnowledgeContextService._attach_retrieval_meta(context, {"meta": {"retrieval": {
        "routing": {"intent": "aggregation", "coverage_incomplete": True}}}})
    assert any("完整枚举" in warning for warning in context["warnings"])


@pytest.mark.asyncio
async def test_local_generation_records_only_provider_reported_token_counts(monkeypatch):
    import httpx
    from app.services.knowledge_index import extractor

    def handler(request):
        assert json.loads(request.content)["options"]["num_predict"] == 2048
        return httpx.Response(200, json={"response": '{"queries":[]}', "prompt_eval_count": 23, "eval_count": 7})

    client = httpx.AsyncClient
    monkeypatch.setattr(extractor.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "measured_calls": 0}
    result = await extractor.generate_json("资料", {"provider": "ollama", "model_name": "local-test",
        "base_url": "http://localhost:11434", "_usage": usage, "_max_predict": 2048}, "规划")
    assert result == {"queries": []}
    assert usage == {"prompt_tokens": 23, "completion_tokens": 7, "measured_calls": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("rounds", [1, 2])
async def test_final_rerank_shares_model_call_budget(exploration, monkeypatch, rounds):
    env, _ = exploration
    rerank = AsyncMock(side_effect=lambda db, query, rows, count: rows)
    monkeypatch.setattr(service, "rerank_results", rerank)
    with env.sessions() as db:
        response = await service.hybrid_search(db, None, "kb", {"query": "深入探索甲校", "rerank_enabled": True,
            "strategy": strategy(budget={"max_model_calls": 2, "max_rounds": rounds})})
    assert response["success"], response
    info = response["data"]["meta"]["retrieval"]["drift"]
    assert info["model_calls"] <= 2 and rerank.await_count == (1 if rounds == 1 else 0)
    if rounds == 2:
        assert info["stop_reason"] == "model_budget"


@pytest.mark.asyncio
async def test_source_change_while_reranking_cannot_publish_results(exploration, monkeypatch):
    env, _ = exploration

    async def changed(db, query, rows, count):
        with env.sessions() as other:
            other.get(Chunk, "second-chunk").is_enabled = False
            other.commit()
        return rows

    monkeypatch.setattr(service, "rerank_results", changed)
    with env.sessions() as db:
        response = await service.hybrid_search(db, None, "kb", {"query": "深入探索甲校", "rerank_enabled": True,
            "strategy": strategy(budget={"max_rounds": 1})})
    assert not response["success"]


def test_auto_routes_event_only_to_event_graph():
    plan = resolve_policy(
        {"strategy": strategy("auto"), "query": "甲校签订了什么事件"},
        capabilities={"events": True},
    )
    assert plan.executed["family"] == "event_graph"
    assert plan.routing["intent"] == "event"


def test_auto_negative_routes_for_short_query_and_incomplete_coverage():
    plan = resolve_policy(
        {"strategy": strategy("auto"), "query": "短"},
        capabilities={"relations": True, "events": True, "entity_matches": 3},
    )
    assert plan.executed["tier"] == "hybrid" and plan.routing["intent"] == "general"
    plan = resolve_policy(
        {"strategy": strategy("auto"), "query": "甲校的合作事件"},
        capabilities={"relations": True, "events": True, "entity_matches": 3,
                      "coverage_incomplete": True},
    )
    assert plan.executed["tier"] == "hybrid"
