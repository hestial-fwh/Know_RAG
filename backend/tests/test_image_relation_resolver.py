from types import SimpleNamespace

from app.services.chunk_service import _rebuild_canonical_weknora_parent_child
from app.services.chunker.canonical_parent_child import build_canonical_parent_child_plan
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.image_relation_resolver import (
    SpatialRelationCandidate,
    extract_figure_ids,
    resolve_image_relation,
)
from app.services.document_parser.types import ParsedBlock, ParsedDocument


def _cfg() -> ChunkerConfig:
    return ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
    )


def test_duplicate_pure_numeric_caption_is_suppressed():
    relation = resolve_image_relation(caption="526", ocr_text="526")

    assert relation.caption == ""
    assert relation.caption_suppressed is True
    assert relation.caption_method == "suppressed_duplicate_identifier"
    assert relation.binding_score == 0.05


def test_duplicate_component_identifier_caption_is_suppressed():
    relation = resolve_image_relation(caption="AB-123.4", ocr_text="AB-123.4")

    assert relation.caption == ""
    assert relation.caption_suppressed is True


def test_duplicate_component_magnification_caption_is_suppressed():
    relation = resolve_image_relation(caption="b 2 000×", ocr_text="b 2 000×")

    assert relation.caption == ""
    assert relation.caption_suppressed is True
    assert relation.binding_score == 0.05


def test_descriptive_caption_equal_to_ocr_is_preserved():
    relation = resolve_image_relation(caption="系统总体架构", ocr_text="系统总体架构")

    assert relation.caption == "系统总体架构"
    assert relation.caption_suppressed is False
    assert relation.binding_score == 0.95


def test_multiple_distinct_figure_ids_are_ambiguous():
    relation = resolve_image_relation(caption="图7、图8 不同工况结果", ocr_text="")

    assert relation.figure_ids == ["figure:7", "figure:8"]
    assert relation.ambiguous is True
    assert relation.binding_score == 0.25
    assert relation.deterministic_binding_allowed is False
    assert "multiple_figure_ids_in_single_caption" in relation.ambiguity_reasons


def test_repeated_same_figure_id_is_not_ambiguous():
    relation = resolve_image_relation(caption="图7：图7 局部放大")

    assert relation.figure_ids == ["figure:7"]
    assert relation.ambiguous is False
    assert relation.deterministic_binding_allowed is True


def test_english_and_subfigure_ids_are_normalized():
    assert extract_figure_ids("Figure 7.2 and Fig. 8(a)") == [
        "figure:7-2",
        "figure:8:a",
    ]


def test_grouped_supplementary_and_chinese_figure_ids_are_normalized():
    assert extract_figure_ids("图一、图2（a）及图S3") == [
        "figure:1",
        "figure:2:a",
        "figure:s3",
    ]
    assert extract_figure_ids("Figures S1 and S2") == ["figure:s1", "figure:s2"]


def test_relation_metadata_reaches_image_chunk():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="图7、图8 不同工况结果",
                order_index=0,
                page_no=1,
                metadata={
                    "parser": "mineru",
                    "caption": "图7、图8 不同工况结果",
                    "ocr_text": "曲线数据",
                },
            )
        ],
    )

    canonical = build_canonical_document(parsed, file_type="pdf")
    asset = next(iter(canonical.assets.values()))
    assert asset.ambiguous is True
    assert asset.figure_ids == ["figure:7", "figure:8"]

    plan = build_canonical_parent_child_plan(canonical, _cfg())
    image = next(child for child in plan.children if child.chunk_type == "image")
    assert image.metadata["ambiguous"] is True
    assert image.metadata["binding_score"] == 0.25
    assert image.metadata["figure_ids"] == ["figure:7", "figure:8"]
    assert image.metadata["deterministic_binding_allowed"] is False


def test_inherited_context_header_is_not_treated_as_parser_caption():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="PDF 图片",
                context_header="图7 系统结构",
                order_index=0,
                page_no=1,
                metadata={
                    "parser": "mineru",
                    "heading_path": ["图7 系统结构"],
                },
            )
        ],
    )

    canonical = build_canonical_document(parsed, file_type="pdf")
    asset = next(iter(canonical.assets.values()))
    assert asset.caption == ""
    assert asset.caption_method == "missing"
    assert asset.binding_score == 0.0
    assert asset.figure_ids == []
    assert asset.deterministic_binding_allowed is False
    assert asset.context_header == "图7 系统结构"

    plan = build_canonical_parent_child_plan(canonical, _cfg())
    image = next(child for child in plan.children if child.chunk_type == "image")
    assert image.metadata["caption"] == ""
    assert image.metadata["deterministic_binding_allowed"] is False
    assert "章节：图7 系统结构" in image.content


def test_suppressed_caption_does_not_return_through_alt_text_or_context_header():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="ab-526",
                context_header="ab-526",
                order_index=0,
                page_no=1,
                metadata={"parser": "mineru", "caption": "AB-526", "ocr_text": "ab-526"},
            )
        ],
    )

    canonical = build_canonical_document(parsed, file_type="pdf")
    asset = next(iter(canonical.assets.values()))
    assert asset.caption == ""
    assert asset.alt_text == "图片 1"
    assert asset.context_header == ""

    plan = build_canonical_parent_child_plan(canonical, _cfg())
    image = next(child for child in plan.children if child.chunk_type == "image")
    assert "图片标题：图片 1" in image.content
    assert "章节：ab-526" not in image.content
    assert image.metadata["caption_method"] == "suppressed_duplicate_identifier"


def _build_spatial_relation(
    caption_blocks: list[ParsedBlock],
    *,
    image_bbox=(0, 0, 400, 300),
    parser="mineru",
):
    image = ParsedBlock(
        block_type="image",
        text="PDF 图片",
        order_index=0,
        page_no=1,
        bbox=image_bbox,
        metadata={"parser": parser, "source": f"{parser}_content_list"},
    )
    parsed = ParsedDocument(content="", blocks=[image, *caption_blocks])
    canonical = build_canonical_document(parsed, file_type="pdf")
    return next(iter(canonical.assets.values())), canonical


def test_same_page_spatial_caption_is_bound_after_bbox_normalization():
    caption = ParsedBlock(
        block_type="text",
        text="图9 系统架构",
        order_index=1,
        page_no=1,
        bbox=(20, 310, 380, 340),
        metadata={"is_caption": True, "parser": "mineru"},
    )

    asset, canonical = _build_spatial_relation([caption])

    assert asset.caption == "图9 系统架构"
    assert asset.caption_method == "spatial_bbox"
    assert asset.spatial_status == "spatial_bound"
    assert asset.figure_ids == ["figure:9"]
    assert asset.deterministic_binding_allowed is True
    assert asset.normalized_bbox == (0.0, 0.0, 1.0, 300 / 340)
    relation_meta = asset.metadata["image_relation"]
    assert relation_meta["raw_bbox"] == [0.0, 0.0, 400.0, 300.0]
    assert relation_meta["normalization_page_extent"] == [0.0, 0.0, 400.0, 340.0]
    assert relation_meta["selected_candidate_orders"] == [1]

    plan = build_canonical_parent_child_plan(canonical, _cfg())
    image = next(child for child in plan.children if child.chunk_type == "image")
    assert image.metadata["spatial_status"] == "spatial_bound"
    assert image.metadata["selected_candidate_orders"] == [1]


def test_same_page_spatial_footnote_reaches_image_chunk():
    footnote = ParsedBlock(
        block_type="text",
        text="来源：内部设计文档",
        order_index=1,
        page_no=1,
        bbox=(20, 310, 380, 340),
        metadata={"role": "image_footnote", "parser": "mineru"},
    )

    asset, canonical = _build_spatial_relation([footnote])

    assert asset.caption == ""
    assert asset.footnote == "来源：内部设计文档"
    assert asset.spatial_status == "spatial_bound"
    plan = build_canonical_parent_child_plan(canonical, _cfg())
    image = next(child for child in plan.children if child.chunk_type == "image")
    assert image.metadata["footnote"] == "来源：内部设计文档"
    assert "图片脚注：来源：内部设计文档" in image.content


def test_spatial_numeric_caption_duplicated_by_ocr_is_suppressed():
    caption = ParsedBlock(
        block_type="text",
        text="526",
        order_index=1,
        page_no=1,
        bbox=(20, 310, 380, 340),
        metadata={"is_caption": True, "parser": "mineru"},
    )
    image = ParsedBlock(
        block_type="image",
        text="526",
        order_index=0,
        page_no=1,
        bbox=(0, 0, 400, 300),
        metadata={
            "parser": "mineru",
            "source": "mineru_content_list",
            "ocr_text": "526",
        },
    )

    canonical = build_canonical_document(
        ParsedDocument(content="", blocks=[image, caption]),
        file_type="pdf",
    )
    asset = next(iter(canonical.assets.values()))

    assert asset.caption == ""
    assert asset.caption_method == "suppressed_duplicate_identifier"
    assert asset.binding_score == 0.05
    assert asset.spatial_status == "spatial_candidate_suppressed"
    assert asset.deterministic_binding_allowed is False


def test_two_column_caption_is_rejected_by_horizontal_overlap():
    caption = ParsedBlock(
        block_type="text",
        text="图10 右栏图片",
        order_index=1,
        page_no=1,
        bbox=(500, 310, 900, 340),
        metadata={"is_caption": True, "parser": "mineru"},
    )

    asset, _ = _build_spatial_relation([caption])

    assert asset.caption == ""
    assert asset.spatial_status == "no_candidate"
    assert asset.deterministic_binding_allowed is False
    audits = asset.metadata["image_relation"]["spatial_candidates"]
    assert audits[0]["reason"] == "insufficient_horizontal_overlap"


def test_incomplete_candidate_bbox_does_not_create_high_score_binding():
    caption = ParsedBlock(
        block_type="text",
        text="图11 缺少坐标",
        order_index=1,
        page_no=1,
        bbox=None,
        metadata={"is_caption": True, "parser": "mineru"},
    )

    asset, _ = _build_spatial_relation([caption])

    assert asset.caption == ""
    assert asset.binding_score == 0.0
    assert asset.spatial_status == "missing_candidate_geometry"
    assert asset.deterministic_binding_allowed is False


def test_similarly_scored_spatial_candidates_are_ambiguous_and_unbound():
    captions = [
        ParsedBlock(
            block_type="text",
            text="图12 第一候选",
            order_index=1,
            page_no=1,
            bbox=(20, 310, 380, 340),
            metadata={"is_caption": True, "parser": "mineru"},
        ),
        ParsedBlock(
            block_type="text",
            text="图13 第二候选",
            order_index=2,
            page_no=1,
            bbox=(20, 345, 380, 375),
            metadata={"is_caption": True, "parser": "mineru"},
        ),
    ]

    asset, _ = _build_spatial_relation(captions)

    assert asset.caption == ""
    assert asset.ambiguous is True
    assert asset.spatial_status == "ambiguous_candidates"
    assert asset.deterministic_binding_allowed is False
    assert "multiple_spatial_caption_candidates" in (
        asset.metadata["image_relation"]["ambiguity_reasons"]
    )


def test_shared_spatial_caption_is_not_bound_to_multiple_images():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="左图",
                order_index=0,
                page_no=1,
                bbox=(0, 0, 400, 300),
                metadata={"parser": "mineru", "source": "mineru_content_list"},
            ),
            ParsedBlock(
                block_type="image",
                text="右图",
                order_index=1,
                page_no=1,
                bbox=(500, 0, 900, 300),
                metadata={"parser": "mineru", "source": "mineru_content_list"},
            ),
            ParsedBlock(
                block_type="text",
                text="图16 系统总览",
                order_index=2,
                page_no=1,
                bbox=(0, 310, 900, 340),
                metadata={"parser": "mineru", "is_caption": True},
            ),
        ],
    )

    assets = list(build_canonical_document(parsed, file_type="pdf").assets.values())

    assert len(assets) == 2
    assert all(asset.caption == "" for asset in assets)
    assert all(asset.caption_ambiguous is True for asset in assets)
    assert all(asset.deterministic_binding_allowed is False for asset in assets)
    assert all(
        "spatial_caption_candidate_shared_between_images"
        in asset.metadata["image_relation"]["ambiguity_reasons"]
        for asset in assets
    )


def test_suppressed_attached_caption_does_not_give_nearby_candidate_to_other_image():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="b 2 000×",
                order_index=0,
                page_no=1,
                bbox=(0, 0, 400, 300),
                metadata={
                    "parser": "mineru",
                    "source": "mineru_content_list",
                    "caption": "b 2 000×",
                    "ocr_text": "b 2 000×",
                },
            ),
            ParsedBlock(
                block_type="image",
                text="PDF 图片",
                order_index=1,
                page_no=1,
                bbox=(250, 0, 650, 300),
                metadata={"parser": "mineru", "source": "mineru_content_list"},
            ),
            ParsedBlock(
                block_type="text",
                text="图18 第一张图片的真实说明",
                order_index=2,
                page_no=1,
                bbox=(20, 310, 380, 340),
                metadata={"parser": "mineru", "is_caption": True},
            ),
        ],
    )

    first, second = list(build_canonical_document(parsed, file_type="pdf").assets.values())

    assert first.caption == "图18 第一张图片的真实说明"
    assert first.caption_method == "spatial_bbox"
    assert first.metadata["image_relation"]["caption_suppressed"] is False
    assert first.deterministic_binding_allowed is True
    assert second.caption == ""
    assert second.deterministic_binding_allowed is False


def test_ambiguous_footnotes_do_not_disable_reliable_caption_binding():
    candidates = [
        SpatialRelationCandidate(
            text="来源：甲",
            kind="footnote",
            page_no=1,
            bbox=(0.0, 0.51, 0.5, 0.55),
            source_order=1,
        ),
        SpatialRelationCandidate(
            text="来源：乙",
            kind="footnote",
            page_no=1,
            bbox=(0.0, 0.56, 0.5, 0.60),
            source_order=2,
        ),
    ]

    relation = resolve_image_relation(
        caption="图17 系统架构",
        page_no=1,
        image_bbox=(0.0, 0.0, 0.5, 0.5),
        image_order=0,
        spatial_candidates=candidates,
        enable_spatial=True,
    )

    assert relation.ambiguous is True
    assert relation.caption_ambiguous is False
    assert relation.footnote_ambiguous is True
    assert relation.footnote == ""
    assert relation.deterministic_binding_allowed is True


def test_spatial_binding_is_disabled_for_unreliable_parser():
    caption = ParsedBlock(
        block_type="text",
        text="图14 辅助链路候选",
        order_index=1,
        page_no=1,
        bbox=(20, 310, 380, 340),
        metadata={"is_caption": True, "parser": "ragflow"},
    )

    asset, _ = _build_spatial_relation([caption], parser="ragflow")

    assert asset.caption == ""
    assert asset.spatial_status == "disabled_unreliable_parser"
    assert asset.deterministic_binding_allowed is False


def test_no_spatial_candidate_stays_unbound():
    asset, _ = _build_spatial_relation([])

    assert asset.caption == ""
    assert asset.spatial_status == "no_candidate"
    assert asset.deterministic_binding_allowed is False


def test_spatial_relation_reaches_production_orm_chunk():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="PDF 图片",
                order_index=0,
                page_no=1,
                bbox=(0, 0, 400, 300),
                metadata={"parser": "mineru", "source": "mineru_content_list"},
            ),
            ParsedBlock(
                block_type="text",
                text="图15 生产链路空间绑定",
                order_index=1,
                page_no=1,
                bbox=(20, 310, 380, 340),
                metadata={"parser": "mineru", "is_caption": True},
            ),
        ],
    )
    document = SimpleNamespace(
        id="doc-spatial-relation",
        knowledge_base_id="kb-spatial-relation",
        title="空间绑定",
        file_name="spatial.pdf",
        file_type="pdf",
    )

    chunks, inserted = _rebuild_canonical_weknora_parent_child(
        document,
        parsed,
        "pdf",
        _cfg(),
    )

    image = next(chunk for chunk in chunks if chunk.chunk_type == "image")
    assert inserted == len(chunks)
    assert image.metadata_json["caption"] == "图15 生产链路空间绑定"
    assert image.metadata_json["caption_method"] == "spatial_bbox"
    assert image.metadata_json["spatial_status"] == "spatial_bound"
    assert image.metadata_json["deterministic_binding_allowed"] is True


def test_ambiguous_relation_reaches_production_orm_chunk():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="图7、图8 对比结果",
                order_index=0,
                page_no=1,
                metadata={
                    "parser": "mineru",
                    "caption": "图7、图8 对比结果",
                    "ocr_text": "工况曲线",
                },
            )
        ],
    )
    document = SimpleNamespace(
        id="doc-image-relation",
        knowledge_base_id="kb-image-relation",
        title="图文绑定",
        file_name="binding.pdf",
        file_type="pdf",
    )

    chunks, inserted = _rebuild_canonical_weknora_parent_child(
        document,
        parsed,
        "pdf",
        _cfg(),
    )

    image = next(chunk for chunk in chunks if chunk.chunk_type == "image")
    assert inserted == len(chunks)
    assert image.metadata_json["ambiguous"] is True
    assert image.metadata_json["figure_ids"] == ["figure:7", "figure:8"]
    assert image.metadata_json["deterministic_binding_allowed"] is False


def test_newline_separated_composite_figure_is_allowed():
    """图3\\n图4 (MinerU composite figure) should NOT be ambiguous."""
    relation = resolve_image_relation(
        caption="图3\n图4 组合结构截面图",
        ocr_text="",
    )

    assert relation.figure_ids == ["figure:3", "figure:4"]
    assert relation.composite_figure is True
    assert relation.ambiguous is False
    assert relation.caption_ambiguous is False
    assert relation.deterministic_binding_allowed is True
    assert "multiple_figure_ids_in_single_caption" not in relation.ambiguity_reasons


def test_newline_separated_composite_figure_reaches_production():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="图3\n图4 组合结构截面图",
                order_index=0,
                page_no=1,
                metadata={
                    "parser": "mineru",
                    "caption": "图3\n图4 组合结构截面图",
                    "ocr_text": "截面示意",
                },
            )
        ],
    )

    canonical = build_canonical_document(parsed, file_type="pdf")
    asset = next(iter(canonical.assets.values()))
    assert asset.composite_figure is True
    assert asset.ambiguous is False
    assert asset.figure_ids == ["figure:3", "figure:4"]
    assert asset.deterministic_binding_allowed is True

    plan = build_canonical_parent_child_plan(canonical, _cfg())
    image = next(child for child in plan.children if child.chunk_type == "image")
    assert image.metadata["composite_figure"] is True
    assert image.metadata["ambiguous"] is False
    assert image.metadata["figure_ids"] == ["figure:3", "figure:4"]
    assert image.metadata["deterministic_binding_allowed"] is True

    document = SimpleNamespace(
        id="doc-composite-figure",
        knowledge_base_id="kb-composite-figure",
        title="复合图号",
        file_name="composite.pdf",
        file_type="pdf",
    )
    chunks, inserted = _rebuild_canonical_weknora_parent_child(
        document,
        parsed,
        "pdf",
        _cfg(),
    )
    image_chunk = next(chunk for chunk in chunks if chunk.chunk_type == "image")
    assert inserted == len(chunks)
    assert image_chunk.metadata_json["composite_figure"] is True
    assert image_chunk.metadata_json["ambiguous"] is False
    assert image_chunk.metadata_json["figure_ids"] == ["figure:3", "figure:4"]
    assert image_chunk.metadata_json["deterministic_binding_allowed"] is True


def test_single_line_multi_figure_stays_ambiguous():
    """图7、图8 on a single line should remain ambiguous (existing behavior)."""
    relation = resolve_image_relation(
        caption="图7、图8 不同工况结果对比",
        ocr_text="",
    )

    assert relation.figure_ids == ["figure:7", "figure:8"]
    assert relation.composite_figure is False
    assert relation.ambiguous is True
    assert relation.caption_ambiguous is True
    assert relation.deterministic_binding_allowed is False
    assert "multiple_figure_ids_in_single_caption" in relation.ambiguity_reasons


def test_spatial_multi_figure_stays_ambiguous():
    """Multi-figure from spatial_bbox should remain ambiguous."""
    candidates = [
        SpatialRelationCandidate(
            text="图9、图10 结果对比",
            kind="caption",
            page_no=1,
            bbox=(0.05, 0.52, 0.95, 0.58),
            source_order=1,
        ),
    ]

    relation = resolve_image_relation(
        caption="",
        page_no=1,
        image_bbox=(0.0, 0.0, 1.0, 0.5),
        image_order=0,
        spatial_candidates=candidates,
        enable_spatial=True,
    )

    assert relation.figure_ids == ["figure:9", "figure:10"]
    assert relation.composite_figure is False
    assert relation.ambiguous is True
    assert relation.deterministic_binding_allowed is False
    assert "multiple_figure_ids_in_single_caption" in relation.ambiguity_reasons


def test_three_figure_composite_is_allowed():
    """图3\\n图4\\n图5 triple composite figure should be allowed."""
    relation = resolve_image_relation(
        caption="图3\n图4\n图5 多视图",
        ocr_text="",
    )

    assert relation.figure_ids == ["figure:3", "figure:4", "figure:5"]
    assert relation.composite_figure is True
    assert relation.ambiguous is False
    assert relation.deterministic_binding_allowed is True
