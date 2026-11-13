"""P3 同切片、同最终预算的工程回放；召回种子固定，不宣称业务检索准确率。"""

import importlib
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from test_knowledge_index_foundation import foundation, add_document
from app.db.models import (
    Chunk, Document, DocumentCanonicalArtifact, EventEntity, KnowledgeBase, KnowledgeEvent,
    KnowledgeRelation, KnowledgeIndexJob,
)
from app.services.knowledge_index import service, worker
from app.services.knowledge_index.claims import ground_claims
from app.services.retrieval.associations import EvidenceReader, search_associations, fuse_associations
from app.services import retrieval_service
from app.schemas.retrieval_strategy import AssociationBudget, RetrievalProfile

C1 = "甲校在2025年新增人工智能专业，培养方案面向智能技术应用，课程教学计划已经公布。"
C2 = "甲校的人工智能专业与乙实验室共建实训基地，为学生提供工程项目实践和设备使用条件。"
C3 = "丙公司在2024年发布水处理设备，产品在工业污水处理场景中提供过滤和净化服务。"


def entity(name, quote, *, kind="organization", scope=None):
    return {"name": name, "mention": name, "entity_type": kind, "scope": name if scope is None else scope, "quote": quote}


def payload(quote):
    if quote == C1:
        entities = [entity("甲校", quote), entity("人工智能专业", quote, kind="concept", scope="甲校")]
        predicate = "新增"
    elif quote == C2:
        entities = [entity("人工智能专业", quote, kind="concept", scope="甲校"), entity("乙实验室", quote)]
        predicate = "共建"
    else:
        entities = [entity("丙公司", quote), entity("水处理设备", quote, kind="product", scope="丙公司")]
        predicate = "发布"
    return {"entities": entities,
            "relations": [{"subject": 0, "object": 1, "predicate": predicate, "quote": quote}],
            "events": [{"text": quote, "quote": quote, "entities": [0, 1]}]}


async def claims_extractor(content, config):
    return payload(next(quote for quote in (C1, C2, C3) if quote in content))


@pytest.fixture
async def indexed(foundation):
    for model in (KnowledgeRelation, KnowledgeEvent, EventEntity):
        model.__table__.create(foundation.engine)
    with foundation.sessions() as db:
        db.get(KnowledgeBase, "kb").retrieval_profile = {
            "version": "new_profile_v2", "build_indexes": ["entities", "relations", "events"],
        }
        db.get(Chunk, "doc-chunk").content = C1
        db.get(DocumentCanonicalArtifact, "doc").canonical_markdown = C1
        db.commit()
    add_document(foundation, "second", content=C2)
    add_document(foundation, "noise", content=C3)
    with foundation.sessions() as db:
        assert service.reconcile(db) == 3
        db.commit()
    for _ in range(3):
        assert await worker.run_once(foundation.sessions, claims_extractor)
    with foundation.sessions() as db:
        states = db.scalars(select(KnowledgeIndexJob.state)).all()
        assert states == ["ready"] * 3, db.scalars(select(KnowledgeIndexJob.error)).all()
    return foundation


def budget(**overrides):
    return AssociationBudget(max_seeds=1, **overrides).model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["relations", "events"])
async def test_cross_document_chain_with_same_two_chunk_budget(indexed, kind):
    with indexed.sessions() as db:
        reader = EvidenceReader(db, "kb")
        direct = [{"chunk_id": "doc-chunk", "score": .9}]
        candidates, meta = search_associations(reader, "甲校有哪些实践资源", direct, kind, budget(), True)
        merged = fuse_associations(direct, candidates, 2, 60)
        assert all(item["rrf_score"] == item["score"] for item in merged)
        assert {item["chunk_id"] for item in merged} == {"doc-chunk", "second-chunk"}
        assert any(set(path["support_chunk_ids"]) == {"doc-chunk", "second-chunk"} for path in meta["paths"])
        assert all(item["chunk_id"] != "noise-chunk" for item in merged)
        if kind == "events":
            assert any(step["kind"] == "shared_entity" for path in meta["paths"] for step in path["steps"])
            assert all(step["kind"] != "extracted_relation" for path in meta["paths"] for step in path["steps"])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["relations", "events"])
@pytest.mark.parametrize("change", ["disable", "revision", "delete", "admission", "model"])
async def test_unusable_bridge_cannot_leak_through_association(indexed, kind, change):
    with indexed.sessions() as db:
        if change == "disable":
            db.get(Chunk, "second-chunk").is_enabled = False
        elif change == "revision":
            db.get(DocumentCanonicalArtifact, "second").parse_revision = "rev2"
        elif change == "delete":
            db.execute(Document.__table__.delete().where(Document.id == "second"))
        elif change == "admission":
            db.get(Chunk, "second-chunk").content = ""
        else:
            from app.db.models import ModelConfig
            db.get(ModelConfig, "model").model_name = "different-model"
        db.commit()
        candidates, meta = search_associations(EvidenceReader(db, "kb"), "甲校有哪些实践资源",
            [{"chunk_id": "doc-chunk"}], kind, budget(), True)
        assert "second-chunk" not in {item["chunk_id"] for item in candidates}
        assert all("second-chunk" not in path["support_chunk_ids"] for path in meta["paths"])


@pytest.mark.asyncio
async def test_same_name_different_scope_does_not_create_a_bridge(indexed):
    with indexed.sessions() as db:
        db.get(Chunk, "second-chunk").content = C2.replace("甲校", "另一学校")
        service.enqueue_document(db, db.get(Document, "second"))
        db.commit()

    async def different_scope(content, config):
        quote = C2.replace("甲校", "另一学校")
        raw = payload(C2)
        for item in raw["entities"]:
            item["quote"] = quote
        raw["entities"][0]["scope"] = "另一学校"
        raw["relations"][0]["quote"] = quote
        raw["events"][0].update(quote=quote, text=quote)
        return raw

    await worker.run_once(indexed.sessions, different_scope)
    with indexed.sessions() as db:
        for kind in ("relations", "events"):
            candidates, _ = search_associations(EvidenceReader(db, "kb"), "甲校有哪些实践资源",
                [{"chunk_id": "doc-chunk"}], kind, budget(), True)
            assert "second-chunk" not in {item["chunk_id"] for item in candidates}


@pytest.mark.asyncio
async def test_limits_and_no_answer_do_not_invent_paths(indexed):
    with indexed.sessions() as db:
        reader = EvidenceReader(db, "kb")
        for kind in ("relations", "events"):
            candidates, meta = search_associations(reader, "不存在的航天器", [], kind, budget(), True)
            assert not candidates and not meta["paths"]
            candidates, meta = search_associations(reader, "甲校实践资源", [{"chunk_id": "doc-chunk"}],
                kind, budget(max_candidates=1), True)
            assert len(candidates) <= 1
            assert all(len(path["support_chunk_ids"]) <= 1 for path in meta["paths"])


@pytest.mark.asyncio
async def test_cross_kb_read_has_no_nodes_or_paths(indexed):
    with indexed.sessions() as db:
        db.add(KnowledgeBase(id="other", name="另一库", retrieval_profile={"build_indexes": ["relations", "events"]}))
        db.commit()
        reader = EvidenceReader(db, "other")
        assert reader.capabilities() == {"relations": False, "events": False}
        for kind in ("relations", "events"):
            results, meta = search_associations(reader, "甲校", [{"chunk_id": "doc-chunk"}], kind, budget(), True)
            assert results == [] and meta["paths"] == []


@pytest.mark.parametrize("mutate", ["bad_index", "invented_predicate", "invented_time", "empty_events", "invented_quote"])
def test_claim_grounding_rejects_unbacked_output(mutate):
    raw = payload(C1)
    if mutate == "bad_index":
        raw["relations"][0]["object"] = 9
    elif mutate == "invented_predicate":
        raw["relations"][0]["predicate"] = "资助"
    elif mutate == "invented_time":
        raw["events"][0]["qualifiers"] = {"time": "2030"}
    elif mutate == "empty_events":
        raw["events"] = []
    else:
        raw["relations"][0]["quote"] = "伪造的支持引文"
    with pytest.raises(ValueError):
        ground_claims(C1, raw, ["relations", "events"])


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["semantic", "hybrid", "graph", "event_graph"])
async def test_four_routes_share_same_final_budget_and_original_sources(indexed, monkeypatch, family):
    monkeypatch.setattr(retrieval_service, "get_or_create_retrieval_config", lambda db: type("Config", (), retrieval_service.DEFAULT_RETRIEVAL_CONFIG)())
    with indexed.sessions() as db:
        initial, _ = search_associations(EvidenceReader(db, "kb"), "甲校", [{"chunk_id": "doc-chunk"}], "relations", budget(), True)
        direct = next(item for item in initial if item["chunk_id"] == "doc-chunk")
    direct.update(vector_rank=1, keyword_rank=1, vector_score=.9, keyword_score=.8)
    monkeypatch.setattr(retrieval_service, "vector_search", AsyncMock(return_value=([direct], "")))
    monkeypatch.setattr(retrieval_service, "keyword_search", Mock(return_value=[direct]))
    monkeypatch.setattr(retrieval_service, "attach_context", Mock())
    monkeypatch.setattr(retrieval_service, "build_parent_groups", Mock(return_value=[]))
    monkeypatch.setattr(retrieval_service, "record_audit", Mock())
    monkeypatch.setattr(retrieval_service, "get_embedding_runtime", lambda db: type("Runtime", (), {"provider": "test", "model_name": "fixed"})())
    strategy = {"family": "event_graph", "budget": budget(candidate_k=7)} if family == "event_graph" else {
        "family": "tiered", "tier": family, "budget": budget(candidate_k=7),
    }
    with indexed.sessions() as db:
        result = await retrieval_service.hybrid_search(db, None, "kb", {
            "query": "甲校有哪些实践资源", "strategy": strategy, "top_k": 2, "include_content": True,
        })
        assert result["success"], result
        assert retrieval_service.vector_search.call_args.args[3] == 7
        if family != "semantic":
            assert retrieval_service.keyword_search.call_args.args[3] == 7
        diagnostic = result["data"]["meta"]["retrieval"]["candidate_budget"]
        assert diagnostic["candidate_k"] == 7 and diagnostic["final_top_k"] == 2
        returned = {item["chunk_id"] for item in result["data"]["results"]}
        assert len(returned) <= 2
        if family in {"graph", "event_graph"}:
            assert returned == {"doc-chunk", "second-chunk"}
            assert result["data"]["meta"]["retrieval"]["association"]["executed"]
        else:
            assert returned == {"doc-chunk"}


@pytest.mark.asyncio
async def test_http_and_mcp_keep_cross_document_groups_and_path_completeness(indexed, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import routes_retrieval
    from app.core.security import get_current_user
    from app.db.session import get_db
    from app.integrations.mcp import tools
    from app.services.context.knowledge_context_service import build_knowledge_context
    from types import SimpleNamespace

    monkeypatch.setattr(retrieval_service, "get_or_create_retrieval_config", lambda db: SimpleNamespace(**retrieval_service.DEFAULT_RETRIEVAL_CONFIG))
    with indexed.sessions() as db:
        candidates, _ = search_associations(EvidenceReader(db, "kb"), "甲校", [{"chunk_id": "doc-chunk"}], "relations", budget(), True)
        direct = next(item for item in candidates if item["chunk_id"] == "doc-chunk")
    direct.update(vector_rank=1, keyword_rank=1, vector_score=.9, keyword_score=.8)
    monkeypatch.setattr(retrieval_service, "vector_search", AsyncMock(return_value=([direct], "")))
    monkeypatch.setattr(retrieval_service, "keyword_search", Mock(return_value=[direct]))
    monkeypatch.setattr(retrieval_service, "attach_context", Mock())
    monkeypatch.setattr(retrieval_service, "build_parent_groups", Mock(return_value=[]))
    monkeypatch.setattr(retrieval_service, "record_audit", Mock())
    monkeypatch.setattr(retrieval_service, "get_embedding_runtime", lambda db: SimpleNamespace(provider="test", model_name="fixed"))
    app = FastAPI()
    app.include_router(routes_retrieval.router)

    def database():
        with indexed.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: None
    strategy = {"family": "event_graph", "budget": budget()}
    with TestClient(app) as client:
        response = client.post("/knowledge-bases/kb/llm-context", json={
            "query_text": "甲校有哪些实践资源", "strategy": strategy, "return_mode": "evidence_groups",
            "top_k": 2,
        }).json()
        assert response["success"], response
        data = response["data"]
        assert {group["document_id"] for group in data["groups"]} == {"doc", "second"}
        paths = data["meta"]["retrieval"]["association"]["paths"]
        chain = next(path for path in paths if len(path["support_chunk_ids"]) == 2)
        assert chain["context_complete"] and len(chain["related_evidence_group_ids"]) == 2

    async def context_api(*, knowledge_base_id, payload):
        with indexed.sessions() as db:
            return await build_knowledge_context(db=db, actor=None, kb_id=knowledge_base_id, payload=payload)

    monkeypatch.setattr(tools, "get_default_client", lambda: SimpleNamespace(build_llm_context=context_api))
    result = await tools.search_context("甲校有哪些实践资源", "kb", strategy=strategy, match_count=2)
    assert result["success"]
    assert result["meta"]["retrieval"]["requested_strategy"]["family"] == "event_graph"
    assert any(path["context_complete"] and len(path["related_evidence_group_ids"]) == 2
               for path in result["meta"]["retrieval"]["association"]["paths"])


@pytest.mark.asyncio
async def test_unready_global_does_not_run_local_and_unready_strict_fails(indexed):
    from app.services.retrieval.policy_resolver import resolve_policy

    with pytest.raises(ValueError, match="社区报告未就绪"):
        resolve_policy({"strategy": {"family": "tiered", "tier": "graph", "graph_method": "global", "fallback_policy": "error"}}, capabilities={"relations": True})
    with pytest.raises(ValueError, match="DRIFT需要"):
        resolve_policy({"strategy": {"family": "tiered", "tier": "graph", "graph_method": "drift", "fallback_policy": "error"}}, capabilities={"relations": True})
    with indexed.sessions() as db:
        db.get(KnowledgeBase, "kb").retrieval_profile = {"build_indexes": ["entities"]}
        db.commit()
        result = await retrieval_service.hybrid_search(db, None, "kb", {
            "query": "甲校", "strategy": {"family": "event_graph", "fallback_policy": "error"},
        })
        assert not result["success"] and "未就绪" in result["message"]


def test_negation_and_time_qualifiers_remain_explicit():
    quote = "甲校在2025年未新增人工智能专业，现有培养方案继续实施。"
    raw = payload(C1)
    for item in raw["entities"]:
        item["quote"] = quote
    for kind in ("relations", "events"):
        raw[kind][0]["quote"] = quote
        raw[kind][0]["qualifiers"] = {"time": "2025", "polarity": "negated"}
    grounded = ground_claims(quote, raw, ["relations", "events"])
    assert grounded["relations"][0]["qualifiers"]["polarity"] == "negated"
    assert grounded["events"][0]["qualifiers"]["time"] == "2025"


def test_context_clipping_marks_chain_incomplete():
    from app.services.context.knowledge_context_service import KnowledgeContextService

    payload = {"chunks": [{"chunk_id": "a"}], "meta": {}}
    retrieval = {"meta": {"retrieval": {"association": {"paths": [{"support_chunk_ids": ["a", "b"]}]}}}}
    KnowledgeContextService._attach_retrieval_meta(payload, retrieval)
    assert payload["meta"]["retrieval"]["association"]["paths"][0]["context_complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("capability,other", [("relations", "events"), ("events", "relations")])
async def test_extraction_only_requests_selected_index(capability, other, monkeypatch):
    from app.services.knowledge_index import claims

    generator = AsyncMock(return_value={"entities": [], capability: []})
    monkeypatch.setattr(claims, "generate_json", generator)
    await claims.extract_claims(C1, {"_capabilities": [capability]})
    prompt = generator.call_args.args[2]
    assert capability in prompt and other not in prompt
    with pytest.raises(ValueError, match="缺少"):
        ground_claims(C1, {"entities": []}, [capability])


@pytest.mark.asyncio
async def test_partial_and_empty_capabilities_are_reported(indexed):
    with indexed.sessions() as db:
        status = service.index_status(db, "kb")
        assert status["graph_retrieval_ready"] and status["event_retrieval_ready"]
        assert status["counts"]["relations"] == 3
        assert status["counts"]["events"] == 3
        assert status["counts"]["entities"] >= 4
        db.get(Chunk, "second-chunk").is_enabled = False
        db.commit()
        reader = EvidenceReader(db, "kb")
        assert reader.coverage["relations"]["ready"] == 2
        assert reader.coverage["relations"]["total"] == 3


def test_migration_and_budget_validation(foundation):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    migration = importlib.import_module("app.db.migrations.versions.0019_relation_event_retrieval")
    with foundation.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.downgrade()
    with pytest.raises(ValueError):
        AssociationBudget(max_hops=3)
    assert "events" in RetrievalProfile(build_indexes=["events"]).build_indexes
