from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

pytest.importorskip("pgvector")

from app.services.chunk_service import _rebuild_document_chunks_structured_parent_child
from app.services.document_parser.ragflow_format_adapter import parse_markdown
from app.services.parser_service import resolve_parser_engine


def _cfg():
    return SimpleNamespace(
        parent_chunk_size=4096,
        child_chunk_size=256,
        child_chunk_overlap=32,
        strategy="auto",
        separators=[],
        token_limit=0,
        languages=[],
        enable_table_row_split=False,
        enable_image_ocr_child=True,
    )


def test_markdown_routes_to_native_parser():
    assert resolve_parser_engine("md") == "native_markdown"
    assert resolve_parser_engine(".markdown") == "native_markdown"


def test_markdown_tables_survive_parse_and_structured_chunking_with_correct_context():
    source = (
        "# 第一章\n\n"
        + "第一段正文足够长，包含实际说明内容，避免被低信息规则过滤。" * 6
        + "\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
        + "## 第二章\n\n"
        + "第二段正文足够长，包含实际说明内容，避免被低信息规则过滤。" * 6
        + "\n\n| C | D |\n| --- | --- |\n| 3 | 4 |"
    )

    with TemporaryDirectory() as directory:
        path = Path(directory) / "case.md"
        path.write_text(source, encoding="utf-8")
        parsed = parse_markdown(path)

    document = SimpleNamespace(
        id="doc-markdown-table",
        knowledge_base_id="kb-markdown-table",
        title="Markdown 表格测试",
        file_name="case.md",
    )
    chunks, inserted = _rebuild_document_chunks_structured_parent_child(
        None,
        document,
        parsed,
        "md",
        _cfg(),
    )

    tables = [chunk for chunk in chunks if chunk.chunk_type == "table"]
    parents = [chunk for chunk in chunks if chunk.chunk_type == "parent_text"]

    assert inserted == len(chunks)
    assert len(parents) == 2
    assert len(tables) == 2
    assert "第一章" in tables[0].context_header
    assert "第二章" in tables[1].context_header
    assert "| A | B |" in tables[0].content
    assert "| C | D |" in tables[1].content
    assert tables[0].metadata_json.get("start_line") == 4
    assert tables[1].metadata_json.get("start_line") == 12
