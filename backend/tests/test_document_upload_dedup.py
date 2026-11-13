"""上传内容去重：覆盖业务拒绝、并发兜底、存储清理与数据库边界。"""

import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import BackgroundTasks, UploadFile
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Document, KnowledgeBase, User
from app.services import document_service as service
from app.services.storage import minio_storage


@pytest.fixture
def uploads(monkeypatch):
    engine = create_engine("sqlite://")
    KnowledgeBase.__table__.create(engine)
    Document.__table__.create(engine)
    with Session(engine, autoflush=False) as db:
        db.add_all([KnowledgeBase(id="kb-1", name="one"), KnowledgeBase(id="kb-2", name="two")])
        db.commit()
        stored = {}

        def save(upload, key, *, fingerprint):
            payload = upload.file.read()
            assert fingerprint == (len(payload), hashlib.sha256(payload).hexdigest())
            stored[key] = payload
            return SimpleNamespace(size=fingerprint[0], sha256=fingerprint[1],
                                   uri=f"minio://test/{key}", bucket="test", object_key=key)

        storage = Mock(side_effect=save)
        cleanup = Mock()
        tasks = Mock()
        monkeypatch.setattr(service, "save_upload_file_to_minio", storage)
        monkeypatch.setattr(service, "delete_document_objects", cleanup)
        monkeypatch.setattr(service, "record_audit", Mock())
        monkeypatch.setattr(service.parse_task_service, "create_parse_task", tasks)
        monkeypatch.setattr(service, "document_to_dict", lambda db, d: {"id": d.id})
        yield SimpleNamespace(db=db, storage=storage, cleanup=cleanup, tasks=tasks,
                              actor=User(id="actor", username="admin", password_hash="x"))
    engine.dispose()


async def upload(ctx, payload=b"same PDF bytes", filename="first.pdf", kb="kb-1", **kwargs):
    return await service.upload_document(
        ctx.db, ctx.actor, UploadFile(filename=filename, file=BytesIO(payload)), kb,
        parse_immediately=kwargs.pop("parse_immediately", False), **kwargs,
    )


@pytest.mark.parametrize("filename", ["first.pdf", "renamed.pdf"])
async def test_same_content_is_rejected_before_storage_and_parse(uploads, filename):
    first = await upload(uploads)
    result = await upload(uploads, filename=filename, parse_immediately=True,
                    background_tasks=BackgroundTasks())
    assert first["success"] is True
    assert result["success"] is False
    assert result["data"]["existing_document_id"] == first["data"]["id"]
    assert "已存在于当前知识库" in result["message"]
    assert uploads.storage.call_count == 1
    uploads.tasks.assert_not_called()
    uploads.cleanup.assert_not_called()
    assert uploads.db.scalar(select(func.count()).select_from(Document)) == 1
    assert uploads.db.get(KnowledgeBase, "kb-1").document_count == 1


async def test_same_name_with_different_content_is_allowed(uploads):
    assert (await upload(uploads))["success"]
    assert (await upload(uploads, payload=b"different PDF bytes"))["success"]
    assert uploads.db.scalar(select(func.count()).select_from(Document)) == 2


async def test_same_content_in_different_knowledge_bases_is_allowed(uploads):
    assert (await upload(uploads))["success"]
    assert (await upload(uploads, kb="kb-2"))["success"]


@pytest.mark.parametrize("status", ["failed", "parsing", "parsed"])
async def test_existing_parse_status_does_not_allow_duplicate_upload(uploads, status):
    first = await upload(uploads)
    existing = uploads.db.get(Document, first["data"]["id"])
    existing.parse_status = status
    existing.enable_status = "disabled"
    uploads.db.commit()
    assert not (await upload(uploads))["success"]
    assert uploads.storage.call_count == 1


async def test_racing_upload_returns_existing_document_and_cleans_only_loser(uploads, monkeypatch):
    first = await upload(uploads)
    original_scalar = uploads.db.scalar
    calls = 0

    def miss_precheck(statement, *args, **kwargs):
        nonlocal calls
        calls += 1
        # 模拟并发请求预检查时尚未看见赢家，真实唯一索引负责拒绝插入。
        return None if calls == 1 else original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(uploads.db, "scalar", miss_precheck)
    result = await upload(uploads, filename="racing.pdf", parse_immediately=True,
                    background_tasks=BackgroundTasks())
    assert not result["success"]
    assert result["data"]["existing_document_id"] == first["data"]["id"]
    uploads.cleanup.assert_called_once()
    kb, loser_id = uploads.cleanup.call_args.args
    assert kb == "kb-1" and loser_id != first["data"]["id"]
    uploads.tasks.assert_not_called()
    assert original_scalar(select(func.count()).select_from(Document)) == 1
    assert uploads.db.get(KnowledgeBase, "kb-1").document_count == 1


async def test_other_database_error_keeps_generic_failure_and_cleans_object(uploads, monkeypatch):
    monkeypatch.setattr(uploads.db, "flush", Mock(side_effect=IntegrityError("insert", {},
                                                                            ValueError("other"))))
    result = await upload(uploads)
    assert result["message"] == "文档上传入库失败"
    uploads.cleanup.assert_called_once()


async def test_unique_index_also_rejects_direct_insert(uploads):
    first = await upload(uploads)
    existing = uploads.db.get(Document, first["data"]["id"])
    uploads.db.add(Document(id="direct", knowledge_base_id="kb-1", title="copy",
                            file_name="direct.pdf", file_type="pdf", file_hash=existing.file_hash))
    with pytest.raises(IntegrityError):
        uploads.db.commit()
    uploads.db.rollback()


def test_unknown_legacy_hashes_do_not_conflict(uploads):
    for i in range(2):
        uploads.db.add(Document(id=f"legacy-{i}", knowledge_base_id="kb-1", title="legacy",
                                file_name="old.pdf", file_type="pdf", file_hash=""))
    uploads.db.commit()


def test_fingerprint_streams_large_files_and_rewinds():
    payload = b"x" * (2 * 1024 * 1024 + 13)
    stream = BytesIO(payload)
    stream.seek(15)
    result = minio_storage.fingerprint_upload_file(UploadFile(filename="large.pdf", file=stream))
    assert result == (len(payload), hashlib.sha256(payload).hexdigest())
    assert stream.tell() == 0


def test_minio_reuses_precomputed_fingerprint(monkeypatch):
    upload_file = UploadFile(filename="test.pdf", file=BytesIO(b"pdf"))
    client = Mock()
    monkeypatch.setattr(minio_storage, "ensure_bucket", Mock())
    monkeypatch.setattr(minio_storage, "_minio_client", lambda: client)
    fingerprint = Mock(side_effect=AssertionError("不应重复计算文件指纹"))
    monkeypatch.setattr(minio_storage, "fingerprint_upload_file", fingerprint)
    stored = minio_storage.save_upload_file(upload_file, "kb/doc/test.pdf", fingerprint=(3, "hash"))
    assert stored.size == 3 and stored.sha256 == "hash"
    fingerprint.assert_not_called()
    assert client.put_object.call_args.kwargs["length"] == 3
