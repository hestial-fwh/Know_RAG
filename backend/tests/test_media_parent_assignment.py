"""Tests for unified Media Parent Assignment (_reassign_singleton_image_parents)
and Final Media Parent Assignment (finalize_media_parent_assignment).

Covers 15 original cases + 10 DCP3.1 override cases.
"""

from __future__ import annotations

import pytest

from app.services.chunker.canonical_parent_child import (
    CanonicalChildDraft,
    CanonicalParentDraft,
    CanonicalChunkPlan,
    _reassign_singleton_image_parents,
    _rebuild_parents_after_assignment,
    finalize_media_parent_assignment,
)
from app.services.document_parser.figure_linker import (
    FigureLink,
    FigureLinkResult,
    FigureReference,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text_child(
    content: str,
    parent_index: int | None,
    start: int = 0,
    end: int = 500,
    *,
    context_header: str = "",
    page_no: int | None = None,
    source_child_seq: int = 0,
) -> CanonicalChildDraft:
    metadata: dict = {
        "canonical_source_child_seq": source_child_seq,
        "content_kind": "text",
    }
    if page_no is not None:
        metadata["page_no"] = page_no
    return CanonicalChildDraft(
        content=content,
        chunk_type="text",
        parent_index=parent_index,
        context_header=context_header,
        start=start,
        end=end,
        source_child_seq=source_child_seq,
        metadata=metadata,
    )


def _image_child(
    parent_index: int | None,
    start: int = 500,
    end: int = 550,
    *,
    asset_id: str = "img_001",
    figure_ids: list[str] | None = None,
    context_header: str = "",
    page_no: int | None = None,
    source_child_seq: int = 1,
    visual_role: str = "",
) -> CanonicalChildDraft:
    metadata: dict = {
        "canonical_source_child_seq": source_child_seq,
        "content_kind": "image",
        "asset_id": asset_id,
        "figure_ids": list(figure_ids or []),
    }
    if page_no is not None:
        metadata["page_no"] = page_no
    if visual_role:
        metadata["visual_role"] = visual_role
    return CanonicalChildDraft(
        content="图片内容描述",
        chunk_type="image",
        parent_index=parent_index,
        context_header=context_header,
        start=start,
        end=end,
        source_child_seq=source_child_seq,
        metadata=metadata,
    )


def _parent(pidx: int, *, children_count: int = 2, start: int = 0, end: int = 2000) -> CanonicalParentDraft:
    return CanonicalParentDraft(
        parent_index=pidx,
        content="parent content",
        context_header="",
        start=start,
        end=end,
        metadata={
            "chunk_role": "parent",
            "children_count_planned": children_count,
        },
    )


def _page_ranges(*pages: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    return list(pages)


def _figure_link(
    figure_id: str = "fig1",
    ref_start: int = 100,
    ref_end: int = 120,
    asset_ids: tuple[str, ...] = ("img_001",),
    *,
    status: str = "bound",
    binding_score: float = 0.85,
    binding_method: str = "figure_id_exact",
    page_no: int | None = None,
) -> FigureLink:
    return FigureLink(
        reference=FigureReference(
            figure_id=figure_id,
            raw_text=f"图{figure_id}",
            start=ref_start,
            end=ref_end,
            page_no=page_no,
        ),
        status=status,
        asset_ids=asset_ids,
        binding_method=binding_method,
        binding_score=binding_score,
    )


def _make_plan(children, parents, page_ranges=None, figure_links=None):
    """Minimal CanonicalChunkPlan builder for DCP3.1 override tests."""
    return CanonicalChunkPlan(
        markdown_content="",
        parents=parents,
        children=children,
        figure_links=figure_links or FigureLinkResult(),
        page_ranges=list(page_ranges or []),
    )


# ============================================================================
# Original 15 tests (Cases 1-10 + 5 edge cases)
# ============================================================================


def test_case1_singleton_image_reassigns_to_same_page_text_parent():
    """Image orphaned in standard mode finds text parent via same-page proximity."""
    children = [
        _text_child("这是正文内容", parent_index=0, start=0, end=500, page_no=1,
                     context_header="## 发明内容", source_child_seq=0),
        _image_child(parent_index=None, start=550, end=600,
                      asset_id="img_001", page_no=1,
                      context_header="## 发明内容", source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=1000),
    ]
    page_ranges = _page_ranges((1, 0, 1000))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, FigureLinkResult(),
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 0, (
        f"Expected parent 0, got {image.parent_index}"
    )
    assert image.metadata["parent_materialized"] is True
    assert "media_parent_assignment" in image.metadata
    assert image.metadata["media_parent_assignment"]["strategy"] in (
        "same_section", "same_page_proximity"
    )
    assert image.flags["media_parent_reassigned"] is True


def test_case2_explicit_figure_link_binds_across_parents():
    """Text references 'Fig.1', FigureLink targets the image's asset_id."""
    children = [
        _text_child("其结构如图1所示，包括", parent_index=0, start=0, end=200,
                     page_no=1, source_child_seq=0),
        _image_child(parent_index=1, start=300, end=350,
                      asset_id="img_fig1", figure_ids=["fig1"],
                      page_no=1, source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=250),
        _parent(1, children_count=1, start=250, end=400),
    ]
    fl_result = FigureLinkResult(links=[
        _figure_link(figure_id="fig1", ref_start=50, ref_end=80,
                      asset_ids=("img_fig1",), binding_score=0.92),
    ])
    page_ranges = _page_ranges((1, 0, 500))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 0, (
        f"Expected parent 0 (text parent), got {image.parent_index}"
    )
    assert image.metadata["media_parent_assignment"]["strategy"] == "explicit_binding"
    assert image.metadata["media_parent_assignment"]["confidence"] == 0.92
    assert len(new_parents) == 1
    assert new_parents[0].parent_index == 0


def test_case3_same_page_same_section_without_strict_overlap():
    """Image and text on same page with same heading — P2 section match."""
    children = [
        _text_child("## 具体实施方式\n\n本发明的清洁模块包括滚刷...",
                     parent_index=0, start=0, end=800,
                     page_no=3, context_header="## 具体实施方式",
                     source_child_seq=0),
        _image_child(parent_index=None, start=2000, end=2050,
                      asset_id="img_002", page_no=3,
                      context_header="## 具体实施方式",
                      source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=1500),
    ]
    page_ranges = _page_ranges((3, 0, 3000))
    fl_result = FigureLinkResult()

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 0, (
        f"Expected parent 0, got {image.parent_index}"
    )
    strategy = image.metadata["media_parent_assignment"]["strategy"]
    assert strategy in ("same_section", "same_page_proximity"), (
        f"Unexpected strategy: {strategy}"
    )


def test_case4_cross_page_explicit_figure_link_reassigns():
    """Page N+1 image with explicit FigureLink from page N text."""
    children = [
        _text_child("参见图3所示结构", parent_index=0, start=0, end=200,
                     page_no=5, source_child_seq=0),
        _image_child(parent_index=1, start=3000, end=3050,
                      asset_id="img_fig3", figure_ids=["fig3"],
                      page_no=6, source_child_seq=3,
                      visual_role="page_primary_visual"),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=500),
        _parent(1, children_count=1, start=3000, end=3500),
    ]
    fl_result = FigureLinkResult(links=[
        _figure_link(figure_id="fig3", ref_start=50, ref_end=70,
                      asset_ids=("img_fig3",), binding_score=0.88,
                      page_no=5),
    ])
    page_ranges = _page_ranges((5, 0, 2500), (6, 2500, 4000))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 0, (
        f"Expected cross-page reassignment to parent 0, got {image.parent_index}"
    )
    strategy = image.metadata["media_parent_assignment"]["strategy"]
    assert strategy in ("explicit_binding", "cross_page_reference"), (
        f"Unexpected strategy: {strategy}"
    )
    parent_ids = {p.parent_index for p in new_parents}
    assert 1 not in parent_ids, "Singleton parent 1 should be removed"


def test_case5_singleton_image_without_evidence_keeps_parent():
    """Pure visual page with no text reference — P5 keeps as-is."""
    children = [
        _image_child(parent_index=0, start=0, end=50,
                      asset_id="img_pure", page_no=2,
                      visual_role="page_primary_visual",
                      source_child_seq=0),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=500),
    ]
    fl_result = FigureLinkResult()
    page_ranges = _page_ranges((2, 0, 500))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 0, (
        f"Pure visual page should keep parent 0, got {image.parent_index}"
    )
    assert "media_parent_assignment" not in image.metadata, (
        "Pure visual page should not be reassigned"
    )
    assert len(new_parents) == 1, "Parent should be preserved"


def test_case6_calligraphy_page_keeps_singleton_parent():
    """Calligraphy page image must not be absorbed into adjacent text."""
    children = [
        _text_child("前页正文内容", parent_index=0, start=0, end=500,
                     page_no=3, source_child_seq=0),
        _image_child(parent_index=1, start=1000, end=1050,
                      asset_id="img_calligraphy", page_no=4,
                      visual_role="page_primary_visual",
                      source_child_seq=1),
        _text_child("后页正文内容", parent_index=2, start=2000, end=2500,
                     page_no=5, source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=800),
        _parent(1, children_count=1, start=1000, end=1500),
        _parent(2, children_count=1, start=2000, end=2800),
    ]
    fl_result = FigureLinkResult()
    page_ranges = _page_ranges((3, 0, 900), (4, 1000, 1800), (5, 2000, 3000))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 1, (
        f"Calligraphy should keep parent 1, got {image.parent_index}"
    )


def test_case7_same_page_two_sections_image_binds_to_correct_section():
    """Image with section A heading must go to section A, not nearer section B."""
    children = [
        _text_child("## 背景技术\n\n现有技术中...",
                     parent_index=0, start=0, end=500,
                     page_no=2, context_header="## 背景技术",
                     source_child_seq=0),
        _image_child(parent_index=None, start=600, end=650,
                      asset_id="img_bg", page_no=2,
                      context_header="## 背景技术",
                      source_child_seq=1),
        _text_child("## 发明内容\n\n本发明提供...",
                     parent_index=1, start=700, end=1200,
                     page_no=2, context_header="## 发明内容",
                     source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=550),
        _parent(1, children_count=1, start=700, end=1300),
    ]
    fl_result = FigureLinkResult()
    page_ranges = _page_ranges((2, 0, 1500))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 0, (
        f"Image should bind to section A (parent 0), got {image.parent_index}"
    )
    strategy = image.metadata["media_parent_assignment"]["strategy"]
    assert strategy == "same_section", (
        f"Expected same_section strategy, got {strategy}"
    )


def test_case8_image_before_text_allows_forward_binding_to_text():
    """Image appears before its reference text on the same page."""
    children = [
        _image_child(parent_index=None, start=0, end=50,
                      asset_id="img_first", page_no=1,
                      context_header="## 实施例", source_child_seq=0),
        _text_child("如图4所示，该装置包括...",
                     parent_index=0, start=100, end=600,
                     page_no=1, context_header="## 实施例",
                     source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=1, start=100, end=700),
    ]
    fl_result = FigureLinkResult()
    page_ranges = _page_ranges((1, 0, 800))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 0, (
        f"Image should bind to text parent 0, got {image.parent_index}"
    )


def test_case9_text_before_image_allows_backward_binding_to_text():
    """Text appears before its image on the same page."""
    children = [
        _text_child("具体结构参照图5...",
                     parent_index=0, start=0, end=300,
                     page_no=1, context_header="## 实施例",
                     source_child_seq=0),
        _image_child(parent_index=None, start=400, end=450,
                      asset_id="img_after", page_no=1,
                      context_header="## 实施例", source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=350),
    ]
    fl_result = FigureLinkResult()
    page_ranges = _page_ranges((1, 0, 500))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 0, (
        f"Image should bind to text parent 0, got {image.parent_index}"
    )


def test_case10_cross_page_without_reference_no_random_binding():
    """Text page, image page, text page — no binding without evidence."""
    children = [
        _text_child("第一页正文", parent_index=0, start=0, end=300,
                     page_no=1, source_child_seq=0),
        _image_child(parent_index=1, start=500, end=550,
                      asset_id="img_mid", page_no=2,
                      visual_role="page_primary_visual",
                      source_child_seq=1),
        _text_child("第三页正文", parent_index=2, start=800, end=1100,
                     page_no=3, source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=400),
        _parent(1, children_count=1, start=500, end=600),
        _parent(2, children_count=1, start=800, end=1200),
    ]
    fl_result = FigureLinkResult()
    page_ranges = _page_ranges((1, 0, 450), (2, 500, 700), (3, 800, 1300))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 1, (
        f"Image should stay in parent 1, got {image.parent_index}"
    )
    assert "media_parent_assignment" not in image.metadata, (
        "Cross-page without evidence should not be reassigned"
    )


def test_multiple_orphan_images_all_reassigned():
    """Multiple orphan images on same page should all find text parent."""
    children = [
        _text_child("## 附图说明\n\n图6-8展示了...",
                     parent_index=0, start=0, end=500,
                     page_no=4, context_header="## 附图说明",
                     source_child_seq=0),
        _image_child(parent_index=None, start=600, end=650,
                      asset_id="img_6", page_no=4,
                      context_header="## 附图说明", source_child_seq=1),
        _image_child(parent_index=None, start=700, end=750,
                      asset_id="img_7", page_no=4,
                      context_header="## 附图说明", source_child_seq=2),
        _image_child(parent_index=None, start=800, end=850,
                      asset_id="img_8", page_no=4,
                      context_header="## 附图说明", source_child_seq=3),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=550),
    ]
    fl_result = FigureLinkResult()
    page_ranges = _page_ranges((4, 0, 1000))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    images = [c for c in new_children if c.chunk_type == "image"]
    assert len(images) == 3
    for img in images:
        assert img.parent_index == 0, (
            f"Image {img.metadata.get('asset_id')} should be in parent 0, "
            f"got {img.parent_index}"
        )


def test_no_reassignment_when_no_text_parent_exists():
    """If all parents are image-only, keep original assignments."""
    children = [
        _image_child(parent_index=None, start=0, end=50,
                      asset_id="img_a", page_no=1,
                      source_child_seq=0),
        _image_child(parent_index=None, start=100, end=150,
                      asset_id="img_b", page_no=1,
                      source_child_seq=1),
    ]
    parents: list[CanonicalParentDraft] = []
    fl_result = FigureLinkResult()
    page_ranges = _page_ranges((1, 0, 200))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    for img in new_children:
        assert img.parent_index is None, (
            f"Image {img.metadata.get('asset_id')} should stay orphan"
        )


def test_unmatched_figure_link_does_not_trigger_reassignment():
    """Only 'bound' FigureLinks trigger P1 reassignment."""
    children = [
        _text_child("参见图9", parent_index=0, start=0, end=100,
                     page_no=1, source_child_seq=0),
        _image_child(parent_index=1, start=200, end=250,
                      asset_id="img_fig9", figure_ids=["fig9"],
                      page_no=1, source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=150),
        _parent(1, children_count=1, start=200, end=300),
    ]
    fl_result = FigureLinkResult(links=[
        _figure_link(figure_id="fig9", ref_start=10, ref_end=30,
                      asset_ids=("img_fig9",), status="unmatched"),
    ])
    page_ranges = _page_ranges((1, 0, 400))

    new_children, _ = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 1 or image.parent_index == 0, (
        "Unmatched link alone should not force reassignment"
    )


def test_figure_link_same_parent_no_reassignment():
    """When text and image already share a parent, no reassignment needed."""
    children = [
        _text_child("如图2所示", parent_index=0, start=0, end=100,
                     page_no=1, source_child_seq=0),
        _image_child(parent_index=0, start=200, end=250,
                      asset_id="img_fig2", figure_ids=["fig2"],
                      page_no=1, source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=2, start=0, end=300),
    ]
    fl_result = FigureLinkResult(links=[
        _figure_link(figure_id="fig2", ref_start=10, ref_end=30,
                      asset_ids=("img_fig2",)),
    ])

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=_page_ranges((1, 0, 400)),
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 0, "Should stay in parent 0"
    assert len(new_parents) == 1, "Parent should not be removed"


def test_p1_priority_over_p2_p3():
    """Explicit FigureLink (P1) should be used even when P2/P3 also match."""
    children = [
        _text_child("## 背景技术\n\n现有技术方案包括...",
                     parent_index=0, start=0, end=300,
                     page_no=1, context_header="## 背景技术",
                     source_child_seq=0),
        _text_child("## 具体实施方式\n\n其结构如图1所示...",
                     parent_index=1, start=400, end=700,
                     page_no=1, context_header="## 具体实施方式",
                     source_child_seq=1),
        _image_child(parent_index=None, start=320, end=370,
                      asset_id="img_fig1", figure_ids=["fig1"],
                      page_no=1, source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=350),
        _parent(1, children_count=1, start=400, end=750),
    ]
    fl_result = FigureLinkResult(links=[
        _figure_link(figure_id="fig1", ref_start=480, ref_end=510,
                      asset_ids=("img_fig1",), binding_score=0.95),
    ])
    page_ranges = _page_ranges((1, 0, 800))

    new_children, new_parents = _reassign_singleton_image_parents(
        children, parents, fl_result,
        page_ranges=page_ranges,
    )

    image = next(c for c in new_children if c.chunk_type == "image")
    assert image.parent_index == 1, (
        f"P1 should bind to parent 1 (explicit reference), got {image.parent_index}"
    )
    assert image.metadata["media_parent_assignment"]["strategy"] == "explicit_binding"


# ============================================================================
# DCP3.1 override tests (10 cases)
# ============================================================================


def test_dcp31_singleton_images_reassigned_to_text_parent():
    """Simulate DCP3.1 output: 2 singleton image parents + text parent on same page.
    After finalize_media_parent_assignment, images should merge into text parent."""
    children = [
        _text_child("具体实施方式\n\n本发明的清洁模块...",
                     parent_index=0, start=0, end=500,
                     page_no=7, context_header="具体实施方式",
                     source_child_seq=0),
        _image_child(parent_index=1, start=600, end=650,
                      asset_id="img_a", page_no=7,
                      context_header="具体实施方式",
                      source_child_seq=1),
        _image_child(parent_index=2, start=700, end=750,
                      asset_id="img_b", page_no=7,
                      context_header="具体实施方式",
                      source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=550),
        _parent(1, children_count=1, start=600, end=700),
        _parent(2, children_count=1, start=700, end=800),
    ]
    plan = _make_plan(children, parents, page_ranges=_page_ranges((7, 0, 1000)))
    plan = finalize_media_parent_assignment(plan)

    images = [c for c in plan.children if c.chunk_type == "image"]
    assert len(images) == 2
    for img in images:
        assert img.parent_index == 0, (
            f"Image {img.metadata.get('asset_id')} should be in text parent 0, "
            f"got {img.parent_index}"
        )
    assert len(plan.parents) == 1
    assert plan.parents[0].metadata["children_count_planned"] == 3


def test_dcp31_one_image_has_owner_one_may_stay():
    """Image A has same-section text owner; Image B has no heading evidence."""
    children = [
        _text_child("## 实施例1\n\n制备方法如下...",
                     parent_index=0, start=0, end=300,
                     page_no=5, context_header="## 实施例1",
                     source_child_seq=0),
        _image_child(parent_index=1, start=400, end=450,
                      asset_id="img_with_owner", page_no=5,
                      context_header="## 实施例1",
                      source_child_seq=1),
        _image_child(parent_index=2, start=500, end=550,
                      asset_id="img_no_owner", page_no=5,
                      context_header="",
                      source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=350),
        _parent(1, children_count=1, start=400, end=500),
        _parent(2, children_count=1, start=500, end=600),
    ]
    plan = _make_plan(children, parents, page_ranges=_page_ranges((5, 0, 700)))
    plan = finalize_media_parent_assignment(plan)

    images = {c.metadata.get("asset_id"): c for c in plan.children if c.chunk_type == "image"}
    assert images["img_with_owner"].parent_index == 0
    img_b = images["img_no_owner"]
    assert img_b.parent_index is not None, "Image without heading should at least have a parent"


def test_dcp31_two_sections_image_binds_to_correct_one():
    """Section A text + Section B text + image with Section A heading."""
    children = [
        _text_child("## 背景技术\n\n现有方案...",
                     parent_index=0, start=0, end=300,
                     page_no=2, context_header="## 背景技术",
                     source_child_seq=0),
        _image_child(parent_index=1, start=400, end=450,
                      asset_id="img_bg", page_no=2,
                      context_header="## 背景技术",
                      source_child_seq=1),
        _text_child("## 发明内容\n\n本发明...",
                     parent_index=2, start=500, end=800,
                     page_no=2, context_header="## 发明内容",
                     source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=350),
        _parent(1, children_count=1, start=400, end=500),
        _parent(2, children_count=1, start=500, end=850),
    ]
    plan = _make_plan(children, parents, page_ranges=_page_ranges((2, 0, 1000)))
    plan = finalize_media_parent_assignment(plan)

    image = next(c for c in plan.children if c.chunk_type == "image")
    final_parent_idx = image.parent_index
    text_children_in_parent = [
        c for c in plan.children
        if c.parent_index == final_parent_idx and c.chunk_type == "text"
    ]
    assert any("背景技术" in c.content for c in text_children_in_parent), (
        f"Image should be in 背景技术 parent, got parent {final_parent_idx}"
    )


def test_dcp31_calligraphy_two_visual_items_kept_separate():
    """Two calligraphy images, no text → both keep independent parents."""
    children = [
        _image_child(parent_index=0, start=0, end=50,
                      asset_id="img_call_a", page_no=3,
                      visual_role="page_primary_visual",
                      source_child_seq=0),
        _image_child(parent_index=1, start=100, end=150,
                      asset_id="img_call_b", page_no=3,
                      visual_role="page_primary_visual",
                      source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=80),
        _parent(1, children_count=1, start=100, end=180),
    ]
    plan = _make_plan(children, parents, page_ranges=_page_ranges((3, 0, 200)))
    plan = finalize_media_parent_assignment(plan)

    images = [c for c in plan.children if c.chunk_type == "image"]
    assert len(images) == 2
    assert len(plan.parents) == 2, "Both calligraphy images should keep independent parents"
    for img in images:
        assert img.parent_index is not None


def test_dcp31_cross_page_figure_link_reassigns():
    """Page N text references Fig.5; page N+1 has Fig.5 image."""
    children = [
        _text_child("如图5所示的结构包括...",
                     parent_index=0, start=0, end=200,
                     page_no=4, source_child_seq=0),
        _image_child(parent_index=1, start=1000, end=1050,
                      asset_id="img_fig5", figure_ids=["fig5"],
                      page_no=5, source_child_seq=1,
                      visual_role="page_primary_visual"),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=300),
        _parent(1, children_count=1, start=1000, end=1100),
    ]
    fl_result = FigureLinkResult(links=[
        _figure_link(figure_id="fig5", ref_start=50, ref_end=70,
                      asset_ids=("img_fig5",), binding_score=0.91),
    ])
    plan = _make_plan(children, parents,
                      page_ranges=_page_ranges((4, 0, 800), (5, 800, 1200)),
                      figure_links=fl_result)
    plan = finalize_media_parent_assignment(plan)

    image = next(c for c in plan.children if c.chunk_type == "image")
    assert image.parent_index == 0, (
        f"Cross-page explicit FigureLink should bind to parent 0, got {image.parent_index}"
    )


def test_dcp31_cross_page_no_reference_no_binding():
    """Text page, image-only page, text page — no binding without evidence."""
    children = [
        _text_child("前页正文", parent_index=0, start=0, end=200,
                     page_no=1, source_child_seq=0),
        _image_child(parent_index=1, start=500, end=550,
                      asset_id="img_mid", page_no=2,
                      visual_role="page_primary_visual",
                      source_child_seq=1),
        _text_child("后页正文", parent_index=2, start=800, end=1000,
                     page_no=3, source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=300),
        _parent(1, children_count=1, start=500, end=600),
        _parent(2, children_count=1, start=800, end=1100),
    ]
    plan = _make_plan(children, parents,
                      page_ranges=_page_ranges((1, 0, 400), (2, 400, 700), (3, 700, 1200)))
    plan = finalize_media_parent_assignment(plan)

    image = next(c for c in plan.children if c.chunk_type == "image")
    text_parents = {c.parent_index for c in plan.children if c.chunk_type == "text"}
    assert image.parent_index not in text_parents, (
        "Cross-page without evidence should not bind image to text pages"
    )


def test_dcp31_parent_rebuild_removes_empties_and_renumbers():
    """After assignment moves image from parent 0 to parent 1, parent 0 removed."""
    children = [
        _image_child(parent_index=0, start=0, end=50,
                      asset_id="img_x", page_no=1,
                      context_header="## 实施例", source_child_seq=0),
        _text_child("实施例正文...", parent_index=1, start=100, end=500,
                     page_no=1, context_header="## 实施例",
                     source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=80),
        _parent(1, children_count=1, start=100, end=550),
    ]
    plan = _make_plan(children, parents, page_ranges=_page_ranges((1, 0, 600)))
    plan = finalize_media_parent_assignment(plan)

    assert len(plan.parents) == 1, f"Expected 1 parent, got {len(plan.parents)}"
    assert plan.parents[0].parent_index == 0
    assert plan.parents[0].metadata["children_count_planned"] == 2
    for child in plan.children:
        assert child.parent_index is not None
        assert child.parent_index in {p.parent_index for p in plan.parents}


def test_dcp31_child_parent_index_exists_in_plan_parents():
    """Integrity check: no dangling parent_index references."""
    children = [
        _text_child("正文A", parent_index=0, start=0, end=200, page_no=1,
                     context_header="## A", source_child_seq=0),
        _image_child(parent_index=None, start=300, end=350, asset_id="img_o",
                      page_no=1, context_header="## A", source_child_seq=1),
        _text_child("正文B", parent_index=1, start=400, end=600, page_no=1,
                     context_header="## B", source_child_seq=2),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=250),
        _parent(1, children_count=1, start=400, end=650),
    ]
    plan = _make_plan(children, parents, page_ranges=_page_ranges((1, 0, 700)))
    plan = finalize_media_parent_assignment(plan)

    valid_indexes = {p.parent_index for p in plan.parents}
    for child in plan.children:
        if child.parent_index is not None:
            assert child.parent_index in valid_indexes, (
                f"Child parent_index {child.parent_index} not in {valid_indexes}"
            )


def test_dcp31_text_image_same_parent_after_assignment():
    """After final assignment, text and image should share a parent."""
    children = [
        _text_child("具体实施方式正文...",
                     parent_index=0, start=0, end=400,
                     page_no=7, context_header="具体实施方式",
                     source_child_seq=0),
        _image_child(parent_index=1, start=500, end=550,
                      asset_id="img_rel", page_no=7,
                      context_header="具体实施方式",
                      source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=450),
        _parent(1, children_count=1, start=500, end=600),
    ]
    plan = _make_plan(children, parents, page_ranges=_page_ranges((7, 0, 700)))
    plan = finalize_media_parent_assignment(plan)

    text_parents = {c.parent_index for c in plan.children if c.chunk_type == "text"}
    image_parents = {c.parent_index for c in plan.children if c.chunk_type == "image"}
    assert text_parents == image_parents, (
        f"Text parents {text_parents} and image parents {image_parents} should match"
    )


def test_dcp31_pure_visual_page_kept():
    """Page with only images and NO text → all image-only parents kept."""
    children = [
        _image_child(parent_index=0, start=0, end=50,
                      asset_id="img_1", page_no=2,
                      visual_role="page_primary_visual",
                      source_child_seq=0),
        _image_child(parent_index=1, start=100, end=150,
                      asset_id="img_2", page_no=2,
                      visual_role="page_primary_visual",
                      source_child_seq=1),
    ]
    parents = [
        _parent(0, children_count=1, start=0, end=80),
        _parent(1, children_count=1, start=100, end=180),
    ]
    plan = _make_plan(children, parents, page_ranges=_page_ranges((2, 0, 200)))
    plan = finalize_media_parent_assignment(plan)

    text_count = sum(1 for c in plan.children if c.chunk_type == "text")
    assert text_count == 0
    assert len(plan.parents) == 2, "Pure visual page should keep all image parents"
