"""Test that fallback flat text chunking activates when structured rebuild produces zero drafts."""

from types import SimpleNamespace
from unittest.mock import patch

from app.services.chunking.rebuild_service import (
    ChunkRebuildService,
    _build_fallback_text_drafts,
)
from app.services.document_parser.types import ParsedBlock, ParsedDocument


def test_fallback_text_drafts_produces_chunks():
    """Build fallback drafts from parsed document blocks."""
    cfg = SimpleNamespace(
        strategy="auto",
        separators=[],
        chunk_size=512,
        chunk_overlap=64,
        child_chunk_size=384,
        child_chunk_overlap=64,
        parent_chunk_size=4096,
        token_limit=0,
        languages=[],
        enable_parent_child=False,
        enable_structured_parent_child=False,
        enable_table_row_split=False,
        enable_image_ocr_child=False,
    )
    doc = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(block_type="text", text="段落一：" + "这是测试文本。" * 50, order_index=0),
            ParsedBlock(block_type="text", text="段落二：" + "更多测试内容。" * 50, order_index=1),
            ParsedBlock(block_type="table", text="A B C\n1 2 3", order_index=2),
        ],
    )

    drafts = _build_fallback_text_drafts(doc, "md", cfg)

    assert len(drafts) > 0, f"Expected non-empty fallback drafts, got {len(drafts)}"

    # Fallback drafts are text children, not parent_text
    assert all(d.chunk_type == "text" for d in drafts), "All fallback drafts should be text type"
    assert all(d.parent_ref is None for d in drafts), "Fallback drafts should have no parent"

    # Fallback metadata should be set
    for draft in drafts:
        meta = draft.metadata or {}
        assert meta.get("parser") == "fallback_flat_text_after_structured_empty"
        assert meta.get("chunk_strategy") == "fallback_flat_text"
        assert draft.flags.get("fallback_flat_text") is True


def test_build_drafts_falls_back_when_sections_are_empty():
    """When make_structured_sections returns empty, fallback should activate."""
    cfg = SimpleNamespace(
        strategy="auto",
        separators=[],
        chunk_size=512,
        chunk_overlap=64,
        child_chunk_size=384,
        child_chunk_overlap=64,
        parent_chunk_size=4096,
        token_limit=0,
        languages=[],
        enable_parent_child=True,
        enable_structured_parent_child=True,
        enable_table_row_split=False,
        enable_image_ocr_child=False,
    )
    doc = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(block_type="text", text="这是正文内容。" * 80, order_index=0),
            ParsedBlock(block_type="text", text="更多正文内容。" * 80, order_index=1),
        ],
    )

    with patch(
        "app.services.chunking.rebuild_service.make_structured_sections",
        return_value=[],
    ):
        drafts = ChunkRebuildService().build_drafts(parsed_document=doc, file_type="md", cfg=cfg)

    assert len(drafts) > 0, f"Fallback should produce drafts, got {len(drafts)}"
    assert all(d.chunk_type == "text" for d in drafts)
    assert all(
        d.metadata.get("chunk_strategy") == "fallback_flat_text" for d in drafts
    ), "Fallback drafts should have fallback_flat_text strategy"


def test_fallback_not_activated_when_sections_produce_drafts():
    """When structured sections produce drafts, fallback should NOT activate."""
    cfg = SimpleNamespace(
        strategy="auto",
        separators=[],
        child_chunk_size=256,
        child_chunk_overlap=32,
        parent_chunk_size=4096,
        token_limit=0,
        languages=[],
        enable_parent_child=True,
        enable_structured_parent_child=True,
        enable_table_row_split=False,
        enable_image_ocr_child=True,
    )
    doc = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(block_type="text", text="## 标题\n\n" + "正文内容。" * 100, order_index=0),
        ],
    )

    drafts = ChunkRebuildService().build_drafts(parsed_document=doc, file_type="md", cfg=cfg)

    assert len(drafts) > 0, f"Expected non-empty drafts, got {len(drafts)}"
    # When structured succeeds, drafts should include parent_text (not fallback)
    has_parent = any(d.chunk_type == "parent_text" for d in drafts)
    is_fallback = any(
        d.metadata.get("chunk_strategy") == "fallback_flat_text" for d in drafts
    )
    assert has_parent or not is_fallback, (
        "Structured rebuild should produce parent_text, not fallback"
    )
