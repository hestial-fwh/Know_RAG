"""P4工程验收：真实来源/社区持久化、Global调用预算与HTTP/MCP证据交付。"""

from datetime import timedelta
import json
import os
from unittest.mock import Mock

import pytest
from sqlalchemy import select, update, func

from test_association_retrieval import indexed, foundation, C1, C2, C3
from app.db.models import (
    Chunk, CommunityReport, Document, DocumentCanonicalArtifact, GraphCommunity, KnowledgeBase,
    KnowledgeGraphJob, ModelConfig,
)
from app.schemas.retrieval_strategy import AssociationBudget, RetrievalProfile
from app.services.knowledge_index import communities, service, worker
from app.services.knowledge_index.community_provider import cluster_graph
from app.services.retrieval import global_search


@pytest.fixture
async def community_db(indexed):
    for model in (KnowledgeGraphJob, GraphCommunity, CommunityReport):
        model.__table__.create(indexed.engine)
    with indexed.sessions() as db:
        kb = db.get(KnowledgeBase, "kb")
        kb.retrieval_profile = {**kb.retrieval_profile, "build_indexes": ["entities", "relations", "events", "communities"]}
        db.commit()
    return indexed


async def report_generator(content, config, prompt):
    sources = json.loads(content)
    citations = [{"chunk_id": source["chunk_id"], "quote": next(quote for quote in (C1, C2, C3) if quote in source["content"])} for source in sources]
    return {"title": "工程回放主题", "points": [{"text": "有来源的主题要点", "evidence": citations}]}


async def query_generator(content, config, prompt):
    points = json.loads(content)["points"]
    return {"points": [{"text": point["text"], "point_ids": [key], "score": 80} for key, point in points.items()]}


async def build_reports(env):
    with env.sessions() as db:
        job, created = communities.enqueue_graph(db, "kb")
        db.commit()
        assert job is not None
    assert await communities.run_graph_once(env.sessions, report_generator)
    return job.id


@pytest.mark.asyncio
async def test_community_outbox_is_transactional_idempotent_and_keeps_source_mapping(community_db):
    with community_db.sessions() as db:
        one, created = communities.enqueue_graph(db, "kb")
        assert created
        two, created = communities.enqueue_graph(db, "kb")
        assert one.id == two.id and not created
        db.rollback()
        assert db.scalar(select(func.count()).select_from(KnowledgeGraphJob)) == 0
    job_id = await build_reports(community_db)
    with community_db.sessions() as db:
        reports, state = communities.current_reports(db, "kb")
        assert len(reports) == 2 and state["state"] == "ready"
        status = service.index_status(db, "kb")
        assert status["counts"]["communities"] >= 2
        assert status["counts"]["reports"] == 2
        assert all(report["derived_summary"] for report in reports)
        assert {source["chunk_id"] for report in reports for source in report["sources"]} == {"doc-chunk", "second-chunk", "noise-chunk"}
        assert state["graph_revision"] == job_id


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "disable_chunk", "disable_document", "revision", "admission", "model", "disable_community"])
async def test_old_reports_immediately_disappear_after_dependency_change(community_db, change):
    await build_reports(community_db)
    with community_db.sessions() as db:
        if change == "delete":
            db.execute(Document.__table__.delete().where(Document.id == "second"))
        elif change == "disable_chunk":
            db.get(Chunk, "second-chunk").is_enabled = False
        elif change == "disable_document":
            db.get(Document, "second").enable_status = "disabled"
        elif change == "revision":
            db.get(DocumentCanonicalArtifact, "second").parse_revision = "revision2"
        elif change == "admission":
            db.get(Chunk, "second-chunk").content = ""
        elif change == "model":
            db.get(ModelConfig, "model").model_name = "changed"
        else:
            db.get(KnowledgeBase, "kb").retrieval_profile = {"build_indexes": ["entities", "relations"]}
        db.commit()
        reports, _ = communities.current_reports(db, "kb")
        assert reports == []


@pytest.mark.asyncio
async def test_global_map_reduce_returns_derived_points_with_original_support(community_db):
    await build_reports(community_db)
    with community_db.sessions() as db:
        candidates, info = await global_search.retrieve_global(db, "kb", "全库的主要主题", AssociationBudget().model_dump(), 3, True, query_generator)
        assert len(candidates) == 3 and len(info["derived_summaries"]) == 2
        assert info["model_calls"] == 3 and info["provider"] == "know_rag_global_map_reduce_v1"
        assert not info["coverage_incomplete"]
        assert all(row["match_type"] == "global_support" for row in candidates)
        assert all(point["derived_summary"] and point["evidence"] for point in info["derived_summaries"])


@pytest.mark.asyncio
async def test_global_respects_total_model_and_raw_evidence_budgets(community_db):
    await build_reports(community_db)
    with community_db.sessions() as db:
        budget = AssociationBudget(max_model_calls=2).model_dump()
        candidates, info = await global_search.retrieve_global(db, "kb", "主题", budget, 1, True, query_generator)
        assert len(candidates) <= 1 and info["model_calls"] <= 2
        assert info["coverage_incomplete"]
        assert all({cite["chunk_id"] for cite in point["evidence"]} <= {row["chunk_id"] for row in candidates}
                   for point in info["derived_summaries"])


@pytest.mark.asyncio
async def test_global_no_answer_does_not_invent_summary_or_reduce(community_db):
    await build_reports(community_db)

    async def empty(content, config, prompt):
        return {"points": []}

    with community_db.sessions() as db:
        candidates, info = await global_search.retrieve_global(db, "kb", "不存在的主题", AssociationBudget().model_dump(), 3, True, empty)
        assert not candidates and not info["derived_summaries"]
        assert info["model_calls"] == 2


@pytest.mark.asyncio
async def test_global_exact_table_copies_share_one_raw_evidence_slot(community_db, monkeypatch):
    # 只替换已发布报告入口，实际执行Global、结果映射和生产来源去重，验证Top K按同源计数。
    with community_db.sessions() as db:
        chunk = db.get(Chunk, "doc-chunk")
        chunk.chunk_type, chunk.start_at, chunk.end_at = "table", 0, len(C1)
        chunk.content_hash = communities.digest(C1)
        chunk.metadata_json = {"source_position_is_exact": True, "canonical_parse_revision": "rev1", "source_node_ids": ["table1"]}
        duplicate = Chunk(**{column.name: getattr(chunk, column.name) for column in Chunk.__table__.columns})
        duplicate.id = "duplicate-chunk"
        db.add(duplicate)
        db.commit()
    sources = [{"chunk_id": chunk_id, "document_id": "doc", "parse_revision": "rev1", "content": C1}
               for chunk_id in ("doc-chunk", "duplicate-chunk")]
    report = {"report_id": "report", "sources": sources, "points": [{"text": "同一表格",
        "evidence": [{"chunk_id": source["chunk_id"], "quote": C1} for source in sources]}]}
    monkeypatch.setattr(global_search, "current_reports", lambda db, kb: ([report], {
        "graph_revision": "revision", "provider": "native", "model": {}, "coverage": {"incomplete": False},
    }))
    with community_db.sessions() as db:
        candidates, info = await global_search.retrieve_global(db, "kb", "表格主题", AssociationBudget().model_dump(), 1, True, query_generator)
        assert len(candidates) == 1 and len(info["derived_summaries"]) == 1
        point = info["derived_summaries"][0]
        assert {cite["returned_chunk_id"] for cite in point["evidence"]} == {candidates[0]["chunk_id"]}
        assert set(candidates[0]["source_chunk_ids"]) == {source["chunk_id"] for source in sources}


@pytest.mark.asyncio
async def test_generation_does_not_publish_stale_graph_and_expired_worker_cannot_publish(community_db):
    with community_db.sessions() as db:
        communities.enqueue_graph(db, "kb")
        db.commit()
        work = worker.claim_job(db, KnowledgeGraphJob)
        db.execute(update(KnowledgeGraphJob).values(lease_until=worker.now() - timedelta(seconds=1)))
        db.commit()
        assert not communities.publish_graph(db, work, [], {})
        replacement = worker.claim_job(db, KnowledgeGraphJob)
        db.get(Chunk, "second-chunk").is_enabled = False
        db.commit()
        assert not communities.publish_graph(db, replacement, [], {})
        assert db.get(KnowledgeGraphJob, replacement["id"]).state == "stale"


@pytest.mark.asyncio
async def test_partial_report_failure_is_not_full_coverage(community_db):
    with community_db.sessions() as db:
        communities.enqueue_graph(db, "kb")
        db.commit()

    async def partial(content, config, prompt):
        if "丙公司" in content:
            raise ValueError("synthetic failure")
        return await report_generator(content, config, prompt)

    await communities.run_graph_once(community_db.sessions, partial)
    with community_db.sessions() as db:
        reports, state = communities.current_reports(db, "kb")
        assert len(reports) == 1 and state["state"] == "partial" and state["coverage"]["incomplete"]


@pytest.mark.asyncio
async def test_cross_kb_report_scope_is_empty(community_db):
    await build_reports(community_db)
    with community_db.sessions() as db:
        db.add(KnowledgeBase(id="private", name="另一库", retrieval_profile={"build_indexes": ["relations", "communities"]}))
        db.commit()
        assert communities.current_reports(db, "private")[0] == []


def test_report_and_global_ids_require_real_support():
    with pytest.raises(ValueError):
        communities.validate_report({"title": "主题", "points": [{"text": "伪造", "evidence": [{"chunk_id": "other", "quote": C1}]}]}, [{"chunk_id": "a", "content": C1}])
    with pytest.raises(ValueError):
        global_search.supported_points({"points": [{"text": "伪造", "score": 80, "point_ids": ["unknown"]}]}, {})
    with pytest.raises(ValueError):
        communities.validate_report({"title": "空", "points": []}, [])
    with pytest.raises(ValueError):
        RetrievalProfile(build_indexes=["communities"])


def test_context_hides_summary_when_quote_is_not_returned():
    from app.services.context.knowledge_context_service import KnowledgeContextService

    payload = {"chunks": [{"chunk_id": "a", "content": "被限长的正文"}], "meta": {}}
    retrieval = {"meta": {"retrieval": {"global": {"coverage_incomplete": False, "derived_summaries": [
        {"derived_summary": True, "text": "主题", "evidence": [{"chunk_id": "a", "quote": "完整支持引文"}]}]}}}}
    KnowledgeContextService._attach_retrieval_meta(payload, retrieval)
    meta = payload["meta"]["retrieval"]["global"]
    assert not meta["derived_summaries"] and meta["withheld_summaries"] == 1 and meta["coverage_incomplete"]


def test_p4_migration_upgrade_downgrade(foundation):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module("app.db.migrations.versions.0020_graph_communities")
    with foundation.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.downgrade()


@pytest.mark.asyncio
async def test_global_http_mcp_separates_summaries_and_never_calls_base_embedding(community_db, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import routes_retrieval, routes_knowledge_base
    from app.core.security import get_current_user
    from app.db.session import get_db
    from app.integrations.mcp import tools
    from app.services import retrieval_service
    from app.services.context.knowledge_context_service import build_knowledge_context

    await build_reports(community_db)
    monkeypatch.setattr(retrieval_service, "get_or_create_retrieval_config", lambda db: SimpleNamespace(**retrieval_service.DEFAULT_RETRIEVAL_CONFIG))
    monkeypatch.setattr(retrieval_service, "vector_search", Mock(side_effect=AssertionError("Global不调用切片向量")))
    monkeypatch.setattr(retrieval_service, "keyword_search", Mock(side_effect=AssertionError("Global不调用BM25")))
    monkeypatch.setattr(retrieval_service, "attach_context", Mock())
    monkeypatch.setattr(retrieval_service, "build_parent_groups", Mock(return_value=[]))
    monkeypatch.setattr(retrieval_service, "record_audit", Mock())
    original = global_search.retrieve_global

    async def controlled(db, kb_id, query, budget, top_k, include_content):
        return await original(db, kb_id, query, budget, top_k, include_content, query_generator)

    monkeypatch.setattr(global_search, "retrieve_global", controlled)
    app = FastAPI()
    app.include_router(routes_retrieval.router)
    app.include_router(routes_knowledge_base.router)
    from fastapi.exceptions import RequestValidationError
    from app.main import validation_exception_handler
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    def database():
        with community_db.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: None
    strategy = {"family": "tiered", "tier": "graph", "graph_method": "global", "fallback_policy": "error"}
    with TestClient(app) as client:
        result = client.post("/knowledge-bases/kb/llm-context", json={
            "query_text": "资料主要主题", "strategy": strategy, "return_mode": "evidence_groups", "top_k": 3,
        }).json()
        assert result["success"], result
        data = result["data"]
        meta = data["meta"]["retrieval"]["global"]
        assert len(meta["derived_summaries"]) == 2 and all(summary["context_complete"] for summary in meta["derived_summaries"])
        assert {group["document_id"] for group in data["groups"]} == {"doc", "second", "noise"}
        assert client.put("/knowledge-bases/kb", json={"retrieval_profile": {"build_indexes": ["communities"]}}).status_code == 422

    async def context_api(*, knowledge_base_id, payload):
        with community_db.sessions() as db:
            return await build_knowledge_context(db=db, actor=None, kb_id=knowledge_base_id, payload=payload)

    monkeypatch.setattr(tools, "get_default_client", lambda: SimpleNamespace(build_llm_context=context_api))
    result = await tools.search_context("资料主要主题", "kb", strategy=strategy, match_count=3)
    assert result["success"] and len(result["meta"]["retrieval"]["global"]["derived_summaries"]) == 2


@pytest.mark.asyncio
async def test_report_validation_rejects_tampering_and_stale_during_global(community_db):
    await build_reports(community_db)
    with community_db.sessions() as db:
        report = db.scalar(select(CommunityReport))
        report.findings_json = [{"text": "伪造", "evidence": [{"chunk_id": "private", "quote": "越界材料"}]}]
        db.commit()
        reports, info = communities.current_reports(db, "kb")
        assert len(reports) == 1 and info["coverage"]["invalid_reports"] == 1

    async def changed(content, config, prompt):
        with community_db.sessions() as other:
            other.get(Chunk, "doc-chunk").is_enabled = False
            other.commit()
        return await query_generator(content, config, prompt)

    with community_db.sessions() as db:
        with pytest.raises(ValueError, match="失效"):
            await global_search.retrieve_global(db, "kb", "主题", AssociationBudget().model_dump(), 3, True, changed)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("KNOW_RAG_GRAPHRAG_PYTHON"), reason="需要已授权安装的官方隔离运行环境")
async def test_official_provider_executes_real_leiden_and_parquet_roundtrip(community_db):
    with community_db.sessions() as db:
        kb = db.get(KnowledgeBase, "kb")
        kb.retrieval_profile = {**kb.retrieval_profile, "community": {"provider": "microsoft_graphrag_2_7_0"}}
        db.commit()
        snapshot = communities.graph_snapshot(db, "kb")
        output = cluster_graph(snapshot)
        assert output and all(row["text_unit_ids"] for row in output)
    await build_reports(community_db)
    with community_db.sessions() as db:
        reports, state = communities.current_reports(db, "kb")
        assert reports and state["provider"] == "microsoft_graphrag_2_7_0"
        assert all(report["provider"]["version"] == "2.7.0" for report in reports)


@pytest.mark.asyncio
async def test_global_background_mode_returns_summaries_and_support(community_db):
    await build_reports(community_db)
    with community_db.sessions() as db:
        summaries, support, meta = await global_search.retrieve_global_background(
            db, "kb", "全库的主要主题",
            AssociationBudget(max_global_reports=2, max_global_model_calls=2).model_dump(),
            3, True, query_generator,
        )
        assert meta["executed"] is True and meta["status"] == "ready"
        assert meta["model_calls"] == 2
        assert summaries and support
        assert all(item["derived"] and not item["citation_allowed"] for item in summaries)
        assert all(item["evidence_chunk_ids"] for item in summaries)
        assert all(item["match_type"] == "global_support" for item in support)


@pytest.mark.asyncio
async def test_global_background_mode_degrades_without_raising(community_db):
    with community_db.sessions() as db:
        summaries, support, meta = await global_search.retrieve_global_background(
            db, "kb", "主题",
            AssociationBudget(max_global_reports=2, max_global_model_calls=2).model_dump(),
            3, True, query_generator,
        )
        assert summaries == [] and support == []
        assert meta["executed"] is False and meta["status"] == "unavailable"
