from types import SimpleNamespace

from app.services.chunking.child_builder import ChildBuilder
from app.services.chunking.chunk_draft import ParentDraft
from app.services.document_parser.types import ParsedBlock


def _cfg():
    return SimpleNamespace(
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


def test_child_builder_generates_core_child_types_without_db():
    long_text = "这是一个足够长的普通文本段落。" * 20
    ocr_text = "图片 OCR 文本内容，包含足够多的可检索信息。" * 20
    section = {
        "title": "章节",
        "heading_path": ["章节"],
        "strategy": "heading",
        "page_start": 1,
        "page_end": 1,
        "blocks": [
            ParsedBlock(block_type="text", text=long_text, order_index=1),
            ParsedBlock(block_type="table", text="列A 列B\n1 2\n3 4", table_html="<table><tr><td>1</td></tr></table>", order_index=2),
            ParsedBlock(block_type="image", text="", image_url="/files/a.png", metadata={"ocr_raw_text": ocr_text}, order_index=3),
            ParsedBlock(block_type="text", text="E = mc^2", metadata={"raw_block_type": "equation"}, order_index=4),
            ParsedBlock(block_type="text", text="```python\nprint('hi')\n```", metadata={"raw_block_type": "code_block"}, order_index=5),
        ],
    }
    parent = ParentDraft(content="parent", context_header="章节")

    children, _ = ChildBuilder(_cfg(), "md").build_children(section=section, parent=parent, start_offset=0)
    chunk_types = [child.chunk_type for child in children]

    assert "text" in chunk_types
    assert "table" in chunk_types
    assert "image" in chunk_types
    assert "image_ocr_text" in chunk_types
    assert "equation" in chunk_types
    assert any(child.metadata.get("content_kind") == "code_block" for child in children)
    assert all(child.parent_ref == parent.draft_ref for child in children)
