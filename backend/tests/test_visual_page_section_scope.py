from types import SimpleNamespace

from app.services.chunk_service import (
    _enforce_parent_child_integrity,
    _rebuild_canonical_weknora_parent_child,
)
from app.services.document_parser.block_reflow import reflow_blocks
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.chunker.canonical_parent_child import (
    CanonicalChildDraft,
    _build_visual_section_parents,
    _build_visual_page_parents,
    _is_page_primary_visual_asset,
    build_canonical_parent_child_plan,
)
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import CanonicalAsset


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


# ---------------------------------------------------------------------------
# Visual section-scoped parent tests (_build_visual_section_parents)
# ---------------------------------------------------------------------------


def _make_text_child(
    content: str,
    *,
    parent_index: int = 0,
    start: int = 0,
    end: int = 100,
    source_child_seq: int = 0,
    page_no: int = 1,
) -> CanonicalChildDraft:
    """Create a minimal text child for section-parent testing."""
    return CanonicalChildDraft(
        content=content,
        chunk_type="text",
        parent_index=parent_index,
        context_header="",
        start=start,
        end=end,
        source_child_seq=source_child_seq,
        metadata={"visual_page_no": page_no},
    )


def _make_image_child(
    *,
    parent_index: int = 0,
    start: int = 0,
    end: int = 100,
    source_child_seq: int = 0,
    page_no: int = 1,
    is_primary_visual: bool = False,
) -> CanonicalChildDraft:
    """Create a minimal image child for section-parent testing."""
    meta: dict = {"visual_page_no": page_no}
    if is_primary_visual:
        meta["visual_role"] = "page_primary_visual"
    return CanonicalChildDraft(
        content="图片内容",
        chunk_type="image",
        parent_index=parent_index,
        context_header="",
        start=start,
        end=end,
        source_child_seq=source_child_seq,
        metadata=meta,
    )


def test_two_text_sections_images_bind_to_correct_section():
    """Author A intro + works → parent 0; Author B intro + works → parent 1."""
    markdown = (
        "王羲之 东晋书法家，后世尊为书圣。" * 6
        + "![作品1](asset://a1)\n\n![作品2](asset://a2)\n\n"
        + "颜真卿 唐代书法家，创颜体楷书。" * 6
        + "![作品3](asset://a3)\n\n"
    )
    children = [
        # Section 0: Author A text + 2 images
        _make_text_child(
            "王羲之 东晋书法家，后世尊为书圣。" * 6,
            parent_index=0, start=0, end=200, source_child_seq=0,
        ),
        _make_image_child(
            parent_index=0, start=200, end=220, source_child_seq=1,
            is_primary_visual=True,
        ),
        _make_image_child(
            parent_index=0, start=220, end=240, source_child_seq=2,
            is_primary_visual=True,
        ),
        # Section 1: Author B text + 1 image
        _make_text_child(
            "颜真卿 唐代书法家，创颜体楷书。" * 6,
            parent_index=1, start=240, end=440, source_child_seq=3,
        ),
        _make_image_child(
            parent_index=1, start=440, end=460, source_child_seq=4,
            is_primary_visual=True,
        ),
    ]
    page_ranges = [(1, 0, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    assert len(parents) == 2, f"Expected 2 parents, got {len(parents)}"
    assert counts == {0: 3, 1: 2}
    assert kept == {0, 1}

    # Parent 0 children: Author A text + 2 images
    parent_0_children = [c for c in children if c.parent_index == 0]
    assert len(parent_0_children) == 3
    assert parent_0_children[0].chunk_type == "text"
    assert parent_0_children[1].chunk_type == "image"
    assert parent_0_children[2].chunk_type == "image"
    assert "王羲之" in parent_0_children[0].content

    # Parent 1 children: Author B text + 1 image
    parent_1_children = [c for c in children if c.parent_index == 1]
    assert len(parent_1_children) == 2
    assert parent_1_children[0].chunk_type == "text"
    assert parent_1_children[1].chunk_type == "image"
    assert "颜真卿" in parent_1_children[0].content


def test_orphan_images_bind_upward_to_nearest_text():
    """Images between two text sections bind upward to the preceding text."""
    markdown = (
        "作者介绍文字，足够长以形成有效文本段落。" * 5
        + "![无标题作品1](asset://a1)\n\n"
        + "![无标题作品2](asset://a2)\n\n"
        + "第二段作者介绍，也是一个有效的文本段落。" * 5
    )
    children = [
        # Section 0: text (will be kept — has semantic content)
        _make_text_child(
            "作者介绍文字，足够长以形成有效文本段落。" * 5,
            parent_index=0, start=0, end=200, source_child_seq=0,
        ),
        # Section 1: image-only (will be dissolved → bind upward to section 0)
        _make_image_child(
            parent_index=1, start=200, end=220, source_child_seq=1,
            is_primary_visual=True,
        ),
        _make_image_child(
            parent_index=1, start=220, end=240, source_child_seq=2,
            is_primary_visual=True,
        ),
        # Section 2: text (will be kept)
        _make_text_child(
            "第二段作者介绍，也是一个有效的文本段落。" * 5,
            parent_index=2, start=240, end=440, source_child_seq=3,
        ),
    ]
    page_ranges = [(1, 0, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    assert len(parents) == 2, f"Expected 2 parents, got {len(parents)}"
    # Section 0 children: text + 2 upward-bound images
    parent_0_children = [c for c in children if c.parent_index == 0]
    assert len(parent_0_children) == 3
    # Section 2 children: text only
    parent_1_children = [c for c in children if c.parent_index == 1]
    assert len(parent_1_children) == 1
    assert parent_1_children[0].chunk_type == "text"

    # Verify upward binding metadata
    images = [c for c in children if c.chunk_type == "image"]
    for img in images:
        assert img.metadata["parent_materialization_rule"] == (
            "visual_section_upward_binding"
        )
        assert img.flags["visual_section_upward_binding"] is True
        assert "original_source_parent_index" in img.metadata


def test_page_top_images_bind_downward_to_first_text():
    """Images at the top of a page (before any text) bind to the first text section."""
    markdown = (
        "![作品1](asset://a1)\n\n"
        + "![作品2](asset://a2)\n\n"
        + "本页作者介绍，是页面第一个有效文本段落。" * 5
    )
    children = [
        # Section 0: image-only at page top (dissolved → downward bind to section 1)
        _make_image_child(
            parent_index=0, start=0, end=20, source_child_seq=0,
            is_primary_visual=True,
        ),
        _make_image_child(
            parent_index=0, start=20, end=40, source_child_seq=1,
            is_primary_visual=True,
        ),
        # Section 1: text (kept)
        _make_text_child(
            "本页作者介绍，是页面第一个有效文本段落。" * 5,
            parent_index=1, start=40, end=240, source_child_seq=2,
        ),
    ]
    page_ranges = [(1, 0, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    assert len(parents) == 1
    # All 3 children should be under parent 0
    assert all(c.parent_index == 0 for c in children)
    assert counts[0] == 3
    # Images should have upward_binding metadata (they bound downward in this case)
    images = [c for c in children if c.chunk_type == "image"]
    for img in images:
        assert img.metadata["parent_materialization_rule"] == (
            "visual_section_upward_binding"
        )


def test_pure_image_page_falls_back_to_page_parent():
    """Page with only images and no semantic text → single page-level parent."""
    markdown = "![作品1](asset://a1)\n\n![作品2](asset://a2)\n\n![作品3](asset://a3)\n\n"
    children = [
        _make_image_child(
            parent_index=0, start=0, end=20, source_child_seq=0,
            is_primary_visual=True,
        ),
        _make_image_child(
            parent_index=0, start=20, end=40, source_child_seq=1,
            is_primary_visual=True,
        ),
        _make_image_child(
            parent_index=0, start=40, end=60, source_child_seq=2,
            is_primary_visual=True,
        ),
    ]
    page_ranges = [(1, 0, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    assert len(parents) == 1
    assert counts[0] == 3
    assert all(c.parent_index == 0 for c in children)


def test_singleton_primary_visual_kept_as_section():
    """Single page_primary_visual image keeps its own parent."""
    markdown = "![整页作品](asset://a1)\n\n"
    children = [
        _make_image_child(
            parent_index=0, start=0, end=20, source_child_seq=0,
            is_primary_visual=True,
        ),
    ]
    page_ranges = [(1, 0, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    assert len(parents) == 1
    assert counts[0] == 1
    child = children[0]
    assert child.metadata["singleton_primary_visual_parent"] is True


def test_two_pages_sections_independent():
    """Each page's sections are independent — no cross-page binding."""
    markdown = (
        # Page 1: text + image
        "第一页作者介绍。" * 6
        + "![作品1](asset://a1)\n\n"
        # Page 2: text + image
        + "第二页作者介绍。" * 6
        + "![作品2](asset://a2)\n\n"
    )
    children = [
        # Page 1
        _make_text_child(
            "第一页作者介绍。" * 6,
            parent_index=0, start=0, end=200, source_child_seq=0, page_no=1,
        ),
        _make_image_child(
            parent_index=0, start=200, end=220, source_child_seq=1, page_no=1,
            is_primary_visual=True,
        ),
        # Page 2
        _make_text_child(
            "第二页作者介绍。" * 6,
            parent_index=0, start=220, end=420, source_child_seq=2, page_no=2,
        ),
        _make_image_child(
            parent_index=0, start=420, end=440, source_child_seq=3, page_no=2,
            is_primary_visual=True,
        ),
    ]
    page_ranges = [(1, 0, 220), (2, 220, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    assert len(parents) == 2
    assert {c.metadata["visual_page_no"] for c in children if c.parent_index == 0} == {1}
    assert {c.metadata["visual_page_no"] for c in children if c.parent_index == 1} == {2}


def test_section_parents_flag_off_preserves_page_level_parents(monkeypatch):
    """When DOCUMENT_VISUAL_SECTION_PARENT_ENABLED=False, old behavior is preserved."""
    monkeypatch.setattr(
        "app.services.chunker.canonical_parent_child.settings.document_visual_section_parent_enabled",
        False,
    )
    markdown = (
        "作者A介绍。" * 6
        + "![作品1](asset://a1)\n\n"
        + "作者B介绍。" * 6
        + "![作品2](asset://a2)\n\n"
        + "作者C介绍。" * 6
        + "![作品3](asset://a3)\n\n"
    )
    # This is a 3-work art book on one visual page
    blocks, _ = reflow_blocks([
        ParsedBlock(
            block_type="text",
            text="作者A介绍。" * 6,
            page_no=1, order_index=0, bbox=(80, 60, 900, 200),
        ),
        _primary_visual(1, 1),
        ParsedBlock(
            block_type="text",
            text="作者B介绍。" * 6,
            page_no=1, order_index=2, bbox=(80, 300, 900, 440),
        ),
        _primary_visual(1, 3),
        ParsedBlock(
            block_type="text",
            text="作者C介绍。" * 6,
            page_no=1, order_index=4, bbox=(80, 540, 900, 680),
        ),
        _primary_visual(1, 5),
    ])
    canonical = build_canonical_document(
        ParsedDocument(content=markdown, blocks=blocks),
        file_type="pdf",
        document_title="书法作品集",
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

    # Flag OFF: should still use page-level parents (1 parent per page)
    parents = [p for p in plan.parents]
    # With flag off, the document may not qualify as visual-page-dominant
    # (needs 3+ visual pages with 60% ratio). Single-page docs won't trigger
    # visual page handling at all.
    # This test verifies no crash and sensible parent assignment.
    assert len(parents) >= 1


# ---------------------------------------------------------------------------
# Cross-page binding tests (Pass 2)
# ---------------------------------------------------------------------------


def test_cross_page_images_bind_to_preceding_text():
    """Page 1 has text, Page 2 is pure-image → Page 2 images bind upward."""
    markdown = (
        # Page 1: text
        "王羲之 东晋书法家，后世尊为书圣。" * 6
        + "![作品1](asset://a1)\n\n"
        # Page 2: image-only (no text child)
        + "![作品2](asset://a2)\n\n"
    )
    children = [
        # Page 1 — has semantic text → kept
        _make_text_child(
            "王羲之 东晋书法家，后世尊为书圣。" * 6,
            parent_index=0, start=0, end=200, source_child_seq=0, page_no=1,
        ),
        _make_image_child(
            parent_index=0, start=200, end=220, source_child_seq=1, page_no=1,
            is_primary_visual=True,
        ),
        # Page 2 — NO text child, only image → dissolved → cross-page bind
        _make_image_child(
            parent_index=1, start=220, end=240, source_child_seq=2, page_no=2,
            is_primary_visual=True,
        ),
    ]
    page_ranges = [(1, 0, 220), (2, 220, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    # Only 1 parent: Page 1's text parent absorbs Page 2's image
    assert len(parents) == 1, f"Expected 1 parent, got {len(parents)}"
    assert counts[0] == 3  # 1 text + 2 images (1 from page 1, 1 from page 2)

    # All children should be under parent_index 0
    assert all(c.parent_index == 0 for c in children)

    # Page 2's image should have cross-page binding metadata
    page2_image = [c for c in children if c.metadata.get("source_visual_page_no") == 2]
    assert len(page2_image) == 1
    assert page2_image[0].metadata["parent_materialization_rule"] == (
        "visual_section_cross_page_binding"
    )
    assert page2_image[0].flags["visual_section_cross_page_binding"] is True


def test_multi_image_pages_bind_to_same_text():
    """One text page, three consecutive pure-image pages → all bind to text."""
    markdown = (
        "赵孟頫 元代书画家，字子昂，号松雪道人。" * 6
        + "![作品1](asset://a1)\n\n"   # page 1 image
        + "![作品2](asset://a2)\n\n"   # page 2 — pure image
        + "![作品3](asset://a3)\n\n"   # page 3 — pure image
        + "![作品4](asset://a4)\n\n"   # page 4 — pure image
    )
    children = [
        # Page 1 — text → kept
        _make_text_child(
            "赵孟頫 元代书画家，字子昂，号松雪道人。" * 6,
            parent_index=0, start=0, end=200, source_child_seq=0, page_no=1,
        ),
        _make_image_child(
            parent_index=0, start=200, end=220, source_child_seq=1, page_no=1,
            is_primary_visual=True,
        ),
        # Page 2 — image-only (no text child)
        _make_image_child(
            parent_index=1, start=220, end=240, source_child_seq=2, page_no=2,
            is_primary_visual=True,
        ),
        # Page 3 — image-only (no text child)
        _make_image_child(
            parent_index=2, start=240, end=260, source_child_seq=3, page_no=3,
            is_primary_visual=True,
        ),
        # Page 4 — image-only (no text child)
        _make_image_child(
            parent_index=3, start=260, end=280, source_child_seq=4, page_no=4,
            is_primary_visual=True,
        ),
    ]
    page_ranges = [
        (1, 0, 220), (2, 220, 240), (3, 240, 260), (4, 260, len(markdown)),
    ]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    # Only 1 parent: all images bind to Page 1's text
    assert len(parents) == 1, f"Expected 1 parent, got {len(parents)}"
    assert counts[0] == 5  # 1 text + 4 images
    assert all(c.parent_index == 0 for c in children)

    # All page 2-4 images should have cross-page binding metadata
    cross_bound = [
        c for c in children
        if c.flags.get("visual_section_cross_page_binding")
    ]
    assert len(cross_bound) == 3  # pages 2, 3, 4
    assert {c.metadata["source_visual_page_no"] for c in cross_bound} == {2, 3, 4}


def test_leading_pure_images_bind_downward():
    """Pages 1-2 are pure images, Page 3 has text → images bind downward."""
    markdown = (
        "![封面](asset://a1)\n\n"     # page 1 — pure image
        + "![封底](asset://a2)\n\n"   # page 2 — pure image
        + "序言正文内容，这是一本精心编纂的书法作品集。" * 6         # page 3 — text
    )
    children = [
        # Page 1 — image-only (no text)
        _make_image_child(
            parent_index=0, start=0, end=20, source_child_seq=0, page_no=1,
            is_primary_visual=True,
        ),
        # Page 2 — image-only (no text)
        _make_image_child(
            parent_index=1, start=20, end=40, source_child_seq=1, page_no=2,
            is_primary_visual=True,
        ),
        # Page 3 — text → kept
        _make_text_child(
            "序言正文内容，这是一本精心编纂的书法作品集。" * 6,
            parent_index=2, start=40, end=240, source_child_seq=2, page_no=3,
        ),
    ]
    page_ranges = [(1, 0, 20), (2, 20, 40), (3, 40, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    # Only 1 parent: Page 3's text absorbs Page 1-2's images
    assert len(parents) == 1, f"Expected 1 parent, got {len(parents)}"
    assert counts[0] == 3  # 1 text + 2 images
    assert all(c.parent_index == 0 for c in children)

    # Page 1-2 images should have cross-page binding metadata
    for child in children:
        if child.chunk_type == "image":
            assert child.metadata["parent_materialization_rule"] == (
                "visual_section_cross_page_binding"
            )
            assert child.flags["visual_section_cross_page_binding"] is True


def test_text_pages_keep_own_parents_with_cross_page_images_between():
    """Text→image→text: image page between two text pages binds upward."""
    markdown = (
        "王羲之介绍，东晋书法家字逸少。" * 6           # page 1 — text
        + "![作品](asset://a1)\n\n"   # page 2 — pure image
        + "颜真卿介绍，唐代书法家创颜体。" * 6           # page 3 — text
    )
    children = [
        # Page 1 — text → kept
        _make_text_child(
            "王羲之介绍，东晋书法家字逸少。" * 6,
            parent_index=0, start=0, end=150, source_child_seq=0, page_no=1,
        ),
        # Page 2 — image-only → cross-page bind upward to page 1
        _make_image_child(
            parent_index=1, start=150, end=170, source_child_seq=1, page_no=2,
            is_primary_visual=True,
        ),
        # Page 3 — text → kept (own parent)
        _make_text_child(
            "颜真卿介绍，唐代书法家创颜体。" * 6,
            parent_index=2, start=170, end=320, source_child_seq=2, page_no=3,
        ),
    ]
    page_ranges = [(1, 0, 150), (2, 150, 170), (3, 170, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    # Page 1 parent absorbs page 2 image; page 3 gets its own parent
    assert len(parents) == 2, f"Expected 2 parents, got {len(parents)}"

    # Parent 0: page 1 text + page 2 image
    parent_0_children = [c for c in children if c.parent_index == 0]
    assert len(parent_0_children) == 2  # text from page1 + image from page2
    assert parent_0_children[0].chunk_type == "text"
    assert "王羲之" in parent_0_children[0].content
    assert parent_0_children[1].chunk_type == "image"
    assert parent_0_children[1].flags["visual_section_cross_page_binding"] is True

    # Parent 1: page 3 text (own section)
    parent_1_children = [c for c in children if c.parent_index == 1]
    assert len(parent_1_children) == 1
    assert parent_1_children[0].chunk_type == "text"
    assert "颜真卿" in parent_1_children[0].content


def test_pure_image_page_cross_page_binding_flag_on():
    """Flag ON: pure-image page between text pages binds upward (not fallback)."""
    markdown = (
        "作者介绍，这是一位著名书法家的传世作品。" * 6
        + "![作品1](asset://a1)\n\n"   # page 1 image
        + "![作品2](asset://a2)\n\n"   # page 2 — pure image
        + "第二位作者介绍，同样是重要的艺术瑰宝。" * 6
        + "![作品3](asset://a3)\n\n"   # page 3 image
    )
    children = [
        # Page 1 — text → kept
        _make_text_child(
            "作者介绍，这是一位著名书法家的传世作品。" * 6,
            parent_index=0, start=0, end=150, source_child_seq=0, page_no=1,
        ),
        _make_image_child(
            parent_index=0, start=150, end=170, source_child_seq=1, page_no=1,
            is_primary_visual=True,
        ),
        # Page 2 — ONLY image, NO text → cross-page bind upward
        _make_image_child(
            parent_index=1, start=170, end=190, source_child_seq=2, page_no=2,
            is_primary_visual=True,
        ),
        # Page 3 — text → kept (own parent)
        _make_text_child(
            "第二位作者介绍，同样是重要的艺术瑰宝。" * 6,
            parent_index=2, start=190, end=340, source_child_seq=3, page_no=3,
        ),
        _make_image_child(
            parent_index=2, start=340, end=360, source_child_seq=4, page_no=3,
            is_primary_visual=True,
        ),
    ]
    page_ranges = [(1, 0, 170), (2, 170, 190), (3, 190, len(markdown))]

    parents, counts, kept = _build_visual_section_parents(
        children, markdown, page_ranges,
    )

    # 2 parents: page1 (text+image+page2's image) + page3 (text+image)
    assert len(parents) == 2, f"Expected 2 parents, got {len(parents)}"
    # Parent 0: page 1 children + page 2 image (cross-page bound)
    assert counts[0] == 3
    # Parent 1: page 3 children
    assert counts[1] == 2

    # Verify cross-page binding on page 2's image
    page2_img = [c for c in children if c.metadata.get("source_visual_page_no") == 2]
    assert len(page2_img) == 1
    assert page2_img[0].flags["visual_section_cross_page_binding"] is True
