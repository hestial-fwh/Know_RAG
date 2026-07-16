from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import UploadFile

from app.services import parse_task_service
from app.services.storage import minio_storage


def test_block_and_chunk_events_are_never_persisted():
    db = MagicMock()

    assert (
        parse_task_service.add_event(
            db,
            "task-1",
            "parsed_block",
            "parse",
            "block",
            {"preview_text": "large payload"},
        )
        is None
    )
    assert (
        parse_task_service.add_event(
            db,
            "task-1",
            "chunk_created",
            "chunk",
            "chunk",
            {"content_preview": "large payload"},
        )
        is None
    )
    db.get.assert_not_called()
    db.add.assert_not_called()


def test_terminal_task_update_deletes_transient_events():
    task = SimpleNamespace(
        id="task-1",
        status="running",
        started_at=None,
        finished_at=None,
        current_stage="chunk",
        progress=80,
        completed_steps=6,
        total_steps=7,
        stats={},
        error_message="",
    )
    db = MagicMock()

    parse_task_service.update_task(db, task, status="completed", stage="done", progress=100)

    assert task.status == "completed"
    assert task.finished_at is not None
    db.execute.assert_called_once()
    db.commit.assert_called_once()


def test_minio_upload_accepts_file_larger_than_old_30_mb_limit(monkeypatch):
    payload = b"x" * (31 * 1024 * 1024)
    upload = UploadFile(filename="large.pdf", file=BytesIO(payload))
    client = MagicMock()

    monkeypatch.setattr(minio_storage, "ensure_bucket", lambda: None)
    monkeypatch.setattr(minio_storage, "_minio_client", lambda: client)
    monkeypatch.setattr(minio_storage, "_bucket", lambda: "documents")

    stored = minio_storage.save_upload_file(upload, "kb/doc/large.pdf")

    assert stored.size == len(payload)
    client.put_object.assert_called_once()
    assert client.put_object.call_args.args[2] is upload.file
    assert client.put_object.call_args.kwargs["length"] == len(payload)
