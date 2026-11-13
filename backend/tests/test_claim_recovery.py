"""实体名称对齐、逐条校验和有界重试的回归测试。"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from test_knowledge_index_foundation import foundation, add_document, candidate, enqueue, TEXT
from app.db.models import (
    Chunk, Document, EventEntity, KnowledgeBase, KnowledgeEntity, KnowledgeEvent,
    KnowledgeEvidence, KnowledgeIndexJob, KnowledgeIndexManifest, KnowledgeRelation,
)
from app.services.knowledge_index import claims, service, worker
from app.api import routes_knowledge_index
from app.core.security import get_current_user
from app.db.session import get_db


def raw_claims():
    return {"entities": [candidate()["entities"][0],
            {**candidate(name="人工智能专业", mention="人工智能专业")["entities"][0], "entity_type": "concept"}],
            "relations": [{"subject": 0, "object": 1, "predicate": "新增", "quote": TEXT}], "events": []}


def enable_relations(env):
    for model in (KnowledgeRelation, KnowledgeEvent, EventEntity):
        model.__table__.create(env.engine)
    with env.sessions() as db:
        db.get(KnowledgeBase, "kb").retrieval_profile = {"build_indexes": ["entities", "relations"]}
        db.commit()


@pytest.mark.asyncio
async def test_named_endpoints_restore_omitted_but_grounded_entity(monkeypatch):
    raw = raw_claims()
    raw["entities"] = raw["entities"][:1]
    raw["relations"][0].update(subject="甲校", object="人工智能专业")
    monkeypatch.setattr(claims, "generate_json", AsyncMock(return_value=raw))
    result = await claims.extract_claims(TEXT, {"reference_mode": "names", "_capabilities": ["relations"]})
    grounded = claims.ground_claims(TEXT, result, ["relations"])
    assert grounded["entities"][1]["name"] == "人工智能专业"
    assert grounded["entities"][1]["entity_type"] == "other"
    assert grounded["relations"][0]["object"] == 1


def test_named_format_alignment_keeps_original_mentions():
    text = "ＡＢＣ学院开设计算机专业。"
    raw = {"entities": [
        {"name": name, "mention": name, "entity_type": "concept", "scope": "", "quote": text}
        for name in ("ＡＢＣ学院", "计算机专业")],
        "relations": [{"subject": " ABC学院 ", "object": "计算机专业", "predicate": "开设", "quote": text}]}
    result = claims.resolve_named_claims(text, raw, [text])
    grounded = claims.ground_claims(text, result, ["relations"])
    assert grounded["entities"][0]["name"] == "ＡＢＣ学院"
    assert grounded["relations"][0]["subject"] == 0


def test_explicit_alias_aligns_without_substring_guessing():
    text = "甲学院，简称甲院，开设计算机专业。"
    raw = {"entities": [
        {"name": "甲学院", "mention": "甲院", "entity_type": "organization", "scope": "", "quote": text},
        {"name": "计算机专业", "mention": "计算机专业", "entity_type": "concept", "scope": "", "quote": text}],
        "relations": [{"subject": "甲院", "object": "计算机专业", "predicate": "开设", "quote": text}]}
    result = claims.resolve_named_claims(text, raw, [text])
    assert len(result["entities"]) == 2 and result["relations"][0]["subject"] == 0
    raw["relations"][0]["subject"] = "甲学校"
    errors = []
    result = claims.resolve_named_claims(text, raw, [text], errors)
    assert not result["relations"] and errors[0]["code"] == "unresolved_reference"


def test_ambiguous_scopes_are_not_merged():
    text = "甲校与乙校的教务处负责课程。"
    raw = {"entities": [
        {"name": "教务处", "mention": "教务处", "entity_type": "organization", "scope": scope, "quote": text}
        for scope in ("甲校", "乙校")],
        "relations": [{"subject": "教务处", "object": "课程", "predicate": "负责", "quote": text}]}
    errors = []
    result = claims.resolve_named_claims(text, raw, [text], errors)
    assert len(result["entities"]) == 2 and not result["relations"]
    assert errors


def test_item_validation_keeps_valid_claims_and_remaps_indices():
    raw = raw_claims()
    raw["entities"].insert(0, {**raw["entities"][0], "name": "不存在"})
    raw["relations"][0].update(subject=1, object=2)
    raw["relations"].append({"subject": 1, "object": 2, "predicate": "虚构关系", "quote": TEXT})
    errors = []
    result = claims.ground_claims(TEXT, raw, ["relations"], errors=errors)
    assert len(result["entities"]) == 2 and len(result["relations"]) == 1
    assert result["relations"][0]["subject"] == 0 and result["relations"][0]["object"] == 1
    assert len(errors) == 2
    with pytest.raises(ValueError):
        claims.ground_claims(TEXT, raw, ["relations"])


def test_duplicate_entities_keep_reference_indices_valid():
    raw = raw_claims()
    raw["entities"].append(dict(raw["entities"][0]))
    raw["relations"][0]["subject"] = 2
    result = claims.ground_claims(TEXT, raw, ["relations"])
    assert len(result["entities"]) == 2
    assert result["relations"][0]["subject"] == 0


@pytest.mark.asyncio
async def test_partial_item_retry_preserves_good_chunks_and_claims(foundation):
    enable_relations(foundation)
    with foundation.sessions() as db:
        db.add(Chunk(id="second", document_id="doc", knowledge_base_id="kb", chunk_index=1,
                     content=TEXT, metadata_json={"canonical_parse_revision": "rev1"}))
        db.commit()
    job_id = enqueue(foundation)
    calls = []

    async def extract(content, config):
        calls.append(content)
        raw = raw_claims()
        if len(calls) == 2:
            raw["relations"].append({"subject": 0, "object": 1, "predicate": "无依据", "quote": TEXT})
        return raw

    await worker.run_once(foundation.sessions, extract)
    with foundation.sessions() as db:
        manifest = db.get(KnowledgeIndexManifest, job_id)
        assert manifest.state == "partial", (calls, manifest.coverage_json)
        assert manifest.coverage_json["processed_chunks"] == 2
        assert manifest.coverage_json["rejected_items"]["second"]
        assert db.scalar(select(func.count()).select_from(KnowledgeRelation)) == 2
        job = db.get(KnowledgeIndexJob, job_id)
        job.lease_until = worker.now() - timedelta(seconds=1)
        db.commit()
    calls.clear()

    async def recovered(content, config):
        calls.append(content)
        # 本次只给出有效实体，上一轮有效关系仍应保留。
        return {**raw_claims(), "relations": []}

    assert await worker.run_once(foundation.sessions, recovered)
    assert len(calls) == 1
    with foundation.sessions() as db:
        assert db.get(KnowledgeIndexJob, job_id).state == "ready"
        assert db.get(KnowledgeIndexJob, job_id).attempts == 2
        assert db.scalar(select(func.count()).select_from(KnowledgeRelation)) == 2
        assert db.scalar(select(func.count()).select_from(KnowledgeEvidence)) == 4


@pytest.mark.asyncio
async def test_empty_event_retains_entities_but_stays_partial(foundation):
    enable_relations(foundation)
    with foundation.sessions() as db:
        db.get(KnowledgeBase, "kb").retrieval_profile = {"build_indexes": ["events"]}
        db.commit()
    job_id = enqueue(foundation)
    await worker.run_once(foundation.sessions, AsyncMock(return_value={**raw_claims(), "relations": []}))
    with foundation.sessions() as db:
        assert db.get(KnowledgeIndexJob, job_id).state == "partial"
        assert db.scalar(select(func.count()).select_from(KnowledgeEvidence)) == 2
        assert db.scalar(select(func.count()).select_from(KnowledgeEvent)) == 0


@pytest.mark.asyncio
async def test_partial_retries_stop_at_limit_and_batch_retry_is_bounded(foundation):
    enable_relations(foundation)
    add_document(foundation, "other")
    with foundation.sessions() as db:
        service.reconcile(db)
        db.commit()
    raw = raw_claims()
    raw["relations"][0]["predicate"] = "没有依据"
    for _ in range(2 * worker.MAX_ATTEMPTS):
        assert await worker.run_once(foundation.sessions, AsyncMock(return_value=raw))
        with foundation.sessions() as db:
            for job in db.scalars(select(KnowledgeIndexJob)):
                job.lease_until = worker.now() - timedelta(seconds=1)
            db.commit()
    assert not await worker.run_once(foundation.sessions, AsyncMock(return_value=raw))
    with foundation.sessions() as db:
        assert service.retry_incomplete(db, "kb", limit=1) == 1
        assert service.retry_incomplete(db, "kb", limit=0) == 0
        db.commit()
        assert db.scalar(select(func.count()).select_from(KnowledgeIndexJob).where(KnowledgeIndexJob.state == "pending")) == 1


@pytest.mark.asyncio
async def test_http_batch_retry_keeps_ready_jobs_and_is_idempotent(foundation):
    add_document(foundation, "other")
    with foundation.sessions() as db:
        service.reconcile(db)
        db.commit()
    await worker.run_once(foundation.sessions, AsyncMock(return_value=candidate()))
    await worker.run_once(foundation.sessions, AsyncMock(return_value={"entities": [{"name": "无原文"}]}))
    app = FastAPI()
    app.include_router(routes_knowledge_index.router)

    def database():
        with foundation.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin")
    with TestClient(app) as client:
        path = "/knowledge-bases/kb/knowledge-index/build"
        result = client.post(path, json={"retry": True, "limit": 1}).json()
        assert result["success"] and result["data"] == {"created_jobs": 0, "retried_jobs": 1}
        assert client.post(path, json={"retry": True}).json()["data"]["retried_jobs"] == 0
    with foundation.sessions() as db:
        assert sorted(db.scalars(select(KnowledgeIndexJob.state)).all()) == ["pending", "ready"]


@pytest.mark.asyncio
async def test_scoped_alias_merges_across_documents_and_preserves_names(foundation):
    text = "甲学校（简称甲校）开展教学。"
    add_document(foundation, "other", content=text)
    with foundation.sessions() as db:
        db.get(Chunk, "doc-chunk").content = text
        service.reconcile(db)
        db.commit()
    responses = iter([
        candidate(text, name="甲学校", mention="甲校", scope="甲学校"),
        candidate(text, name="甲校", mention="甲校", scope="甲学校"),
    ])

    async def extract(content, config):
        return next(responses)

    await worker.run_once(foundation.sessions, extract)
    await worker.run_once(foundation.sessions, extract)
    with foundation.sessions() as db:
        evidence = service.valid_evidence(db, "kb", name="甲校")
        assert len(evidence) == 2 and len({item["entity_id"] for item in evidence}) == 1
        assert db.scalar(select(func.count()).select_from(KnowledgeEntity)) == 1


@pytest.mark.asyncio
async def test_disabled_alias_evidence_does_not_merge_new_document(foundation):
    text = "甲学校（简称甲校）开展教学。"
    with foundation.sessions() as db:
        db.get(Chunk, "doc-chunk").content = text
        db.commit()
    enqueue(foundation)
    await worker.run_once(foundation.sessions, AsyncMock(return_value=candidate(text, name="甲学校", mention="甲校", scope="甲学校")))
    with foundation.sessions() as db:
        db.get(Chunk, "doc-chunk").is_enabled = False
        db.commit()
    add_document(foundation, "other", content=text)
    with foundation.sessions() as db:
        service.enqueue_document(db, db.get(Document, "other"))
        db.commit()
    await worker.run_once(foundation.sessions, AsyncMock(return_value=candidate(text, name="甲校", mention="甲校", scope="甲学校")))
    with foundation.sessions() as db:
        rows = db.scalars(select(KnowledgeEntity)).all()
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_same_chunk_alias_resolution_with_production_autoflush(foundation):
    foundation.sessions.configure(autoflush=False)
    text = "甲学校（简称甲校）开展教学。"
    with foundation.sessions() as db:
        db.get(Chunk, "doc-chunk").content = text
        db.commit()
    job_id = enqueue(foundation)
    payload = {"entities": [
        candidate(text, name="甲学校", mention="甲校", scope="甲学校")["entities"][0],
        candidate(text, name="甲校", mention="甲校", scope="甲学校")["entities"][0],
    ]}
    await worker.run_once(foundation.sessions, AsyncMock(return_value=payload))
    with foundation.sessions() as db:
        assert db.get(KnowledgeIndexJob, job_id).state == "ready"
        assert db.scalar(select(func.count()).select_from(KnowledgeEntity)) == 1
        assert db.scalar(select(func.count()).select_from(KnowledgeEvidence)) == 2


def test_manual_retry_preserves_running_lease(foundation):
    job_id = enqueue(foundation)
    with foundation.sessions() as db:
        work = worker.claim_job(db)
    with foundation.sessions() as db:
        job, _ = service.enqueue_document(db, db.get(Document, "doc"), retry=True)
        assert job.id == job_id and job.state == "running"
        assert job.attempts == 1 and job.lease_token == work["token"]
        assert service.retry_incomplete(db, "kb") == 0
        db.commit()
