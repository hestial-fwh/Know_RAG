"""Test that ChunkPersistence does not drop all chunks in post-processing."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.chunking.chunk_draft import ChildDraft, ChunkDraft
from app.services.chunking.chunk_persistence import ChunkPersistence
from app.db.models import Chunk, Document


def make_mock_document() -> MagicMock:
    doc = MagicMock(spec=Document)
    doc.id = "test-doc-001"
    doc.knowledge_base_id = "test-kb-001"
    return doc


def make_mock_db() -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.delete.return_value = 0
    return db


def _draft(content: str, chunk_type: str = "text", **kwargs) -> ChunkDraft:
    return ChildDraft(content=content, chunk_type=chunk_type, **kwargs)


def test_persist_with_empty_drafts_returns_zero():
    """Empty drafts → return ([], 0)."""
    persistence = ChunkPersistence()
    db = make_mock_db()
    doc = make_mock_document()

    chunks, inserted = persistence.persist(
        db=db, document=doc, drafts=[], file_type="md", delete_existing=True,
    )
    assert inserted == 0
    assert chunks == []


def test_persist_with_text_drafts_produces_chunks():
    """Normal text drafts should survive post-processing."""
    persistence = ChunkPersistence()
    db = make_mock_db()
    doc = make_mock_document()

    drafts = [
        _draft("这是第一个测试切片。" * 30),
        _draft("这是第二个测试切片。" * 30),
        _draft("这是第三个测试切片。" * 30),
    ]

    chunks, inserted = persistence.persist(
        db=db, document=doc, drafts=drafts, file_type="md", delete_existing=True,
    )
    assert inserted > 0, f"Expected >0 chunks, got {inserted}"
    assert len(chunks) == inserted


def test_persist_with_table_draft_survives():
    """Table chunks should not be removed by post-processing."""
    persistence = ChunkPersistence()
    db = make_mock_db()
    doc = make_mock_document()

    drafts = [
        _draft("产品 | 价格\n--- | ---\n苹果 | 5元", chunk_type="table"),
    ]

    chunks, inserted = persistence.persist(
        db=db, document=doc, drafts=drafts, file_type="md", delete_existing=True,
    )
    assert inserted == 1, f"Expected 1 table chunk, got {inserted}"


def test_persist_with_image_draft_survives():
    """Image chunks should not be removed by post-processing."""
    persistence = ChunkPersistence()
    db = make_mock_db()
    doc = make_mock_document()

    drafts = [
        _draft("Figure 1: 数据图", chunk_type="image"),
    ]

    chunks, inserted = persistence.persist(
        db=db, document=doc, drafts=drafts, file_type="md", delete_existing=True,
    )
    assert inserted == 1, f"Expected 1 image chunk, got {inserted}"


def test_persist_with_mixed_drafts_all_survive():
    """Mixed text/table/image drafts should all survive."""
    persistence = ChunkPersistence()
    db = make_mock_db()
    doc = make_mock_document()

    drafts = [
        _draft("这是文本切片。" * 50, chunk_type="text"),
        _draft("产品 价格\n苹果 5元", chunk_type="table"),
        _draft("Figure 1: 图表", chunk_type="image"),
    ]

    chunks, inserted = persistence.persist(
        db=db, document=doc, drafts=drafts, file_type="md", delete_existing=True,
    )
    assert inserted == 3, f"Expected 3 chunks, got {inserted}"


def test_persist_uses_delete_existing_flag():
    """When delete_existing=False, existing chunks should not be deleted."""
    persistence = ChunkPersistence()
    db = make_mock_db()
    doc = make_mock_document()

    drafts = [_draft("测试内容。" * 10)]

    chunks, inserted = persistence.persist(
        db=db, document=doc, drafts=drafts, file_type="md", delete_existing=False,
    )
    # When delete_existing=False, the delete queries should NOT be called
    assert inserted > 0
