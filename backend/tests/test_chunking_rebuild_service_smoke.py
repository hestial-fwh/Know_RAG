from types import SimpleNamespace

from app.services.chunking.rebuild_service import ChunkRebuildService
from app.services.document_parser.types import ParsedBlock, ParsedDocument


def test_rebuild_service_builds_drafts_without_database():
    cfg = SimpleNamespace(
        strategy="auto",
        separators=[],
        child_chunk_size=256,
        child_chunk_overlap=32,
        parent_chunk_size=1024,
        token_limit=0,
        languages=[],
        enable_structured_parent_child=True,
        enable_table_row_split=False,
        enable_image_ocr_child=True,
    )
    doc = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(block_type="text", text="标题", metadata={"raw_block_type": "heading"}, order_index=0),
            ParsedBlock(block_type="text", text="这是正文内容。" * 80, order_index=1),
            ParsedBlock(block_type="table", text="A B\n1 2", order_index=2),
        ],
    )

    drafts = ChunkRebuildService().build_drafts(parsed_document=doc, file_type="md", cfg=cfg)

    assert any(draft.chunk_type == "parent_text" for draft in drafts)
    assert any(draft.chunk_type == "text" for draft in drafts)
    assert all(draft.chunk_index is not None for draft in drafts)
