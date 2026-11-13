"""P2 集成验收：真实数据库事务、worker 重放、来源失效和权限范围。"""

from datetime import timedelta
import os
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, update, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_knowledge_index
from app.core.security import get_current_user
from app.db.models import (
    Chunk, Document, DocumentCanonicalArtifact, EntityAlias, KnowledgeBase,
    KnowledgeEntity, KnowledgeEvidence, KnowledgeIndexJob, KnowledgeIndexManifest, ModelConfig,
)
from app.db.session import get_db
from app.services.knowledge_index import service, worker
from app.services.knowledge_index.extractor import extract_entities, grounded_candidates

TEXT = "甲校于2025年新增人工智能专业，并与乙实验室合作开展实践教学，相关项目已在学校正式公布。"


@pytest.fixture
def foundation():
    schema = None
    if os.environ.get("KNOW_RAG_INDEX_TEST_POSTGRES") == "1":
        from app.core.config import settings

        schema = "p2_test_" + uuid4().hex
        admin = create_engine(settings.database_url)
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(settings.database_url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

    for model in (KnowledgeBase, Document, DocumentCanonicalArtifact, Chunk, ModelConfig,
                  KnowledgeIndexJob, KnowledgeIndexManifest, KnowledgeEntity, KnowledgeEvidence, EntityAlias):
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        kb = KnowledgeBase(id="kb", name="测试库", retrieval_profile={
            "version": "new_profile_v2", "build_indexes": ["entities"],
        })
        db.add(kb)
        db.add(ModelConfig(id="model", model_type="llm", provider="ollama", model_name="local-test", base_url="http://localhost:11434"))
        db.commit()
    value = SimpleNamespace(engine=engine, sessions=sessions)
    add_document(value, "doc")
    yield value
    engine.dispose()
    if schema:
        # schema 仅由本 fixture 的固定前缀和 UUID 生成，禁止清理任何业务 schema。
        assert schema.startswith("p2_test_") and len(schema) == 40
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def add_document(env, doc_id, *, kb_id="kb", content=TEXT):
    with env.sessions() as db:
        document = Document(id=doc_id, knowledge_base_id=kb_id, title="教学资料", file_name=f"{doc_id}.txt",
                            file_type="txt", parse_status="parsed", enable_status="enabled")
        db.add(document)
        db.flush()
        db.add(DocumentCanonicalArtifact(document_id=doc_id, knowledge_base_id=kb_id,
            parse_revision="rev1", canonical_hash="hash1", canonical_markdown=content))
        db.add(Chunk(id=f"{doc_id}-chunk", document_id=doc_id, knowledge_base_id=kb_id, chunk_index=0,
                     content=content, metadata_json={"canonical_parse_revision": "rev1"}))
        db.commit()


def candidate(content=TEXT, *, name="甲校", mention="甲校", scope=""):
    return {"entities": [{"name": name, "mention": mention, "entity_type": "organization", "scope": scope, "quote": content}]}


async def extractor(content, config):
    return candidate()


def enqueue(env):
    with env.sessions() as db:
        job, inserted = service.enqueue_document(db, db.get(Document, "doc"))
        db.commit()
        return job.id


def test_outbox_rolls_back_with_source_and_is_idempotent(foundation):
    with foundation.sessions() as db:
        job, inserted = service.enqueue_document(db, db.get(Document, "doc"))
        assert inserted
        db.rollback()
        assert db.scalar(select(func.count()).select_from(KnowledgeIndexJob)) == 0
        one, _ = service.enqueue_document(db, db.get(Document, "doc"))
        two, inserted = service.enqueue_document(db, db.get(Document, "doc"))
        assert one.id == two.id and not inserted
        db.commit()
    with foundation.sessions() as db:
        assert service.reconcile(db) == 0


@pytest.mark.asyncio
async def test_worker_publishes_grounded_evidence_and_does_not_replay_ready_job(foundation):
    job_id = enqueue(foundation)
    assert await worker.run_once(foundation.sessions, extractor)
    assert not await worker.run_once(foundation.sessions, extractor)
    with foundation.sessions() as db:
        assert db.get(KnowledgeIndexJob, job_id).state == "ready"
        evidence = service.valid_evidence(db, "kb", name="甲校")
        assert len(evidence) == 1
        assert evidence[0]["quote"] == TEXT
        assert evidence[0]["span_basis"] == "admitted_projection"
        status = service.index_status(db, "kb")
        assert status["state"] == "ready"
        assert status["graph_retrieval_ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disable_chunk", "disable_document", "delete_chunk", "delete_document", "revision", "content", "model", "disable_profile"])
async def test_changed_source_is_immediately_invisible(foundation, change):
    enqueue(foundation)
    await worker.run_once(foundation.sessions, extractor)
    with foundation.sessions() as db:
        chunk = db.get(Chunk, "doc-chunk")
        doc = db.get(Document, "doc")
        if change == "disable_chunk":
            chunk.is_enabled = False
        elif change == "disable_document":
            doc.enable_status = "disabled"
        elif change == "delete_chunk":
            db.execute(Chunk.__table__.delete().where(Chunk.id == chunk.id))
        elif change == "delete_document":
            # 使用 SQL 删除模拟生产批量清理，并验证数据库级联。
            db.execute(Document.__table__.delete().where(Document.id == "doc"))
        elif change == "revision":
            db.get(DocumentCanonicalArtifact, "doc").parse_revision = "rev2"
        elif change == "content":
            chunk.content = TEXT + "另有补充说明。"
        elif change == "model":
            db.get(ModelConfig, "model").model_name = "different"
        else:
            db.get(KnowledgeBase, "kb").retrieval_profile = {"version": "legacy_v1"}
        db.commit()
        assert service.valid_evidence(db, "kb") == []
        if change == "delete_document":
            assert db.scalar(select(func.count()).select_from(KnowledgeIndexJob)) == 0


def test_publication_refuses_old_revision(foundation):
    enqueue(foundation)
    with foundation.sessions() as db:
        work = worker.claim_job(db)
        db.get(DocumentCanonicalArtifact, "doc").parse_revision = "rev2"
        db.commit()
        assert not worker.publish(db, work, {}, {})
        assert db.get(KnowledgeIndexJob, work["id"]).state == "stale"
        assert db.scalar(select(func.count()).select_from(KnowledgeEvidence)) == 0


def test_expired_lease_cannot_publish_and_new_worker_can_recover(foundation):
    enqueue(foundation)
    with foundation.sessions() as db:
        first = worker.claim_job(db)
        db.execute(update(KnowledgeIndexJob).values(lease_until=worker.now() - timedelta(seconds=1)))
        db.commit()
        second = worker.claim_job(db)
        assert first["token"] != second["token"]
        staged = {"doc-chunk": grounded_candidates(first["input"]["sources"][0]["content"], candidate())}
        assert not worker.publish(db, first, staged, {})
        assert worker.publish(db, second, staged, {})
        assert db.get(KnowledgeIndexJob, second["id"]).attempts == 2


@pytest.mark.asyncio
async def test_failure_retries_are_bounded_and_manual_retry_is_idempotent(foundation):
    job_id = enqueue(foundation)

    async def broken(content, config):
        raise RuntimeError("private response must not be stored")

    for _ in range(worker.MAX_ATTEMPTS):
        assert await worker.run_once(foundation.sessions, broken)
        with foundation.sessions() as db:
            job = db.get(KnowledgeIndexJob, job_id)
            assert job.state == "failed" and "private" not in job.error
            job.lease_until = worker.now() - timedelta(seconds=1)
            db.commit()
    assert not await worker.run_once(foundation.sessions, broken)
    with foundation.sessions() as db:
        job, created = service.enqueue_document(db, db.get(Document, "doc"), retry=True)
        assert job.id == job_id and not created and job.attempts == 0
        db.commit()
    assert await worker.run_once(foundation.sessions, extractor)


@pytest.mark.asyncio
async def test_partial_extraction_reports_coverage(foundation):
    with foundation.sessions() as db:
        db.add(Chunk(id="second", document_id="doc", knowledge_base_id="kb", chunk_index=1,
                     content="失败样例：" + TEXT, metadata_json={"canonical_parse_revision": "rev1"}))
        db.commit()
    enqueue(foundation)

    calls = []
    async def partial(content, config):
        calls.append(content)
        if len(calls) == 2:
            raise ValueError("bad output")
        return candidate()

    await worker.run_once(foundation.sessions, partial)
    with foundation.sessions() as db:
        status = service.index_status(db, "kb")
        assert status["documents"]["partial"] == 1 and status["covered_documents"] == 0, (
            status, db.scalar(select(KnowledgeIndexJob)).error,
        )
        manifest = db.scalar(select(KnowledgeIndexManifest))
        assert manifest.coverage_json["processed_chunks"] == 1
        assert len(service.valid_evidence(db, "kb")) == 1


@pytest.mark.asyncio
async def test_cross_kb_and_same_name_do_not_merge(foundation):
    add_document(foundation, "other")
    with foundation.sessions() as db:
        service.reconcile(db)
        db.commit()
    await worker.run_once(foundation.sessions, extractor)
    await worker.run_once(foundation.sessions, extractor)
    with foundation.sessions() as db:
        assert len({item["entity_id"] for item in service.valid_evidence(db, "kb", name="甲校")}) == 2
        db.add(KnowledgeBase(id="private", name="另一库", retrieval_profile={"build_indexes": ["entities"]}))
        db.commit()
        assert service.valid_evidence(db, "private", name="甲校") == []


@pytest.mark.parametrize("payload", [
    candidate(content="伪造原文"), candidate(name="未出现的实体"), candidate(scope="未出现的限定"),
    {"entities": [{}]}, {"entities": [], "extra": True},
])
def test_ungrounded_extraction_is_rejected(payload):
    with pytest.raises(ValueError):
        grounded_candidates(TEXT, payload)


@pytest.mark.asyncio
async def test_cloud_model_is_rejected_before_network():
    with pytest.raises(ValueError, match="本机 Ollama"):
        await extract_entities(TEXT, {"provider": "openai", "model_name": "remote", "base_url": "https://example.com"})


def test_http_scope_and_task_payload_are_bounded(foundation):
    app = FastAPI()
    app.include_router(routes_knowledge_index.router)

    def database():
        with foundation.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin")
    path = "/knowledge-bases/kb/knowledge-index"
    with TestClient(app) as client:
        assert not client.post(path + "/build", json={"document_id": "other-kb-doc"}).json()["success"]
        assert client.post(path + "/build", json={"limit": 0}).status_code == 422
        assert client.post(path + "/build", json={}).json()["data"]["created_jobs"] == 1
        rows = client.get(path + "/jobs").json()["data"]["items"]
        assert rows[0]["state"] == "pending" and "input_json" not in rows[0]


def test_p2_migration_upgrade_downgrade(foundation):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    migration = importlib.import_module("app.db.migrations.versions.0018_knowledge_index_foundation")
    with foundation.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()
        assert "knowledge_evidence" in inspect(connection).get_table_names()


def test_actual_chunk_rebuild_and_outbox_share_transaction(foundation):
    from app.db.models import ChunkEmbedding
    from app.services.chunk_service import rebuild_document_chunks
    from app.services.chunker.models import ChunkerConfig

    if foundation.engine.dialect.name == "postgresql":
        pytest.skip("此主链路回滚用 SQLite 验证；PG 临时 schema 不引入公共 vector 类型")
    ChunkEmbedding.__table__.create(foundation.engine)
    with foundation.sessions() as db:
        document = db.get(Document, "doc")
        count = rebuild_document_chunks(db, document, TEXT, cfg_override=ChunkerConfig())
        assert count > 0
        job = db.scalar(select(KnowledgeIndexJob))
        artifact = db.get(DocumentCanonicalArtifact, "doc")
        assert job.input_json["parse_revision"] == artifact.parse_revision
        assert job.input_json["sources"]
        db.rollback()
    with foundation.sessions() as db:
        assert db.get(DocumentCanonicalArtifact, "doc").parse_revision == "rev1"
        assert db.get(Chunk, "doc-chunk") is not None
        assert db.scalar(select(func.count()).select_from(KnowledgeIndexJob)) == 0


def test_quarantined_ocr_cannot_enter_derived_input(foundation, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "document_field_content_admission_enabled", True)
    with foundation.sessions() as db:
        db.add(Chunk(id="ocr", document_id="doc", knowledge_base_id="kb", chunk_index=1,
            content="被隔离的图片文字：虚构人物与不存在的机构秘密合作", chunk_type="image_ocr_text",
            metadata_json={"canonical_parse_revision": "rev1", "chunk_policy_image_ocr_mode": "shadow_auxiliary"}))
        db.commit()
        snapshot = service.snapshot_document(db, db.get(Document, "doc"))
        assert all(s["chunk_id"] != "ocr" for s in snapshot["sources"])
        assert snapshot["excluded_chunks"] == 1


@pytest.mark.asyncio
async def test_restored_input_and_missing_manifest_are_requeued(foundation):
    original = enqueue(foundation)
    await worker.run_once(foundation.sessions, extractor)
    with foundation.sessions() as db:
        db.get(Chunk, "doc-chunk").is_enabled = False
        db.commit()
        service.reconcile(db)
        db.commit()
        assert db.get(KnowledgeIndexJob, original).state == "stale"
        db.get(Chunk, "doc-chunk").is_enabled = True
        db.commit()
        service.reconcile(db)
        db.commit()
        assert db.get(KnowledgeIndexJob, original).state == "pending"
    await worker.run_once(foundation.sessions, extractor)
    with foundation.sessions() as db:
        manifest = db.get(KnowledgeIndexManifest, original)
        # 先移除旧的空输入任务，确保重放测试只关注原来的有效输入。
        assert manifest is not None
        db.delete(manifest)
        db.commit()
        assert service.index_status(db, "kb")["state"] != "ready"
        service.reconcile(db)
        db.commit()
        assert db.get(KnowledgeIndexJob, original).state == "pending"


@pytest.mark.skipif(os.environ.get("KNOW_RAG_INDEX_TEST_POSTGRES") != "1", reason="需要独立 PG 测试 schema")
def test_postgres_concurrent_claims_are_distinct(foundation):
    from concurrent.futures import ThreadPoolExecutor

    add_document(foundation, "doc2")
    with foundation.sessions() as db:
        service.reconcile(db)
        db.commit()

    def claim():
        with foundation.sessions() as db:
            return worker.claim_job(db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: claim(), range(2)))
    assert all(claimed)
    assert len({job["id"] for job in claimed}) == 2
