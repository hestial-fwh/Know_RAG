from app.services.chunker.canonical_parent_child import (
    _immediate_tail_context,
    build_canonical_parent_child_plan,
    compose_image_child_content,
)
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import (
    CanonicalAsset,
    build_canonical_document,
)
from app.services.document_parser.mineru_adapter import _content_list_to_blocks
from app.services.document_parser.types import ParsedBlock, ParsedDocument


def _cfg() -> ChunkerConfig:
    return ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
        separators=["\n\n", "\n", "。"],
    )


def _mineru_image_blocks(*, footnote: object = "来源：设计文档", ocr: str = "架构组件"):
    return _content_list_to_blocks(
        [
            {
                "type": "image",
                "page_idx": 1,
                "bbox": [10, 20, 300, 240],
                "image_caption": ["图 1 系统架构"],
                "image_footnote": footnote,
                "text": ocr,
            }
        ],
        {},
        knowledge_base_id="kb-test",
        document_id="doc-test",
    )


def test_mineru_footnote_is_standardized_on_parsed_block():
    block = _mineru_image_blocks(footnote=["来源：设计文档", "注：仅供内部使用"])[0]

    assert block.page_no == 2
    assert block.bbox == (10.0, 20.0, 300.0, 240.0)
    assert block.metadata["footnote"] == "来源：设计文档\n注：仅供内部使用"
    assert block.metadata["image_footnote"] == "来源：设计文档\n注：仅供内部使用"


def test_mineru_footnote_reaches_canonical_asset_and_image_child():
    parsed = ParsedDocument(content="", blocks=_mineru_image_blocks())
    canonical = build_canonical_document(parsed, file_type="pdf", document_title="架构")
    asset = next(iter(canonical.assets.values()))

    assert asset.caption == "图 1 系统架构"
    assert asset.footnote == "来源：设计文档"

    plan = build_canonical_parent_child_plan(canonical, _cfg())
    image = next(child for child in plan.children if child.chunk_type == "image")
    assert "图片脚注：来源：设计文档" in image.content
    assert image.metadata["footnote"] == "来源：设计文档"


def test_footnote_is_not_repeated_when_ocr_already_contains_it():
    content = compose_image_child_content(
        CanonicalAsset(
            asset_id="image-1",
            caption="图 1",
            footnote="来源： 设计文档",
            ocr_text="架构组件\n来源：  设计文档",
        )
    )

    assert "图片脚注：" not in content
    assert "图片文字：" in content


def test_nearby_context_stops_at_page_boundary():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(block_type="text", text="第一页末尾，与图片无关", order_index=0, page_no=1),
            ParsedBlock(
                block_type="image",
                text="图 2",
                order_index=1,
                page_no=2,
                metadata={"caption": "图 2"},
            ),
            ParsedBlock(block_type="text", text="第二页图片说明", order_index=2, page_no=2),
        ],
    )

    asset = next(iter(build_canonical_document(parsed, file_type="pdf").assets.values()))
    assert asset.nearby_before == ""
    assert asset.nearby_after == "第二页图片说明"


def test_nearby_context_stops_at_heading_boundary():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(block_type="text", text="上一节正文", order_index=0, page_no=1),
            ParsedBlock(
                block_type="text",
                text="新章节",
                order_index=1,
                page_no=1,
                metadata={"is_heading": True, "heading_level": 2},
            ),
            ParsedBlock(
                block_type="image",
                text="图 3",
                order_index=2,
                page_no=1,
                metadata={"caption": "图 3"},
            ),
        ],
    )

    asset = next(iter(build_canonical_document(parsed, file_type="pdf").assets.values()))
    assert asset.nearby_before == ""


def test_nearby_context_respects_five_block_scan_limit():
    blocks = [ParsedBlock(block_type="text", text="过远正文", order_index=0, page_no=1)]
    blocks.extend(
        ParsedBlock(block_type="table", text=f"表格 {index}", order_index=index, page_no=1)
        for index in range(1, 7)
    )
    blocks.append(
        ParsedBlock(
            block_type="image",
            text="图 4",
            order_index=7,
            page_no=1,
            metadata={"caption": "图 4"},
        )
    )

    asset = next(iter(build_canonical_document(ParsedDocument(content="", blocks=blocks), file_type="pdf").assets.values()))
    assert asset.nearby_before == ""


def test_captioned_image_does_not_fallback_to_order_neighbor():
    content = compose_image_child_content(
        CanonicalAsset(
            asset_id="image-1",
            caption="图 1 系统架构",
            nearby_before="错误的上一节正文",
            nearby_after="错误的下一节正文",
        ),
        local_context="",
    )

    assert "错误的上一节正文" not in content
    assert "错误的下一节正文" not in content
    assert "上下文：" not in content


def test_mineru_image_keeps_page_bounded_preceding_context():
    content = compose_image_child_content(
        CanonicalAsset(
            asset_id="image-1",
            caption="图 1 系统架构",
            page_no=2,
            nearby_before="系统整体架构如下",
            nearby_after="不应使用的后文",
            metadata={"parser": "mineru", "source": "mineru_content_list"},
        ),
        local_context="图片后的直接说明",
    )

    assert "系统整体架构如下" in content
    assert "图片后的直接说明" in content
    assert "不应使用的后文" not in content


def test_immediate_tail_stops_at_asset_heading_and_paragraph_boundaries():
    assert _immediate_tail_context("xx图片后的正文![图2](asset://image-2)", 2, 8) == "图片后的正文"
    assert _immediate_tail_context("xx\n\n## 下一节\n正文", 2, 12) == ""
    assert _immediate_tail_context("xx第一段\n\n第二段", 2, 11) == "第一段"
