from types import SimpleNamespace

from app.services.chunk_service import (
    _enforce_parent_child_integrity,
    _rebuild_canonical_weknora_parent_child,
)
from app.services.document_parser.block_reflow import reflow_blocks
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.chunker.canonical_parent_child import (
    build_canonical_parent_child_plan,
)
from app.services.chunker.models import ChunkerConfig


def _primary_visual(page_no: int, order: int) -> ParsedBlock:
    return ParsedBlock(
        block_type="image",
        text=f"PDF 第 {page_no} 页主视觉",
        page_no=page_no,
        order_index=order,
        bbox=(0, 0, 1000, 1000),
        metadata={
            "source": "pdf_page_visual_guard",
            "visual_role": "page_primary_visual",
        },
    )


def test_visual_pages_do_not_inherit_heading_from_previous_page():
    blocks = [
        ParsedBlock(
            block_type="text",
            text="第一件作品",
            page_no=1,
            order_index=0,
            bbox=(50, 40, 300, 80),
            metadata={"is_heading": True, "text_level": 1},
        ),
        _primary_visual(1, 1),
        ParsedBlock(
            block_type="text",
            text="这一页只有无法可靠作为标题的长段 OCR 内容。" * 12,
            page_no=2,
            order_index=2,
            bbox=(40, 250, 960, 800),
        ),
        _primary_visual(2, 3),
    ]

    output, stats = reflow_blocks(blocks)

    page_one = [block for block in output if block.page_no == 1]
    page_two = [block for block in output if block.page_no == 2]
    assert all(block.context_header == "第一件作品" for block in page_one)
    assert page_two[0].metadata["synthetic_page_heading"] is True
    assert all(block.context_header == "第 2 页内容" for block in page_two)
    assert stats["visual_page_section_count"] == 2
    assert stats["visual_page_synthetic_heading_count"] == 1


def test_visual_page_promotes_reliable_top_text_to_page_heading():
    blocks = [
        ParsedBlock(
            block_type="text",
            text="赵孟頫 书洛神赋卷 元\n纸本行书\n纵 29.2、横 193 厘米",
            page_no=3,
            order_index=0,
            bbox=(80, 60, 320, 125),
        ),
        ParsedBlock(
            block_type="text",
            text="作品说明正文，包含作者、年代和流传信息。",
            page_no=3,
            order_index=1,
            bbox=(80, 150, 900, 300),
        ),
        _primary_visual(3, 2),
    ]

    output, stats = reflow_blocks(blocks)

    expected = "赵孟頫 书洛神赋卷 元\n纸本行书\n纵 29.2、横 193 厘米"
    assert all(block.context_header == expected for block in output)
    assert output[0].metadata["heading_inference"] == "visual_page_top_text"
    assert stats["visual_page_inferred_heading_count"] == 1
    assert stats["visual_page_synthetic_heading_count"] == 0


def test_visual_page_demotes_mid_page_parser_heading_and_uses_top_title():
    blocks = [
        ParsedBlock(
            block_type="text",
            text="徐渭 草书作品卷 明",
            page_no=19,
            order_index=0,
            bbox=(80, 60, 360, 105),
        ),
        ParsedBlock(
            block_type="text",
            text="被 MinerU 错误识别成标题的书法 OCR 长行",
            page_no=19,
            order_index=1,
            bbox=(120, 260, 900, 430),
            metadata={"is_heading": True, "text_level": 1},
        ),
        _primary_visual(19, 2),
    ]

    output, stats = reflow_blocks(blocks)

    assert all(block.context_header == "徐渭 草书作品卷 明" for block in output)
    assert output[0].metadata["heading_inference"] == "visual_page_top_text"
    assert output[1].metadata["heading_demoted"] is True
    assert "text_level" not in output[1].metadata
    assert stats["visual_page_demoted_heading_count"] == 1


def test_normal_prose_keeps_cross_page_heading_inheritance():
    blocks = [
        ParsedBlock(
            block_type="text",
            text="第一章 系统设计",
            page_no=1,
            order_index=0,
            metadata={"is_heading": True, "text_level": 1},
        ),
        ParsedBlock(
            block_type="text",
            text="第一页正文内容。",
            page_no=1,
            order_index=1,
        ),
        ParsedBlock(
            block_type="text",
            text="第二页继续讨论同一章节。",
            page_no=2,
            order_index=2,
        ),
    ]

    output, stats = reflow_blocks(blocks)

    assert all(block.context_header == "第一章 系统设计" for block in output)
    assert stats["visual_page_section_count"] == 0
    assert stats["visual_page_synthetic_heading_count"] == 0


def test_single_page_primary_visual_keeps_page_parent_instead_of_becoming_orphan():
    blocks, _ = reflow_blocks(
        [
            _primary_visual(1, 0),
            _primary_visual(2, 1),
        ]
    )
    canonical = build_canonical_document(
        ParsedDocument(content="", blocks=blocks),
        file_type="pdf",
        document_title="视觉图册",
    )
    plan = build_canonical_parent_child_plan(
        canonical,
        ChunkerConfig(
            enable_parent_child=True,
            parent_chunk_size=4096,
            child_chunk_size=384,
            child_chunk_overlap=64,
        ),
    )

    image_children = [child for child in plan.children if child.chunk_type == "image"]
    assert len(image_children) == 2
    assert all(child.parent_index is not None for child in image_children)
    assert all(
        child.metadata["singleton_primary_visual_parent"] is True
        for child in image_children
    )
    assert len(plan.parents) == 2
    assert image_children[0].context_header == "第 1 页内容"
    assert image_children[1].context_header == "第 2 页内容"
    assert all(
        parent.metadata["synthetic_visual_page_parent"] is True
        for parent in plan.parents
    )


def test_final_integrity_keeps_single_page_primary_visual_parents():
    blocks, _ = reflow_blocks([_primary_visual(1, 0), _primary_visual(2, 1)])
    document = SimpleNamespace(
        id="visual-parent-integrity",
        knowledge_base_id="kb-visual",
        title="Visual parent integrity",
        file_type="pdf",
    )
    config = ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
    )

    chunks, _ = _rebuild_canonical_weknora_parent_child(
        document,
        ParsedDocument(content="", blocks=blocks),
        "pdf",
        config,
    )
    persisted = _enforce_parent_child_integrity(chunks)

    parents = [chunk for chunk in persisted if chunk.chunk_type == "parent_text"]
    images = [chunk for chunk in persisted if chunk.chunk_type == "image"]
    assert len(parents) == 2
    assert len(images) == 2
    assert all(image.parent_chunk_id for image in images)
    assert {image.parent_chunk_id for image in images} == {
        parent.id for parent in parents
    }


def test_visual_page_preparation_preserves_page_less_block_order():
    page_less_title = ParsedBlock(
        block_type="text",
        text="Document title",
        page_no=None,
        order_index=0,
        metadata={"is_heading": True, "text_level": 1},
    )

    output, _ = reflow_blocks([page_less_title, _primary_visual(1, 1)])

    assert output[0] is page_less_title
    assert output[1].metadata["synthetic_page_heading"] is True
    assert output[2].block_type == "image"
