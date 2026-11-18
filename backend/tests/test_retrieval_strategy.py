"""双模式 P0/P1：旧契约、策略选择、通道失败和 HTTP/MCP 全链路回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api import routes_retrieval
from app.core.security import get_current_user
from app.db.models import KnowledgeBase
from app.db.session import get_db
from app.integrations.mcp import tools
from app.schemas.retrieval import HybridSearchRequest
from app.schemas.retrieval_strategy import RetrievalProfile
from app.services import retrieval_service as service
from app.services.context.knowledge_context_service import KnowledgeContextService
from app.services.retrieval.policy_resolver import resolve_policy


def strategy(tier="hybrid", **kwargs):
    return {"family": "tiered", "tier": tier, **kwargs}


@pytest.mark.parametrize("mode", [None, "hybrid", "vector", "keyword", "historical"])
@pytest.mark.parametrize("flags", [
    {"use_vector": True, "use_keyword": False},
    {"use_vector": False, "use_keyword": True},
    {"use_vector": True, "use_keyword": True},
])
def test_legacy_mode_never_overrides_existing_channels(mode, flags):
    payload = {**flags, "retrieval_mode": mode}
    plan = resolve_policy(payload)
    assert plan.legacy
    for key, value in flags.items():
        assert plan.payload[key] == value


def test_request_overrides_kb_default_without_mutating_input():
    payload = {"strategy": strategy("semantic")}
    profile = RetrievalProfile().model_dump()
    plan = resolve_policy(payload, profile)
    assert plan.payload["use_vector"] is True
    assert plan.payload["use_keyword"] is False
    assert payload == {"strategy": strategy("semantic")}
    assert profile["strategy"]["tier"] == "auto"


def test_v2_kb_supplies_default_strategy():
    plan = resolve_policy({}, RetrievalProfile().model_dump())
    assert not plan.legacy
    assert plan.executed["tier"] == "hybrid"
    assert plan.payload["use_keyword"] is True


@pytest.mark.parametrize("raw", [
    {"family": "unknown"}, strategy("unknown"), strategy(graph_method="local"),
    {"family": "event_graph", "tier": "hybrid"}, strategy(extra_field=True),
])
def test_invalid_strategy_is_rejected_by_http_schema(raw):
    with pytest.raises(ValidationError):
        HybridSearchRequest(strategy=raw)


@pytest.mark.parametrize("flags", [
    {"use_keyword": True}, {"disable_keywords_match": False},
    {"disable_vector_match": True}, {"retrieval_mode": "hybrid"},
])
def test_semantic_conflicting_switch_is_rejected(flags):
    with pytest.raises(ValueError):
        resolve_policy({"strategy": strategy("semantic"), **flags})


@pytest.mark.parametrize("raw", [strategy("graph"), {"family": "event_graph"}])
def test_unready_strategy_is_explicitly_degraded_or_fails(raw):
    plan = resolve_policy({"strategy": raw})
    assert plan.degraded and "未就绪" in plan.reason
    assert plan.executed["tier"] == "hybrid"
    with pytest.raises(ValueError, match="未就绪"):
        resolve_policy({"strategy": {**raw, "fallback_policy": "error"}})


@pytest.fixture
def search_backend(monkeypatch):
    db = Mock()
    db.scalars.return_value = []  # 召回桩不提供 Canonical 账本；位置补偿不得虚构来源。
    db.get.return_value = SimpleNamespace(retrieval_profile={"version": "legacy_v1"})
    config = SimpleNamespace(**service.DEFAULT_RETRIEVAL_CONFIG)
    monkeypatch.setattr(service, "get_or_create_retrieval_config", lambda db: config)
    vector = AsyncMock(return_value=([{"chunk_id": "v", "score": .9}], ""))
    keyword = Mock(return_value=[{"chunk_id": "k", "score": .8}])
    monkeypatch.setattr(service, "vector_search", vector)
    monkeypatch.setattr(service, "keyword_search", keyword)
    monkeypatch.setattr(service, "merge_results", lambda v, k, options: v + k)
    expansion = Mock(side_effect=lambda db, results, **kwargs: results)
    monkeypatch.setattr(service, "_expand_figure_bindings", expansion)
    monkeypatch.setattr(service, "attach_context", Mock())
    monkeypatch.setattr(service, "build_parent_groups", Mock(return_value=[]))
    monkeypatch.setattr(service, "record_audit", Mock())
    monkeypatch.setattr(service, "get_embedding_runtime", lambda db: SimpleNamespace(
        provider="test", model_name="test",
    ))
    return SimpleNamespace(db=db, vector=vector, keyword=keyword, expansion=expansion)


@pytest.mark.asyncio
async def test_semantic_executes_only_vector_and_disables_relation_expansion(search_backend):
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "strategy": strategy("semantic"),
    })
    assert response["success"], response
    search_backend.vector.assert_awaited_once()
    search_backend.keyword.assert_not_called()
    assert search_backend.expansion.call_args.kwargs["enabled"] is False
    meta = response["data"]["meta"]["retrieval"]
    assert meta["channels"] == ["vector"]
    assert meta["degraded"] is False


@pytest.mark.asyncio
async def test_unavailable_vector_reports_actual_keyword_route(search_backend):
    search_backend.vector.return_value = ([], "向量索引未就绪")
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "strategy": strategy(),
    })
    meta = response["data"]["meta"]["retrieval"]
    assert meta["degraded"] is True
    assert meta["executed_strategy"] == {"family": "keyword"}
    assert "未就绪" in meta["degradation_reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tier,policy", [("semantic", "hybrid"), ("hybrid", "error")])
async def test_missing_required_vector_fails(search_backend, tier, policy):
    search_backend.vector.return_value = ([], "向量索引未就绪")
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "strategy": strategy(tier, fallback_policy=policy),
    })
    assert response["success"] is False
    search_backend.keyword.assert_not_called()


def test_http_get_post_use_same_policy(search_backend):
    import json

    app = FastAPI()
    app.include_router(routes_retrieval.router)
    app.dependency_overrides[get_db] = lambda: search_backend.db
    app.dependency_overrides[get_current_user] = lambda: None
    with TestClient(app) as client:
        path = "/knowledge-bases/kb/hybrid-search"
        get = client.get(path, params={"query": "问题", "strategy": json.dumps(strategy("semantic"))})
        post = client.post(path, json={"query": "问题", "strategy": strategy("semantic")})
        for response in (get, post):
            body = response.json()
            assert body["success"], body
            assert body["data"]["meta"]["retrieval"]["channels"] == ["vector"]
        assert client.get(path, params={"strategy": "{"}).status_code == 422
        assert client.post(path, json={"strategy": {"family": "bad"}}).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("return_mode", ["legacy", "chunks", "evidence_groups", "debug"])
async def test_diagnostics_survive_context_and_mcp(search_backend, monkeypatch, return_mode):
    async def build_llm_context(*, knowledge_base_id, payload):
        return await KnowledgeContextService(search_backend.db).build_for_knowledge_base(
            knowledge_base_id, payload,
        )

    # 空候选仍能验证真实 context builder 和 MCP 适配；不依赖生产数据或模型。
    search_backend.vector.return_value = ([], "")
    search_backend.keyword.return_value = []
    client = SimpleNamespace(build_llm_context=build_llm_context)
    monkeypatch.setattr(tools, "get_default_client", lambda: client)
    response = await tools.search_context(
        "问题", "kb", return_mode=return_mode, strategy=strategy("graph"),
    )
    assert response["success"], response
    meta = response["meta"]["retrieval"]
    assert meta["requested_strategy"]["tier"] == "graph"
    assert meta["executed_strategy"]["tier"] == "hybrid"
    assert meta["degraded"] is True


@pytest.mark.asyncio
async def test_mcp_propagates_failure_and_omits_legacy_default(monkeypatch):
    client = SimpleNamespace(build_llm_context=AsyncMock(return_value={
        "success": False, "message": "语义图检索尚未实现", "data": None,
    }))
    monkeypatch.setattr(tools, "get_default_client", lambda: client)
    result = await tools.search_context("问题", "kb", strategy=strategy("graph", fallback_policy="error"))
    assert result["success"] is False
    payload = client.build_llm_context.call_args.kwargs["payload"]
    assert "retrieval_mode" not in payload
    assert payload["strategy"]["fallback_policy"] == "error"


def test_profile_persists_and_old_records_remain_legacy():
    engine = create_engine("sqlite://")
    KnowledgeBase.__table__.create(engine)
    with Session(engine) as db:
        db.add(KnowledgeBase(id="old", name="旧库"))
        db.add(KnowledgeBase(id="new", name="新库", retrieval_profile=RetrievalProfile().model_dump()))
        db.commit()
        assert db.get(KnowledgeBase, "old").retrieval_profile["version"] == "legacy_v1"
        assert db.get(KnowledgeBase, "new").retrieval_profile["strategy"]["tier"] == "auto"


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_keyword_complexity_respects_fallback_policy(search_backend, strict):
    search_backend.keyword.side_effect = service.ElasticsearchQueryComplexityError("too many terms")
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "strategy": strategy(fallback_policy="error" if strict else "hybrid"),
    })
    assert response["success"] is not strict
    if not strict:
        meta = response["data"]["meta"]["retrieval"]
        assert meta["channels"] == ["vector"]
        assert meta["degraded"]
        assert meta["executed_strategy"]["tier"] == "semantic"


def test_migration_preserves_old_row_and_can_downgrade():
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module("app.db.migrations.versions.0016_kb_retrieval_profile")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE knowledge_bases (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO knowledge_bases (id) VALUES ('old')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            value = connection.exec_driver_sql("SELECT retrieval_profile FROM knowledge_bases").scalar()
            assert 'legacy_v1' in value
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT id FROM knowledge_bases").scalar() == "old"


@pytest.mark.asyncio
async def test_explicit_rerank_control_reaches_executor(search_backend, monkeypatch):
    rerank = AsyncMock(side_effect=lambda db, query, results, top_k: results)
    monkeypatch.setattr(service, "rerank_results", rerank)
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "strategy": strategy(), "rerank_enabled": True,
    })
    assert response["success"]
    rerank.assert_awaited_once()


@pytest.mark.asyncio
async def test_registered_mcp_tool_exposes_strategy():
    from app.integrations.mcp.server import mcp

    registered = await mcp.list_tools()
    schema = next(tool.inputSchema for tool in registered if tool.name == "know_rag_search_context")
    assert schema["properties"]["strategy"]["type"] == "object"
    assert schema["properties"]["retrieval_mode"]["default"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("vector,keyword", [(True, False), (False, True), (True, True)])
async def test_legacy_execution_keeps_configured_channels(search_backend, vector, keyword):
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "retrieval_mode": "hybrid",
        "use_vector": vector, "use_keyword": keyword,
    })
    assert response["success"]
    assert search_backend.vector.await_count == int(vector)
    assert search_backend.keyword.call_count == int(keyword)
    assert response["data"]["meta"]["retrieval"]["contract"] == "legacy_v1"


def test_fusion_contract_forces_base_channels_and_rejects_conflicts():
    from app.schemas.retrieval_strategy import TieredStrategy

    plan = resolve_policy({"strategy": strategy("fusion")}, capabilities={"relations": True})
    assert plan.payload["_association_kinds"] == ["relations"]
    assert plan.payload["_fusion_settings"]["rank_mode"] == "rrf"
    assert plan.payload["_fusion_settings"]["event_policy"] == "independent"
    assert plan.payload["use_vector"] is True and plan.payload["use_keyword"] is True
    assert "_global_background" not in plan.payload
    with pytest.raises(ValidationError):
        TieredStrategy(tier="fusion", graph_method="local")
    with pytest.raises(ValueError):
        resolve_policy({"strategy": strategy("fusion"), "use_vector": False})
    with pytest.raises(ValidationError):
        TieredStrategy(tier="hybrid", fusion={"include_global": True})
    # 新增字段全部有默认值，旧 profile 仍可通过校验。
    assert RetrievalProfile.model_validate({"version": "new_profile_v2"}).strategy.tier == "auto"


def test_fusion_requires_at_least_one_structural_channel():
    plan = resolve_policy({"strategy": strategy("fusion")}, capabilities={})
    assert plan.degraded and plan.executed["tier"] == "hybrid"
    assert "未就绪" in plan.reason
    with pytest.raises(ValueError, match="未就绪"):
        resolve_policy({"strategy": strategy("fusion", fallback_policy="error")}, capabilities={})
    # 事件通道显式关闭时，只有事件就绪也不能进入 fusion。
    plan = resolve_policy(
        {"strategy": strategy("fusion", fusion={"event_policy": "off"})},
        capabilities={"events": True},
    )
    assert plan.degraded


def test_fusion_global_background_requires_request_and_capability():
    plan = resolve_policy(
        {"strategy": strategy("fusion", fusion={"include_global": True})},
        capabilities={"relations": True, "global": True},
    )
    assert plan.payload["_global_background"] is True
    plan = resolve_policy(
        {"strategy": strategy("fusion", fusion={"include_global": True})},
        capabilities={"relations": True},
    )
    assert "_global_background" not in plan.payload


def test_new_knowledge_base_requires_family_and_derives_indexes(monkeypatch):
    from app.services import knowledge_base_service as kb_service

    engine = create_engine("sqlite://")
    KnowledgeBase.__table__.create(engine)
    monkeypatch.setattr(kb_service, "record_audit", lambda *args, **kwargs: None)
    with Session(engine) as db:
        missing = kb_service.create_knowledge_base(db, None, {"name": "未选家族"})
        assert not missing["success"]

        family_a = kb_service.create_knowledge_base(
            db, None, {"name": "关系库", "retrieval_profile": {"family_scope": "a"}}
        )
        assert family_a["success"], family_a
        profile = family_a["data"]["retrieval_profile"]
        assert profile["family_scope"] == "a"
        assert profile["strategy"]["tier"] == "auto"
        assert set(profile["build_indexes"]) == {"entities", "relations", "communities"}

        family_b = kb_service.create_knowledge_base(
            db, None, {"name": "事件库", "retrieval_profile": {"family_scope": "b"}}
        )
        assert family_b["success"], family_b
        assert set(family_b["data"]["retrieval_profile"]["build_indexes"]) == {"entities", "events"}
