from app.services.chunker.canonical_parent_child import (
    CanonicalChildDraft,
    build_canonical_parent_child_plan,
    merge_short_text_siblings,
)
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.types import ParsedDocument


def _child(
    content: str,
    *,
    seq: int,
    parent_index: int | None = 0,
    context_header: str = "## 同一章节",
    content_kind: str = "text",
    chunk_type: str = "text",
) -> CanonicalChildDraft:
    return CanonicalChildDraft(
        content=content,
        chunk_type=chunk_type,
        parent_index=parent_index,
        context_header=context_header,
        start=seq * 100,
        end=seq * 100 + len(content),
        source_child_seq=seq,
        metadata={"content_kind": content_kind},
        flags={"content_kind": content_kind},
    )


def _cfg(*, parent_size: int = 900, child_size: int = 220) -> ChunkerConfig:
    return ChunkerConfig(
        enable_parent_child=True,
        chunk_size=512,
        chunk_overlap=40,
        parent_chunk_size=parent_size,
        child_chunk_size=child_size,
        child_chunk_overlap=40,
        separators=["\n\n", "\n", "。"],
    )


def test_heading_led_short_child_merges_forward():
    short_head = _child("## March 2024 Vol. 46 No. 3\n\n恶病质是一切恶性肿瘤发展的最终结局。", seq=0)
    following = _child("后续正文" * 20, seq=1)

    merged = merge_short_text_siblings(
        [short_head, following],
        child_size=220,
        min_child_size=120,
    )

    assert len(merged) == 1
    assert merged[0].content.startswith("## March 2024 Vol. 46 No. 3")
    assert "后续正文" in merged[0].content
    assert merged[0].metadata["short_sibling_merged"] is True
    assert merged[0].metadata["merged_source_child_seqs"] == [0, 1]


def test_short_tail_merges_backward():
    previous = _child("主体正文" * 20, seq=0)
    short_tail = _child("综上所述，活性成分具有进一步研究价值。", seq=1)

    merged = merge_short_text_siblings(
        [previous, short_tail],
        child_size=220,
        min_child_size=120,
    )

    assert len(merged) == 1
    assert merged[0].content.startswith("主体正文")
    assert merged[0].content.endswith("综上所述，活性成分具有进一步研究价值。")


def test_short_text_does_not_cross_image_table_code_or_heading_context():
    short = _child("一句短结论。", seq=0)
    image = _child(
        "图片描述内容",
        seq=1,
        content_kind="image",
        chunk_type="image",
    )
    same_parent_after_image = _child("后续正文" * 10, seq=2)
    different_heading = _child(
        "另一章节正文" * 8,
        seq=3,
        context_header="## 另一章节",
    )

    merged = merge_short_text_siblings(
        [short, image, same_parent_after_image, different_heading],
        child_size=220,
        min_child_size=120,
    )

    assert len(merged) == 4
    assert merged[0].content == "一句短结论。"
    assert merged[1].chunk_type == "image"
    assert merged[3].context_header == "## 另一章节"


def test_short_child_is_kept_when_merge_would_exceed_child_size():
    short = _child("短句。", seq=0)
    full = _child("长正文" * 90, seq=1)

    merged = merge_short_text_siblings(
        [short, full],
        child_size=220,
        min_child_size=120,
    )

    assert len(merged) == 2
    assert merged[0].content == "短句。"


def test_parent_is_dropped_when_short_merge_collapses_two_children_to_one():
    # The text is intentionally split into two retrieval children under one
    # parent.  The first child is a heading-led short fragment and should merge
    # into the following child; parent validation must then downgrade the sole
    # surviving child to standalone.
    markdown = (
        "## March 2024 Vol. 46 No. 3\n\n"
        "恶病质是一切恶性肿瘤发展的最终结局。\n\n"
        + "后续正文内容。" * 18
    )
    canonical = build_canonical_document(
        ParsedDocument.from_text(markdown),
        file_type="md",
        document_title="期刊摘要",
    )
    plan = build_canonical_parent_child_plan(canonical, _cfg(parent_size=900, child_size=220))

    assert plan.children
    assert not any(
        len("".join(line for line in child.content.splitlines() if not line.lstrip().startswith("#"))) < 30
        for child in plan.children
    )
    # No parent is allowed to survive with one child after the merge layer.
    child_counts = {}
    for child in plan.children:
        if child.parent_index is not None:
            child_counts[child.parent_index] = child_counts.get(child.parent_index, 0) + 1
    assert all(count >= 2 for count in child_counts.values())
    assert all(parent.metadata["children_count_planned"] >= 2 for parent in plan.parents)
