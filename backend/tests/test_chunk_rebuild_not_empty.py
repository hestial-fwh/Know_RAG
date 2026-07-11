"""Test that ChunkRebuildService.build_drafts produces non-empty results
for a typical parsed document with mixed block types.
"""

from types import SimpleNamespace

from app.services.chunking.rebuild_service import ChunkRebuildService
from app.services.document_parser.types import ParsedBlock, ParsedDocument


def test_build_drafts_produces_text_and_table_children():
    """3 text blocks + 1 table block → should produce at least text + table children."""
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
            ParsedBlock(
                block_type="text",
                text="## 概述\n\n这是第一个正文段落，包含足够的文本内容来确保不会被过滤为低信息文本。"
                * 10,
                order_index=0,
            ),
            ParsedBlock(
                block_type="text",
                text="这是第二个正文段落，也包含足够的文本内容。" * 10,
                order_index=1,
            ),
            ParsedBlock(
                block_type="text",
                text="这是第三个正文段落。" * 10,
                order_index=2,
            ),
            ParsedBlock(
                block_type="table",
                text="列A 列B 列C\n值1 值2 值3\n值4 值5 值6",
                order_index=3,
            ),
        ],
    )

    drafts = ChunkRebuildService().build_drafts(parsed_document=doc, file_type="md", cfg=cfg)

    # Must produce SOME drafts
    assert len(drafts) > 0, f"Expected non-empty drafts, got {len(drafts)}"

    # Check draft types
    draft_types = [d.chunk_type for d in drafts]
    assert "text" in draft_types, f"Expected at least one text child, got types: {draft_types}"

    # Table should survive
    assert "table" in draft_types, f"Expected at least one table child, got types: {draft_types}"

    # All drafts should have chunk_index set
    assert all(d.chunk_index is not None for d in drafts), "All drafts must have chunk_index"


def test_build_drafts_with_only_table_blocks_still_produces_drafts():
    """Table-only sections should not be skipped just because parent content is short."""
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
            ParsedBlock(
                block_type="table",
                text="产品 价格 数量\n苹果 5元 100\n香蕉 3元 200",
                order_index=0,
            ),
        ],
    )

    drafts = ChunkRebuildService().build_drafts(parsed_document=doc, file_type="md", cfg=cfg)

    # Must produce at least a table child (even if parent is synthetic)
    assert len(drafts) > 0, f"Expected non-empty drafts for table-only document, got {len(drafts)}"


def test_build_drafts_with_only_image_blocks_still_produces_drafts():
    """Image-only sections should not be skipped just because parent content is short."""
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
            ParsedBlock(
                block_type="image",
                text="",
                image_url="/files/kb/abc/images/chart.png",
                metadata={
                    "raw_block_type": "image",
                    "caption": "Figure 1: 数据趋势图",
                    "ocr_raw_text": "2023年 100万\n2024年 150万",
                },
                order_index=0,
            ),
        ],
    )

    drafts = ChunkRebuildService().build_drafts(parsed_document=doc, file_type="pdf", cfg=cfg)

    # Must produce at least an image child
    assert len(drafts) > 0, f"Expected non-empty drafts for image-only document, got {len(drafts)}"
