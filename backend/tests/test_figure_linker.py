from app.services.chunker.canonical_parent_child import build_canonical_parent_child_plan
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import (
    CanonicalAsset,
    CanonicalDocument,
    CanonicalSourceNode,
)
from app.services.document_parser.figure_linker import (
    extract_explicit_figure_references,
    link_figure_references,
)


def _asset(asset_id: str, figure_id: str, *, source_order: int) -> CanonicalAsset:
    return CanonicalAsset(
        asset_id=asset_id,
        caption=f"图{figure_id.removeprefix('figure:')} 说明",
        figure_ids=[figure_id],
        deterministic_binding_allowed=True,
        source_block_order=source_order,
    )


def _cfg() -> ChunkerConfig:
    return ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
    )


def test_extracts_required_chinese_and_english_figure_variants():
    markdown = (
        "如图1所示，系统完成初始化。\n\n"
        "See Fig. 2 for details.\n\n"
        "As shown in Figure 3.2, the value increases.\n\n"
        "图4(a)中给出了局部结构。"
    )

    references = extract_explicit_figure_references(markdown)

    assert [reference.figure_id for reference in references] == [
        "figure:1",
        "figure:2",
        "figure:3-2",
        "figure:4:a",
    ]


def test_does_not_confuse_figure_1_with_figure_10():
    references = extract_explicit_figure_references("如图1所示；另见图10。")

    assert [reference.figure_id for reference in references] == ["figure:1", "figure:10"]


def test_ignores_asset_markers_code_fences_and_bare_caption_labels():
    markdown = (
        "![图1 系统结构](asset://image-1)\n\n"
        "图1 系统结构\n\n"
        "请参见\n\n图1 另一条裸标题\n\n"
        "```text\nsee Figure 1\n```\n\n"
        "调用 `see Figure 1` 只是代码示例。\n\n"
        "正文没有显式图引用。"
    )

    assert extract_explicit_figure_references(markdown) == []


def test_unique_reliable_figure_asset_is_bound_exactly():
    markdown = "如图1所示，系统完成初始化。\n\n![图1 系统结构](asset://image-1)"
    result = link_figure_references(
        markdown,
        {"image-1": _asset("image-1", "figure:1", source_order=1)},
    )

    assert len(result.links) == 1
    assert result.links[0].status == "bound"
    assert result.links[0].asset_ids == ("image-1",)
    assert result.links[0].binding_method == "figure_id_exact"
    assert result.links[0].score_kind == "heuristic_rule_score"
    assert result.links[0].binding_score == result.links[0].evidence[0].rule_score
    assert result.links[0].evidence[0].figure_id_exact is True


def test_repeated_figure_id_is_resolved_only_by_unique_section():
    markdown = (
        "## 第一章\n\n如图1所示，第一套系统启动。\n\n"
        "![图1 第一套系统](asset://image-a)\n\n"
        "## 第二章\n\n如图1所示，第二套系统启动。\n\n"
        "![图1 第二套系统](asset://image-b)"
    )
    result = link_figure_references(
        markdown,
        {
            "image-a": _asset("image-a", "figure:1", source_order=1),
            "image-b": _asset("image-b", "figure:1", source_order=2),
        },
    )

    assert [link.status for link in result.links] == ["bound", "bound"]
    assert [link.asset_ids for link in result.links] == [("image-a",), ("image-b",)]
    assert all(link.binding_method == "figure_id_section_exact" for link in result.links)


def test_repeated_figure_id_in_same_section_remains_ambiguous():
    markdown = (
        "## 系统\n\n如图1所示，存在两个同号候选。\n\n"
        "![图1 候选甲](asset://image-a)\n\n"
        "![图1 候选乙](asset://image-b)"
    )
    result = link_figure_references(
        markdown,
        {
            "image-a": _asset("image-a", "figure:1", source_order=1),
            "image-b": _asset("image-b", "figure:1", source_order=2),
        },
    )

    link = result.links[0]
    assert link.status == "ambiguous"
    assert link.asset_ids == ()
    assert set(link.candidate_asset_ids) == {"image-a", "image-b"}
    assert link.ambiguity_reason == "repeated_figure_id_not_uniquely_resolved"


def test_unreliable_p1_asset_is_not_linked():
    markdown = "如图7所示。\n\n![图7、图8](asset://image-ambiguous)"
    asset = CanonicalAsset(
        asset_id="image-ambiguous",
        figure_ids=["figure:7", "figure:8"],
        deterministic_binding_allowed=False,
    )

    result = link_figure_references(markdown, {asset.asset_id: asset})

    assert result.links[0].status == "unmatched"
    assert result.links[0].asset_ids == ()


def test_production_plan_runs_linker_after_normalization_before_split():
    canonical = CanonicalDocument(
        markdown_content=(
            "## 系统\n\n如图1所示，系统完成初始化。\n\n"
            "![图1 系统结构](asset://image-1)"
        ),
        assets={"image-1": _asset("image-1", "figure:1", source_order=1)},
        metadata={"canonical_source": "ordered_parsed_blocks"},
        source_format="pdf",
    )

    plan = build_canonical_parent_child_plan(canonical, _cfg())

    assert plan.figure_links.coordinate_space == "normalized_canonical_markdown"
    assert len(plan.figure_links.links) == 1
    assert plan.figure_links.links[0].asset_ids == ("image-1",)
    assert plan.figure_links.links[0].reference.start == plan.markdown_content.index("图1所示") + 1


def test_extracts_grouped_supplementary_chinese_and_subfigure_syntax():
    markdown = (
        "如图一和图2（a）所示，结果一致。\n\n"
        "See Figs. S1 and S2 for supplementary results."
    )

    references = extract_explicit_figure_references(markdown)

    assert [reference.figure_id for reference in references] == [
        "figure:1",
        "figure:2:a",
        "figure:s1",
        "figure:s2",
    ]


def test_page_provenance_resolves_repeated_figure_id_and_is_auditable():
    markdown = "如图1所示，第二页结果更高。\n\n![图1甲](asset://a)\n\n![图1乙](asset://b)"
    reference_end = markdown.index("，")
    node = CanonicalSourceNode(
        node_id="node-reference",
        node_type="text",
        source_order=5,
        canonical_text=markdown[:reference_end],
        text_hash="hash",
        page_no=2,
        normalized_start=0,
        normalized_end=reference_end,
    )
    first = _asset("a", "figure:1", source_order=2)
    first.page_no = 1
    second = _asset("b", "figure:1", source_order=6)
    second.page_no = 2

    result = link_figure_references(
        markdown,
        {"a": first, "b": second},
        provenance_nodes=[node],
    )

    link = result.links[0]
    assert link.status == "bound"
    assert link.asset_ids == ("b",)
    assert link.binding_method == "figure_id_page_exact"
    assert link.reference.source_node_id == "node-reference"
    assert [item.page_match for item in link.evidence] == [True, False]


def test_explicit_reference_inside_caption_provenance_is_ignored():
    markdown = "如图1所示"
    node = CanonicalSourceNode(
        node_id="caption-node",
        node_type="text",
        source_order=1,
        canonical_text=markdown,
        text_hash="hash",
        semantic_role="caption",
        normalized_start=0,
        normalized_end=len(markdown),
    )

    assert extract_explicit_figure_references(markdown, provenance_nodes=[node]) == []
