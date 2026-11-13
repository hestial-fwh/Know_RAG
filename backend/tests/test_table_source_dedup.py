"""整表 overlap 副本与独立/分片表格的边界回归。"""

from dataclasses import replace

from app.services.chunker.canonical_parent_child import (
    CanonicalChildDraft, build_canonical_parent_child_plan, deduplicate_repeated_table_children,
)
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.types import ParsedDocument

TABLE = "| 项目 | 内容 |\n| --- | --- |\n| 专业编号 | P05 |\n| 专业代码 | 510209 |"


def table(seq=1, start=0, end=None, content=TABLE, parent=0):
    return CanonicalChildDraft(content, "table", parent, "测试", start,
                               len(content) if end is None else end, seq)


def test_same_table_from_overlapping_windows_is_materialized_once():
    first, second = table(), table(seq=2, parent=1)
    result = deduplicate_repeated_table_children([first, second], TABLE)
    assert result == [first]
    assert result[0].metadata["table_overlap_source_child_seqs"] == [1, 2]
    assert result[0].metadata["table_overlap_source_parent_indexes"] == [0, 1]


def test_identical_tables_at_independent_locations_are_kept():
    markdown = TABLE + "\n\n" + TABLE
    second_start = len(TABLE) + 2
    children = [table(), table(seq=2, start=second_start, end=len(markdown))]
    assert len(deduplicate_repeated_table_children(children, markdown)) == 2


def test_same_header_with_different_rows_is_kept():
    second = TABLE.replace("P05", "P06")
    markdown = TABLE + "\n\n" + second
    children = [table(), table(seq=2, content=second, start=len(TABLE) + 2, end=len(markdown))]
    assert len(deduplicate_repeated_table_children(children, markdown)) == 2


def test_unverified_source_range_and_other_chunk_types_are_not_deleted():
    children = [table(end=len(TABLE) + 10), table(seq=2, end=len(TABLE) + 10)]
    assert len(deduplicate_repeated_table_children(children, TABLE)) == 2
    text = replace(table(), chunk_type="text")
    assert len(deduplicate_repeated_table_children([text, replace(text, source_child_seq=2)], TABLE)) == 2


def test_real_parent_child_plan_has_no_repeated_table_spans():
    content = ("# 专业设置\n\n" + "专业建设说明。" * 40 + "\n\n" + TABLE +
               "\n\n" + "课程与实验室说明。" * 40)
    canonical = build_canonical_document(ParsedDocument.from_text(content), file_type="md")
    plan = build_canonical_parent_child_plan(canonical, ChunkerConfig(
        enable_parent_child=True, parent_chunk_size=1200, child_chunk_size=160, child_chunk_overlap=100,
    ))
    tables = [child for child in plan.children if child.chunk_type == "table"]
    assert tables
    assert len({(child.start, child.end, child.content) for child in tables}) == len(tables)
