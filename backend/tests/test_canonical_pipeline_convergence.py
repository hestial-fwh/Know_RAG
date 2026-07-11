from app.services.chunker.canonical_parent_child import (
    CanonicalChildDraft,
    build_canonical_parent_child_plan,
    merge_short_text_siblings,
)
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.types import ParsedBlock, ParsedDocument


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


def _child(content: str, *, seq: int, parent_index=None, header="## 同一章节") -> CanonicalChildDraft:
    return CanonicalChildDraft(
        content=content,
        chunk_type="text",
        parent_index=parent_index,
        context_header=header,
        start=seq * 100,
        end=seq * 100 + len(content),
        source_child_seq=seq,
        metadata={"content_kind": "text"},
        flags={"content_kind": "text"},
    )


def test_pdf_page_number_blocks_are_removed_before_canonical_markdown():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="沉香叶提取物的结构化学成分及活性",
                order_index=0,
                metadata={"is_heading": True, "heading_level": 2},
            ),
            ParsedBlock(block_type="text", text="526", order_index=1),
            ParsedBlock(block_type="text", text="524", order_index=2),
            ParsedBlock(block_type="text", text="117", order_index=3),
            ParsedBlock(block_type="text", text="研究结果表明，该提取物具有显著活性。" * 8, order_index=4),
        ],
    )

    canonical = build_canonical_document(parsed, file_type="pdf", document_title="论文")

    assert "526" not in canonical.markdown_content
    assert "524" not in canonical.markdown_content
    assert "117" not in canonical.markdown_content
    assert canonical.metadata["canonical_noise_blocks_dropped"] == 3

    plan = build_canonical_parent_child_plan(canonical, _cfg())
    assert plan.children
    assert not any("526\n\n524\n\n117" in child.content for child in plan.children)
    assert not any(child.content.strip() in {"526", "524", "117"} for child in plan.children)


def test_explicit_parser_header_footer_and_page_number_roles_are_removed():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(block_type="text", text="Journal Header", order_index=0, metadata={"raw_block_type": "header"}),
            ParsedBlock(block_type="text", text="正文内容。" * 20, order_index=1),
            ParsedBlock(block_type="text", text="Page 3 / 10", order_index=2, metadata={"role": "footer"}),
        ],
    )
    canonical = build_canonical_document(parsed, file_type="pdf")
    assert "Journal Header" not in canonical.markdown_content
    assert "Page 3 / 10" not in canonical.markdown_content
    assert "正文内容" in canonical.markdown_content
    assert canonical.metadata["canonical_noise_blocks_dropped"] == 2


def test_single_numeric_parser_block_is_not_dropped_without_noise_context():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(block_type="text", text="实验编号", order_index=0, metadata={"is_heading": True, "heading_level": 2}),
            ParsedBlock(block_type="text", text="2024", order_index=1),
            ParsedBlock(block_type="text", text="该数字是正文中的有效实验年份。", order_index=2),
        ],
    )
    canonical = build_canonical_document(parsed, file_type="pdf")
    assert "2024" in canonical.markdown_content
    assert canonical.metadata["canonical_noise_blocks_dropped"] == 0


def test_repeated_page_noise_inside_one_parser_text_block_is_removed_but_code_numbers_are_kept():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text=(
                    "章节标题\n\n526\n\n524\n\n117\n\n"
                    "```text\n526\n524\n117\n```\n\n"
                    "正文内容继续。"
                ),
                order_index=0,
                metadata={"is_heading": True, "heading_level": 2},
            )
        ],
    )
    canonical = build_canonical_document(parsed, file_type="pdf")
    assert canonical.metadata["canonical_noise_lines_dropped"] == 3
    assert "```text\n526\n524\n117\n```" in canonical.markdown_content
    assert canonical.markdown_content.count("526") == 1
    assert canonical.markdown_content.count("524") == 1
    assert canonical.markdown_content.count("117") == 1


def test_native_markdown_numeric_data_is_authoritative_and_not_filtered():
    markdown = "# 测量结果\n\n526\n\n524\n\n117"
    canonical = build_canonical_document(ParsedDocument.from_text(markdown), file_type="md")
    assert canonical.markdown_content == markdown


def test_standalone_short_text_children_can_merge_when_context_matches():
    short_head = _child("## March 2024 Vol. 46 No. 3\n\n恶病质是一切恶性肿瘤发展的最终结局。", seq=0)
    following = _child("后续正文" * 20, seq=1)

    merged = merge_short_text_siblings(
        [short_head, following],
        child_size=220,
        min_child_size=120,
    )

    assert len(merged) == 1
    assert merged[0].parent_index is None
    assert "恶病质" in merged[0].content
    assert "后续正文" in merged[0].content


def test_standalone_short_text_does_not_cross_context_or_media_boundary():
    short = _child("一句短结论。", seq=0, header="## 第一章")
    image = CanonicalChildDraft(
        content="图片描述",
        chunk_type="image",
        parent_index=None,
        context_header="## 第一章",
        start=100,
        end=120,
        source_child_seq=1,
        metadata={"content_kind": "image"},
        flags={"content_kind": "image"},
    )
    after = _child("后续正文" * 10, seq=2, header="## 第一章")
    other = _child("另一章节正文" * 10, seq=3, header="## 第二章")

    merged = merge_short_text_siblings([short, image, after, other], child_size=220, min_child_size=120)
    assert len(merged) == 4
    assert merged[0].content == "一句短结论。"
    assert merged[1].chunk_type == "image"
    assert merged[-1].context_header == "## 第二章"


def test_no_zero_or_one_child_parent_survives_after_converged_pipeline():
    markdown = (
        "## 第一节\n\n一句短结论。\n\n" + "第一节正文。" * 25 +
        "\n\n## 第二节\n\n另一句短结论。\n\n" + "第二节正文。" * 25
    )
    canonical = build_canonical_document(ParsedDocument.from_text(markdown), file_type="md")
    plan = build_canonical_parent_child_plan(canonical, _cfg(parent_size=500, child_size=180))

    counts = {}
    for child in plan.children:
        if child.parent_index is not None:
            counts[child.parent_index] = counts.get(child.parent_index, 0) + 1
    assert all(count >= 2 for count in counts.values())
    assert all(parent.metadata["children_count_planned"] >= 2 for parent in plan.parents)


def test_heading_context_uses_heading_active_at_child_start_not_last_overlap_heading():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(block_type="text", text="第一篇", order_index=0, metadata={"is_heading": True, "heading_level": 2}),
            ParsedBlock(block_type="text", text="第一篇正文。" * 45, order_index=1),
            ParsedBlock(block_type="text", text="第二篇", order_index=2, metadata={"is_heading": True, "heading_level": 2}),
            ParsedBlock(block_type="text", text="第二篇正文。" * 45, order_index=3),
        ],
    )
    canonical = build_canonical_document(parsed, file_type="pdf")
    plan = build_canonical_parent_child_plan(canonical, _cfg(parent_size=1200, child_size=220))

    first = next(child for child in plan.children if child.content.lstrip().startswith("## 第一篇"))
    final_second = plan.children[-1]
    assert first.context_header == "## 第一篇"
    assert final_second.context_header == "## 第二篇"
    # A chunk that overlaps into the next heading must not retroactively relabel
    # earlier prose with the last heading contained in that chunk.
    crossing = next(child for child in plan.children if "## 第二篇" in child.content and not child.content.lstrip().startswith("## 第二篇"))
    assert crossing.context_header == "## 第一篇"


def test_parent_flatten_preserves_standalone_child_boundaries(monkeypatch):
    from types import SimpleNamespace
    import app.services.chunker.canonical_parent_child as module

    fake_result = SimpleNamespace(
        parents=[
            SimpleNamespace(
                content="父上下文",
                context_header="## 同一章节",
                start=0,
                end=120,
                metadata={},
            )
        ],
        children=[
            SimpleNamespace(
                content="一句很短的结论。",
                seq=0,
                start=0,
                end=20,
                context_header="## 同一章节",
                parent_index=0,
                metadata={},
            ),
            SimpleNamespace(
                content="后续正文" * 20,
                seq=1,
                start=20,
                end=180,
                context_header="## 同一章节",
                parent_index=None,
                metadata={},
            ),
        ],
    )
    monkeypatch.setattr(module, "split_parent_child_result", lambda *_args, **_kwargs: fake_result)

    canonical = build_canonical_document(ParsedDocument.from_text("占位正文"), file_type="md")
    plan = build_canonical_parent_child_plan(canonical, _cfg(parent_size=900, child_size=220))

    assert len(plan.parents) == 0
    assert len(plan.children) == 2
    assert plan.children[0].content == "一句很短的结论。"
    assert plan.children[1].content == "后续正文" * 20
    assert all(child.parent_index is None for child in plan.children)
    assert not any(child.metadata.get("short_sibling_merged") for child in plan.children)


def test_child_noise_guard_drops_parser_derived_heading_plus_numeric_noise_fragment():
    from app.services.document_parser.canonical_document import CanonicalDocument

    canonical = CanonicalDocument(
        markdown_content="## 沉香叶提取物的结构化学成分及活性\n\n526\n\n524\n\n117",
        metadata={"canonical_source": "ordered_parsed_blocks", "canonical_markdown": True},
        source_format="pdf",
    )
    plan = build_canonical_parent_child_plan(canonical, _cfg(parent_size=900, child_size=220))
    assert not plan.children
    assert not plan.parents
