from app.services.chunker.canonical_parent_child import (
    CanonicalChildDraft,
    _build_visual_page_parents,
    _rehome_single_overlapping_orphans,
    _split_visual_pages_parent_child,
    _visual_page_ranges,
    build_canonical_parent_child_plan,
    is_structural_only,
)
from app.services.chunker.models import ChunkerConfig, ParsedChunk
from app.services.document_parser.canonical_document import (
    CanonicalAsset,
    CanonicalDocument,
    CanonicalSourceNode,
    build_canonical_document,
)
from app.services.document_parser.types import ParsedBlock, ParsedDocument


def _cfg() -> ChunkerConfig:
    return ChunkerConfig(
        enable_parent_child=True,
        chunk_size=512,
        chunk_overlap=80,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
        separators=["\n\n", "\n", "。"],
    )


def test_singleton_orphan_rehomes_to_best_overlapping_parent_window():
    parents = [
        ParsedChunk(
            content="left parent",
            context_header="## Section",
            seq=0,
            start=0,
            end=11107,
            metadata={},
            chunk_type="parent_text",
        ),
        ParsedChunk(
            content="right parent",
            context_header="## Section",
            seq=1,
            start=11167,
            end=18709,
            metadata={},
            chunk_type="parent_text",
        ),
    ]
    child = CanonicalChildDraft(
        content="meaningful boundary paragraph",
        chunk_type="text",
        parent_index=None,
        context_header="## Section",
        start=10954,
        end=11308,
        source_child_seq=29,
        metadata={
            "canonical_source_child_seq": 29,
            "parent_materialized": False,
        },
    )
    counts = {0: 2, 1: 2}

    _rehome_single_overlapping_orphans(
        [child],
        parents,
        kept_parent_indexes={0, 1},
        counts=counts,
    )

    assert child.parent_index == 0
    assert child.metadata["parent_rehome_overlap_chars"] == 153
    assert child.metadata["parent_materialization_rule"] == (
        "overlapping_surviving_parent_window"
    )
    assert child.flags["parent_rehomed_from_orphan"] is True
    assert counts[0] == 3


def test_visual_page_dominant_document_splits_parents_inside_page_boundaries():
    page_texts = [
        "## Work A\n\n" + ("page one artwork description. " * 35),
        "## Work B\n\n" + ("page two artwork description. " * 35),
        "## Work C\n\n" + ("page three artwork description. " * 35),
    ]
    markdown = "".join(page_texts)
    starts = [0, len(page_texts[0]), len(page_texts[0]) + len(page_texts[1])]
    assets = {
        f"image-{page_no}": CanonicalAsset(
            asset_id=f"image-{page_no}",
            page_no=page_no,
            metadata={"visual_role": "page_primary_visual"},
        )
        for page_no in range(1, 4)
    }
    nodes = [
        CanonicalSourceNode(
            node_id=f"node-{page_no}",
            node_type="text",
            source_order=page_no - 1,
            canonical_text=page_texts[page_no - 1],
            text_hash=f"hash-{page_no}",
            page_no=page_no,
            normalized_start=starts[page_no - 1],
            normalized_end=starts[page_no - 1] + len(page_texts[page_no - 1]),
        )
        for page_no in range(1, 4)
    ]
    canonical = CanonicalDocument(
        markdown_content=markdown,
        assets=assets,
        provenance_nodes=nodes,
        source_format="pdf",
    )

    ranges = _visual_page_ranges(
        canonical,
        nodes,
        markdown_length=len(markdown),
    )
    result = _split_visual_pages_parent_child(markdown, _cfg(), ranges)

    assert ranges == [
        (1, starts[0], starts[1]),
        (2, starts[1], starts[2]),
        (3, starts[2], len(markdown)),
    ]
    assert len(result.parents) == 3
    assert {
        parent.metadata["visual_page_no"] for parent in result.parents
    } == {1, 2, 3}
    for parent in result.parents:
        page_no = parent.metadata["visual_page_no"]
        _, page_start, page_end = ranges[page_no - 1]
        assert page_start <= parent.start < parent.end <= page_end
        assert f"Work {chr(64 + page_no)}" in parent.content
        assert all(
            f"Work {chr(64 + other_page)}" not in parent.content
            for other_page in range(1, 4)
            if other_page != page_no
        )
    assert all(child.metadata["visual_page_scoped_split"] is True for child in result.children)


def test_visual_page_ranges_use_normalized_markdown_length_for_last_page():
    canonical = CanonicalDocument(
        markdown_content="x" * 100,
        assets={
            f"image-{page_no}": CanonicalAsset(
                asset_id=f"image-{page_no}",
                page_no=page_no,
                metadata={"visual_role": "page_primary_visual"},
            )
            for page_no in range(1, 4)
        },
    )
    nodes = [
        CanonicalSourceNode(
            node_id=f"node-{page_no}",
            node_type="text",
            source_order=page_no,
            canonical_text="x",
            text_hash=f"hash-{page_no}",
            page_no=page_no,
            normalized_start=start,
            normalized_end=start + 1,
        )
        for page_no, start in ((1, 0), (2, 40), (3, 80))
    ]

    ranges = _visual_page_ranges(
        canonical,
        nodes,
        markdown_length=140,
    )

    assert ranges[-1] == (3, 80, 140)


def test_visual_page_parent_collects_all_same_page_children():
    children = [
        CanonicalChildDraft(
            content="work A description",
            chunk_type="text",
            parent_index=None,
            context_header="## Work A",
            start=0,
            end=80,
            source_child_seq=0,
            metadata={"visual_page_no": 1},
        ),
        CanonicalChildDraft(
            content="work A image",
            chunk_type="image",
            parent_index=None,
            context_header="## Work A",
            start=80,
            end=100,
            source_child_seq=1,
            metadata={
                "visual_page_no": 1,
                "visual_role": "page_primary_visual",
            },
        ),
        CanonicalChildDraft(
            content="work B image",
            chunk_type="image",
            parent_index=None,
            context_header="## Work B",
            start=100,
            end=140,
            source_child_seq=2,
            metadata={
                "visual_page_no": 2,
                "visual_role": "page_primary_visual",
            },
        ),
    ]

    parents, counts, _ = _build_visual_page_parents(
        children,
        ("A" * 100) + ("B" * 40),
        [(1, 0, 100), (2, 100, 140)],
    )

    assert len(parents) == 2
    assert counts == {0: 2, 1: 1}
    assert [child.parent_index for child in children] == [0, 0, 1]
    assert parents[0].metadata["visual_page_no"] == 1
    assert parents[1].metadata["singleton_primary_visual_parent"] is True
    assert parents[1].content == "work B image"
    assert all(child.metadata["visual_page_scoped_parent"] is True for child in children)


def test_heading_and_separator_do_not_become_standalone_children():
    directory_lines = "\n".join(
        f"backend/path/{i}/file.py # description {i}" for i in range(50)
    )
    markdown = f"---\n\n## 3. 目录结构\n\n```text\n{directory_lines}\n```\n"

    plan = build_canonical_parent_child_plan(
        build_canonical_document(
            ParsedDocument.from_text(markdown),
            file_type="md",
            document_title="后端架构与开发指南",
        ),
        _cfg(),
    )

    assert len(plan.children) >= 2
    assert not any(is_structural_only(child.content) for child in plan.children)
    assert not any(child.content.strip() == "## 3. 目录结构" for child in plan.children)
    assert all(len(child.content) < 600 for child in plan.children)
    assert all("## 3. 目录结构" in child.context_header for child in plan.children)
    assert all(parent.metadata["children_count_planned"] >= 2 for parent in plan.parents)


def test_all_file_types_share_canonical_markdown_splitter_shape():
    file_types = ["pdf", "docx", "pptx", "xlsx", "csv", "html", "txt", "json", "png"]
    for file_type in file_types:
        parsed = ParsedDocument(
            content="",
            blocks=[
                ParsedBlock(
                    block_type="text",
                    text="统一架构",
                    order_index=0,
                    metadata={"is_heading": True, "heading_level": 2},
                ),
                ParsedBlock(block_type="text", text="第一段内容。" * 80, order_index=1),
                ParsedBlock(block_type="text", text="第二段内容。" * 80, order_index=2),
            ],
        )
        canonical = build_canonical_document(parsed, file_type=file_type, document_title="测试")
        plan = build_canonical_parent_child_plan(canonical, _cfg())

        assert canonical.metadata["canonical_markdown"] is True
        assert canonical.markdown_content.startswith("## 统一架构")
        assert plan.children
        assert not any(is_structural_only(child.content) for child in plan.children)
        assert all(parent.metadata["children_count_planned"] >= 2 for parent in plan.parents)


def test_image_asset_sidecar_is_materialized_as_one_image_child_with_vlm_and_ocr():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="系统架构",
                order_index=0,
                metadata={"is_heading": True, "heading_level": 2},
            ),
            ParsedBlock(block_type="text", text="系统整体架构如下。", order_index=1),
            ParsedBlock(
                block_type="image",
                text="图 1 系统总体架构",
                order_index=2,
                image_url="/files/kb/doc/media/arch.png",
                page_no=3,
                bbox=(10, 20, 300, 240),
                metadata={
                    "caption": "图 1 系统总体架构",
                    "vlm_description": "左侧为 Vue3 前端，中间为 FastAPI 后端，后端连接 PostgreSQL、Elasticsearch 和 MinIO。",
                    "ocr_text": "Vue3 FastAPI PostgreSQL Elasticsearch MinIO",
                },
            ),
            ParsedBlock(block_type="text", text="系统采用前后端分离设计。", order_index=3),
        ],
    )

    canonical = build_canonical_document(parsed, file_type="pdf", document_title="系统说明")
    assert "asset://" in canonical.markdown_content
    assert "左侧为 Vue3" not in canonical.markdown_content
    assert len(canonical.assets) == 1

    plan = build_canonical_parent_child_plan(canonical, _cfg())
    image_children = [child for child in plan.children if child.chunk_type == "image"]
    assert len(image_children) == 1

    image = image_children[0]
    assert "图片标题：图 1 系统总体架构" in image.content
    assert "图片描述：左侧为 Vue3 前端" in image.content
    assert "图片文字：Vue3 FastAPI PostgreSQL Elasticsearch MinIO" in image.content
    # When caption exists, nearby_before/after are excluded; only local_context (tail) is kept.
    assert "系统整体架构如下" not in image.content
    assert "系统采用前后端分离设计" in image.content  # local_context (tail)
    assert image.image_info["image_url"] == "/files/kb/doc/media/arch.png"
    assert image.metadata["vlm_description"]
    assert image.metadata["ocr_text"]
    assert image.flags["has_vlm_description"] is True
    assert image.flags["has_ocr_text"] is True


def test_single_image_does_not_create_meaningless_parent():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="产品图",
                order_index=0,
                image_url="/files/product.png",
                metadata={"vlm_description": "白色背景中的紫色欧包产品。"},
            )
        ],
    )
    canonical = build_canonical_document(parsed, file_type="png", document_title="产品")
    plan = build_canonical_parent_child_plan(canonical, _cfg())

    assert len(plan.parents) == 0
    assert len(plan.children) == 1
    assert plan.children[0].chunk_type == "image"
    assert plan.children[0].parent_index is None


def test_markdown_source_is_preserved_as_canonical_input():
    markdown = "# 标题\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    parsed = ParsedDocument.from_text(markdown)
    canonical = build_canonical_document(parsed, file_type="md", document_title="表格")
    assert canonical.markdown_content == markdown
    assert canonical.metadata["canonical_source"] == "native_markdown"


def test_table_block_is_canonicalized_and_kept_as_table_child():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="实验结果",
                order_index=0,
                metadata={"is_heading": True, "heading_level": 2},
            ),
            ParsedBlock(
                block_type="table",
                text="A | B\n1 | 2\n3 | 4",
                order_index=1,
            ),
        ],
    )
    canonical = build_canonical_document(parsed, file_type="csv", document_title="实验")
    assert "| A | B |" in canonical.markdown_content
    assert "| --- | --- |" in canonical.markdown_content

    plan = build_canonical_parent_child_plan(canonical, _cfg())
    tables = [child for child in plan.children if child.chunk_type == "table"]
    assert len(tables) == 1
    assert tables[0].content.startswith("| A | B |")
    assert tables[0].context_header.endswith("## 实验结果")


def test_multiple_tables_expanded_from_one_source_child_share_a_parent():
    canonical = CanonicalDocument(
        markdown_content=(
            "## 续表 2\n\n"
            "<table><tr><td>成分 1</td></tr></table>\n\n"
            "<table><tr><td>成分 2</td></tr></table>\n\n"
            "<table><tr><td>成分 3</td></tr></table>"
        ),
        metadata={"canonical_source": "ordered_parsed_blocks"},
        source_format="pdf",
    )

    plan = build_canonical_parent_child_plan(canonical, _cfg())

    tables = [child for child in plan.children if child.chunk_type == "table"]
    assert len(tables) == 3
    assert len({table.parent_index for table in tables}) == 1
    assert tables[0].parent_index is not None
    parent = next(
        parent for parent in plan.parents if parent.parent_index == tables[0].parent_index
    )
    assert parent.metadata["synthetic_expanded_source_parent"] is True
    assert parent.metadata["children_count_planned"] == 3
    assert all(
        table.metadata["parent_materialization_rule"]
        == "expanded_source_child_multiple_children"
        for table in tables
    )


def test_large_markdown_table_is_split_without_heading_scatter():
    rows = "\n".join(f"| row{i} | value{i} | detail{i} |" for i in range(80))
    markdown = "## 大表格\n\n| name | value | note |\n|---|---|---|\n" + rows
    canonical = build_canonical_document(ParsedDocument.from_text(markdown), file_type="md")
    plan = build_canonical_parent_child_plan(canonical, _cfg())

    tables = [child for child in plan.children if child.chunk_type == "table"]
    assert len(tables) > 1
    assert all("| name | value | note |" in child.content for child in tables)
    assert not any(is_structural_only(child.content) for child in plan.children)
    assert all("## 大表格" in child.context_header for child in tables)


# ── Standalone root child (replaces 1:1 orphan promotion) ──


def test_standalone_chapter_marked_as_root_child():
    """A single chapter text that has no parent is marked as standalone_root_child
    rather than given a 1:1 synthetic parent."""
    markdown = (
        "## 2 药理作用\n\n茯苓多糖具有免疫调节作用。研究表明，茯苓多糖能显著增强巨噬细胞的吞噬功能。\n\n"
        "## 3 结语与展望\n\n茯苓作为传统中药，应用历史悠久。未来研究应关注其作用机制。"
    )
    canonical = build_canonical_document(ParsedDocument.from_text(markdown), file_type="md")
    plan = build_canonical_parent_child_plan(canonical, _cfg())

    text_children = [c for c in plan.children if c.chunk_type == "text"]
    # Children with no parent must be explicitly marked as standalone
    for child in text_children:
        if child.parent_index is None:
            assert child.metadata.get("standalone_root_child") is True, (
                f"Orphan without standalone_root_child marker: {child.context_header[:80]}"
            )
            assert child.metadata.get("parent_assignment_state") == "standalone_root_child"

    # No 1:1 synthetic parent should exist
    synthetic_11_parents = [
        p for p in plan.parents
        if p.metadata.get("parent_materialization_rule") == "single_orphan_promoted_to_parent"
    ]
    assert len(synthetic_11_parents) == 0, (
        "No 1:1 synthetic parents should exist"
    )


def test_text_child_without_parent_is_standalone_root_not_one_to_one_parent():
    """Orphan resolution does not create 1:1 parents — marks as standalone_root_child."""
    markdown = "## 章节一\n\n正文内容段落。\n\n![图1](asset://image-1)\n\n## 章节二\n\n第二段正文。"
    canonical = build_canonical_document(
        ParsedDocument(
            content=markdown,
            blocks=[
                ParsedBlock(block_type="text", text=markdown),
                ParsedBlock(
                    block_type="image", text="图1",
                    metadata={"visual_role": "page_primary_visual"},
                ),
            ],
        ),
        file_type="md",
    )
    plan = build_canonical_parent_child_plan(canonical, _cfg())

    # Image children should have a valid parent_index
    image_children = [c for c in plan.children if c.chunk_type == "image"]
    for img in image_children:
        assert img.parent_index is not None, (
            f"Image child has no parent: context_header={img.context_header}"
        )

    # Text orphans must be marked as standalone_root_child, not parentless
    for child in plan.children:
        if child.chunk_type == "text" and child.parent_index is None:
            assert child.metadata.get("standalone_root_child"), (
                "Text child without parent and without standalone_root_child marker"
            )

    # No 1:1 parents
    for p in plan.parents:
        if p.metadata.get("children_count_planned") == 1:
            is_visual = (
                p.metadata.get("singleton_primary_visual_parent")
                or p.metadata.get("synthetic_visual_page_parent")
            )
            assert is_visual, (
                f"Non-visual 1:1 parent found: {p.metadata}"
            )


def test_two_chapters_not_merged_under_same_parent():
    """Two independent chapters do not share a parent."""
    markdown = (
        "## 2 药理作用\n\n茯苓多糖具有免疫调节作用。\n\n"
        "## 3 结语与展望\n\n未来研究方向包括机制研究和临床应用。"
    )
    canonical = build_canonical_document(ParsedDocument.from_text(markdown), file_type="md")
    plan = build_canonical_parent_child_plan(canonical, _cfg())

    chapter2_children = [
        c for c in plan.children
        if c.chunk_type == "text" and "2 药理作用" in (c.context_header or "")
    ]
    chapter3_children = [
        c for c in plan.children
        if c.chunk_type == "text" and "3 结语与展望" in (c.context_header or "")
    ]

    if chapter2_children and chapter3_children:
        p2 = {c.parent_index for c in chapter2_children if c.parent_index is not None}
        p3 = {c.parent_index for c in chapter3_children if c.parent_index is not None}
        if p2 and p3:
            assert not p2.intersection(p3), (
                f"Chapters 2 and 3 share parents: {p2 & p3}"
            )


def test_standalone_root_child_has_correct_metadata():
    """Standalone root children carry parent_assignment_state=standalone_root_child."""
    markdown = "## 独立章节\n\n这是独立章节的正文内容。没有其他章节与之合并。"
    canonical = build_canonical_document(ParsedDocument.from_text(markdown), file_type="md")
    plan = build_canonical_parent_child_plan(canonical, _cfg())

    standalones = [
        c for c in plan.children
        if c.metadata.get("standalone_root_child")
    ]

    for child in standalones:
        assert child.metadata.get("parent_assignment_state") == "standalone_root_child"
        # standalone_root_child should NOT have parent_materialized=True
        assert child.metadata.get("parent_materialized") is False

    # Every text child is either parented or standalone_root_child
    for child in plan.children:
        if child.chunk_type == "image":
            continue
        has_parent = child.parent_index is not None
        is_root = child.metadata.get("standalone_root_child")
        assert has_parent or is_root, (
            f"Text child neither parented nor standalone: {child.context_header[:80]}"
        )


# ── Cross-floating-content continuation detection ──


def test_cross_floating_continuation_detected_and_grouped():
    """Test 2: Text split by floating content (image, table, caption, header)
    is detected as a continuation and placed under the same parent."""
    from app.services.chunker.canonical_parent_child import (
        _detect_floating_content_continuations,
    )

    # Build children directly to test continuation detection
    children = [
        CanonicalChildDraft(
            content="通过制作肝纤维化模型进行羧甲基茯苓多糖抗肝纤维",
            chunk_type="text", parent_index=None,
            context_header="## 2 药理作用", start=0, end=24,
            source_child_seq=0, metadata={"chunk_policy_page_no": 1},
        ),
        CanonicalChildDraft(
            content="第 46 卷 第 4 期",
            chunk_type="text", parent_index=None,
            context_header="## 2 药理作用", start=25, end=35,
            source_child_seq=1, metadata={"page_noise": True},
            flags={"page_noise": True},
        ),
        CanonicalChildDraft(
            content="图 1 化学结构式",
            chunk_type="image", parent_index=None,
            context_header="## 2 药理作用", start=36, end=46,
            source_child_seq=2,
        ),
        CanonicalChildDraft(
            content="| 编号 | 名称 |",
            chunk_type="table", parent_index=None,
            context_header="## 2 药理作用", start=47, end=57,
            source_child_seq=3,
        ),
        CanonicalChildDraft(
            content="化的研究，结果显示羧甲基茯苓多糖降低TGF-β表达。",
            chunk_type="text", parent_index=None,
            context_header="## 2 药理作用", start=58, end=80,
            source_child_seq=4, metadata={"chunk_policy_page_no": 1},
        ),
    ]

    detected = _detect_floating_content_continuations(children)

    # Must find exactly one continuation pair
    assert len(detected) == 1, f"Expected 1 continuation, got {len(detected)}"
    desc = detected[0]
    assert desc["child_a_index"] == 0, f"Child A should be index 0, got {desc['child_a_index']}"
    assert desc["child_b_index"] == 4, f"Child B should be index 4, got {desc['child_b_index']}"
    assert "image" in desc["gap_block_types"]
    assert "table" in desc["gap_block_types"]


def test_adjacent_text_without_floating_content_is_not_continuation():
    """Ordinary adjacent chunks stay under the normal splitter contract."""
    from app.services.chunker.canonical_parent_child import (
        _detect_floating_content_continuations,
    )

    children = [
        CanonicalChildDraft(
            content="第一段在切片边界处没有句号",
            chunk_type="text",
            parent_index=None,
            context_header="## 同一章节",
            start=0,
            end=14,
            source_child_seq=0,
        ),
        CanonicalChildDraft(
            content="下一段是独立正文内容。",
            chunk_type="text",
            parent_index=None,
            context_header="## 同一章节",
            start=14,
            end=25,
            source_child_seq=1,
        ),
    ]

    assert _detect_floating_content_continuations(children) == []


def test_floating_content_does_not_bridge_different_sections_or_parents():
    """Continuation recovery fails closed across stable ownership boundaries."""
    from app.services.chunker.canonical_parent_child import (
        _detect_floating_content_continuations,
    )

    def make_children(*, context_b: str, parent_b: int | None):
        return [
            CanonicalChildDraft(
                content="前一章节正文在这里没有结束",
                chunk_type="text",
                parent_index=1,
                context_header="## 第一章节",
                start=0,
                end=14,
                source_child_seq=0,
            ),
            CanonicalChildDraft(
                content="图 1",
                chunk_type="image",
                parent_index=None,
                context_header="## 第一章节",
                start=15,
                end=18,
                source_child_seq=1,
            ),
            CanonicalChildDraft(
                content="后续正文内容。",
                chunk_type="text",
                parent_index=parent_b,
                context_header=context_b,
                start=19,
                end=26,
                source_child_seq=2,
            ),
        ]

    different_sections = make_children(
        context_b="## 第二章节",
        parent_b=None,
    )
    assert _detect_floating_content_continuations(different_sections) == []

    different_parents = make_children(
        context_b="## 第一章节",
        parent_b=2,
    )
    before = [
        (child.content, child.start, child.end, child.parent_index)
        for child in different_parents
    ]
    assert _detect_floating_content_continuations(different_parents) == []
    assert [
        (child.content, child.start, child.end, child.parent_index)
        for child in different_parents
    ] == before


def test_chapter_boundary_not_bridged():
    """Test 3: A new chapter heading prevents continuation bridging."""
    from app.services.chunker.canonical_parent_child import (
        _detect_floating_content_continuations,
    )

    # Child A: text before chapter boundary, ends incomplete
    # Floating: image between them
    # Child B: starts with "## 3" — this is a SECTION START, not a continuation
    children = [
        CanonicalChildDraft(
            content="正文未以句号结束",
            chunk_type="text", parent_index=None,
            context_header="## 2 药理作用", start=0, end=10,
            source_child_seq=0,
        ),
        CanonicalChildDraft(
            content="图 1",
            chunk_type="image", parent_index=None,
            context_header="## 2 药理作用", start=11, end=15,
            source_child_seq=1,
        ),
        CanonicalChildDraft(
            content="## 3 结语与展望",
            chunk_type="text", parent_index=None,
            context_header="## 3 结语与展望", start=16, end=26,
            source_child_seq=2,
        ),
        CanonicalChildDraft(
            content="这是新章节的正文内容。",
            chunk_type="text", parent_index=None,
            context_header="## 3 结语与展望", start=27, end=40,
            source_child_seq=2,
        ),
    ]

    detected = _detect_floating_content_continuations(children)

    # Child A (index 0) should NOT be bridged to any child after "## 3"
    # because "## 3" is a section start (_looks_like_section_start returns True)
    for desc in detected:
        idx_a = desc["child_a_index"]
        idx_b = desc["child_b_index"]
        assert idx_a != 0, (
            f"Child A (index 0) should not be bridged across chapter boundary. "
            f"Got continuation from {idx_a} to {idx_b}"
        )

    # Child B (the "## 3" section heading child at index 2) should be detected
    # as a section start and should NOT be bridged to child at index 3
    assert not any(d["child_a_index"] == 2 for d in detected), (
        "Section heading should not be bridged as continuation source"
    )


def test_complete_sentence_not_bridged():
    """Test 4: A text block ending with a sentence terminator is NOT bridged."""
    from app.services.chunker.canonical_parent_child import (
        _detect_floating_content_continuations,
    )

    children = [
        CanonicalChildDraft(
            content="第一段正文已经完整结束。",
            chunk_type="text", parent_index=None,
            context_header="## 2 药理作用", start=0, end=12,
            source_child_seq=0,
        ),
        CanonicalChildDraft(
            content="图 1",
            chunk_type="image", parent_index=None,
            context_header="## 2 药理作用", start=13, end=17,
            source_child_seq=1,
        ),
        CanonicalChildDraft(
            content="第二段正文是新内容的开始。",
            chunk_type="text", parent_index=None,
            context_header="## 2 药理作用", start=18, end=30,
            source_child_seq=2,
        ),
    ]

    detected = _detect_floating_content_continuations(children)

    # Child A (index 0) ends with "。", which is a sentence terminator.
    # It should NOT be bridged to child at index 2.
    for desc in detected:
        assert desc["child_a_index"] != 0, (
            f"Complete sentence should not be bridged: {desc}"
        )


# ── Continuation detection helpers ──


def test_text_ends_incomplete():
    """_text_ends_incomplete correctly identifies sentences without terminators."""
    from app.services.chunker.canonical_parent_child import _text_ends_incomplete

    assert _text_ends_incomplete("羧甲基茯苓多糖抗肝纤维") is True
    assert _text_ends_incomplete("这是完整的句子。") is False
    assert _text_ends_incomplete("这是完整的句子！") is False
    assert _text_ends_incomplete("这是完整的句子？") is False
    assert _text_ends_incomplete("Ends with period.") is False
    assert _text_ends_incomplete("No terminator") is True
    assert _text_ends_incomplete("") is False


def test_looks_like_section_start():
    """_looks_like_section_start correctly identifies section headings."""
    from app.services.chunker.canonical_parent_child import _looks_like_section_start

    assert _looks_like_section_start("## 3 结语与展望") is True
    assert _looks_like_section_start("3 结语与展望") is True
    assert _looks_like_section_start("化的研究，结果显示") is False
    assert _looks_like_section_start("正文内容") is False
    assert _looks_like_section_start("") is False


# ── Standalone root / noise classification ──


def test_classify_unassigned_page_number_noise():
    """Case 1: Low-quality short text like "20" → skipped_noise, no synthetic parent."""
    from app.services.chunker.canonical_parent_child import (
        _classify_unassigned_child,
    )

    child = CanonicalChildDraft(
        content="20",
        chunk_type="text",
        parent_index=None,
        context_header="",
        start=0,
        end=2,
        source_child_seq=0,
    )
    category = _classify_unassigned_child(child)
    assert category == "skipped_noise", f"Expected skipped_noise, got {category}"


def test_classify_unassigned_whole_page_table_is_standalone_root():
    """Case 2: Whole-page independent table → standalone_root, no 1:1 parent."""
    from app.services.chunker.canonical_parent_child import (
        _classify_unassigned_child,
    )

    table_content = (
        "| INTERNATIONAL SEARCH REPORT Information on patent family members |\n"
        "| --- | --- | --- |\n"
        "| Patent document | Publication date | Patent family member |\n"
    )
    child = CanonicalChildDraft(
        content=table_content,
        chunk_type="table",
        parent_index=None,
        context_header="",
        start=0,
        end=len(table_content),
        source_child_seq=0,
    )
    category = _classify_unassigned_child(child)
    assert category == "standalone_root", f"Expected standalone_root, got {category}"


def test_classify_unassigned_references_section_is_standalone_root():
    """Case 3: Independent References section without parent owner → standalone_root."""
    from app.services.chunker.canonical_parent_child import (
        _classify_unassigned_child,
    )

    content = (
        "## EP 4 517 993 A1\n\n"
        "## REFERENCES CITED IN THE DESCRIPTION\n\n"
        "This list of references cited by the applicant is for the reader's convenience only."
    )
    child = CanonicalChildDraft(
        content=content,
        chunk_type="text",
        parent_index=None,
        context_header="## REFERENCES CITED IN THE DESCRIPTION",
        start=0,
        end=len(content),
        source_child_seq=0,
    )
    category = _classify_unassigned_child(child)
    assert category == "standalone_root", f"Expected standalone_root, got {category}"


def test_standalone_root_child_marked_with_category_in_plan():
    """Case 5: Normal text child with parent → behavior completely unchanged.

    Text children that share a valid parent should have parent_index set,
    standalone_root_child should NOT be set on them.
    """
    markdown = (
        "## 第二章\n\n"
        "这是第一段文字，属于第二章的内容。茯苓多糖具有免疫调节作用。\n\n"
        "这是第二段文字，继续讨论药理机制。研究表明，茯苓多糖能显著增强巨噬细胞的吞噬功能。\n\n"
    )
    canonical = build_canonical_document(ParsedDocument.from_text(markdown), file_type="md")
    plan = build_canonical_parent_child_plan(canonical, _cfg())

    for child in plan.children:
        if child.parent_index is not None:
            # Parented children should NOT be marked as standalone_root_child
            assert child.metadata.get("standalone_root_child") is not True, (
                f"Parented child wrongly marked standalone: {child.content[:80]}"
            )
            assert child.metadata.get("parent_assignment_state") != "standalone_root_child"


def test_true_orphan_detection():
    """Case 6: True orphan detection — parent_index=None, standalone_root_child=False."""
    from app.services.chunker.canonical_parent_child import is_true_orphan

    # A chunk with no parent and no standalone marker → true orphan
    orphan_meta = {
        "chunk_role": "child",
        "standalone_root_child": False,
    }
    assert is_true_orphan(orphan_meta, parent_chunk_id=None, chunk_type="text") is True

    # A chunk with parent → not an orphan
    parented_meta = {"chunk_role": "child"}
    assert is_true_orphan(parented_meta, parent_chunk_id="some-parent-id", chunk_type="text") is False

    # parent_text is never a true orphan
    parent_text_meta = {}
    assert is_true_orphan(parent_text_meta, parent_chunk_id=None, chunk_type="parent_text") is False


def test_standalone_root_detection_not_counted_as_orphan():
    """Case 7: Standalone root → is_standalone_root=True, is_true_orphan=False."""
    from app.services.chunker.canonical_parent_child import (
        is_standalone_root,
        is_true_orphan,
    )

    standalone_meta = {
        "standalone_root_child": True,
        "parent_assignment_state": "standalone_root_child",
        "standalone_root_category": "standalone_root",
    }
    assert is_standalone_root(standalone_meta, parent_chunk_id=None) is True
    assert is_true_orphan(standalone_meta, parent_chunk_id=None, chunk_type="text") is False

    # Also works with parent_assignment_state alone
    state_only_meta = {
        "parent_assignment_state": "standalone_root_child",
    }
    assert is_standalone_root(state_only_meta, parent_chunk_id=None) is True


def test_standalone_root_with_parent_policy_materialize_false():
    """Case 8: parent_policy.materialize=true but child is complete standalone table.

    The materialize flag is a policy capability, not a hard requirement.
    A complete standalone table without text siblings should still be standalone_root.
    """
    from app.services.chunker.canonical_parent_child import (
        _classify_unassigned_child,
    )

    # Simulate a table child with parent_policy.materialize=true in its metadata
    # (which is what _apply_chunk_policy_metadata would set later)
    table_content = (
        "| Item | lon conductor | Insulating structure | Liquid electrolyte |\n"
        "| --- | --- | --- | --- |\n"
        "| Example 1 | PEO-based | Yes | No | 1500 |\n"
    )
    child = CanonicalChildDraft(
        content=table_content,
        chunk_type="table",
        parent_index=None,
        context_header="",
        start=0,
        end=len(table_content),
        source_child_seq=0,
        metadata={
            "parent_policy": {
                "policy_id": "parent-table-v1",
                "strategy": "table",
                "materialize": True,
            },
        },
    )
    category = _classify_unassigned_child(child)
    # The parent_policy.materialize flag does NOT force synthetic parent creation.
    # A standalone table without text siblings can remain standalone_root.
    assert category == "standalone_root", f"Expected standalone_root, got {category}"


def test_image_primary_visual_not_affected_by_text_table_logic():
    """Case 9: Image standalone visual must NOT be affected by text/table logic."""
    from app.services.chunker.canonical_parent_child import (
        _classify_unassigned_child,
        _is_page_primary_visual_asset,
    )

    # Images that are page_primary_visual are skipped by the text/table logic
    # in the standalone orphan resolution loop via _is_page_primary_visual_asset check.
    # This test verifies the guard is present.
    #
    # Since _is_page_primary_visual_asset operates on asset objects,
    # we verify the skip condition through the child metadata path.
    child = CanonicalChildDraft(
        content="FIG. 1 schematic diagram",
        chunk_type="image",
        parent_index=None,
        context_header="## BRIEF DESCRIPTION OF DRAWINGS",
        start=0,
        end=29,
        source_child_seq=0,
        metadata={
            "visual_role": "page_primary_visual",
            "semantic_role": "page_primary_visual",
        },
    )
    # The classification function only handles text/table, so for images
    # it doesn't apply (the loop in build_canonical_parent_child_plan
    # skips images via chunk_type check).
    # This test confirms _classify_unassigned_child handles the edge case gracefully.
    category = _classify_unassigned_child(child)
    # Image content "FIG. 1 schematic diagram" is not page noise and has > 3 chars
    assert category == "standalone_root", f"Expected standalone_root for image-typed child, got {category}"


def test_skipped_noise_preserves_skip_metadata_in_plan():
    """Case 1 (extended): Plan-level metadata for skipped_noise children."""
    markdown = (
        "## 第二章\n\n"
        "正文内容。\n\n"
        "20\n\n"  # page number noise
    )
    canonical = build_canonical_document(ParsedDocument.from_text(markdown), file_type="md")
    plan = build_canonical_parent_child_plan(canonical, _cfg())

    # Find the "20" child
    noise_children = [
        c for c in plan.children
        if c.content.strip() == "20"
    ]
    if noise_children:
        for child in noise_children:
            assert child.metadata.get("standalone_root_child") is True
            assert child.metadata.get("parent_assignment_state") == "standalone_root_child"
            assert child.metadata.get("standalone_root_category") == "skipped_noise"
            assert child.metadata.get("skip_embedding_recommended") is True
            assert child.metadata.get("skip_reason") == "standalone_noise_child"
            assert child.flags.get("skip_embedding_recommended") is True


# ── Phase 3: Mutual Exclusivity & structural_role tests ───────────────────


def test_is_skipped_noise_with_category():
    """Case 1: standalone_root_category=skipped_noise → is_skipped_noise=True."""
    from app.services.chunker.canonical_parent_child import is_skipped_noise

    meta = {
        "standalone_root_child": True,
        "standalone_root_category": "skipped_noise",
        "skip_embedding_recommended": True,
    }
    assert is_skipped_noise(meta) is True


def test_is_skipped_noise_backward_compat():
    """Case 1b: Old data with skip_embedding_recommended + standalone_root_child but no category."""
    from app.services.chunker.canonical_parent_child import is_skipped_noise

    meta = {
        "standalone_root_child": True,
        "skip_embedding_recommended": True,
    }
    assert is_skipped_noise(meta) is True


def test_is_skipped_noise_false_for_standalone_root():
    """skipped_noise must be False for legitimate standalone_root."""
    from app.services.chunker.canonical_parent_child import is_skipped_noise

    meta = {
        "standalone_root_child": True,
        "standalone_root_category": "standalone_root",
    }
    assert is_skipped_noise(meta) is False


def test_is_standalone_root_excludes_skipped_noise():
    """Case 1 (mutual exclusivity): skipped_noise → is_standalone_root=False."""
    from app.services.chunker.canonical_parent_child import (
        is_skipped_noise,
        is_standalone_root,
    )

    meta = {
        "standalone_root_child": True,
        "parent_assignment_state": "standalone_root_child",
        "standalone_root_category": "skipped_noise",
        "skip_embedding_recommended": True,
    }
    assert is_skipped_noise(meta) is True
    assert is_standalone_root(meta, parent_chunk_id=None) is False


def test_is_standalone_root_backward_compat_no_category():
    """Case 2: Old data — standalone_root_child=true, no category → still standalone_root."""
    from app.services.chunker.canonical_parent_child import (
        is_skipped_noise,
        is_standalone_root,
    )

    meta = {
        "standalone_root_child": True,
    }
    assert is_skipped_noise(meta) is False
    assert is_standalone_root(meta, parent_chunk_id=None) is True


def test_is_standalone_root_new_data_with_category():
    """Case 3: New standalone — standalone_root_child=true, category=standalone_root."""
    from app.services.chunker.canonical_parent_child import is_standalone_root

    meta = {
        "standalone_root_child": True,
        "standalone_root_category": "standalone_root",
    }
    assert is_standalone_root(meta, parent_chunk_id=None) is True


def test_is_standalone_root_with_parent_returns_false():
    """standalone_root with actual parent_chunk_id → is_standalone_root=False."""
    from app.services.chunker.canonical_parent_child import is_standalone_root

    meta = {
        "standalone_root_child": True,
        "standalone_root_category": "standalone_root",
    }
    assert is_standalone_root(meta, parent_chunk_id="some-parent-id") is False


def test_true_orphan_no_markers():
    """Case 4: True orphan — role=child, parent=NULL, no standalone/noise markers."""
    from app.services.chunker.canonical_parent_child import is_true_orphan

    meta = {"chunk_role": "child"}
    assert is_true_orphan(meta, parent_chunk_id=None, chunk_type="text") is True


def test_true_orphan_not_triggered_for_standalone():
    """Standalone root must not be classified as true_orphan."""
    from app.services.chunker.canonical_parent_child import (
        is_true_orphan,
    )

    meta = {
        "standalone_root_child": True,
        "standalone_root_category": "standalone_root",
    }
    assert is_true_orphan(meta, parent_chunk_id=None, chunk_type="text") is False


def test_true_orphan_not_triggered_for_skipped_noise():
    """Skipped noise must not be classified as true_orphan."""
    from app.services.chunker.canonical_parent_child import (
        is_true_orphan,
    )

    meta = {
        "standalone_root_child": True,
        "standalone_root_category": "skipped_noise",
        "skip_embedding_recommended": True,
    }
    assert is_true_orphan(meta, parent_chunk_id=None, chunk_type="text") is False


def test_structural_role_for_parent():
    """Case 6: parent_text → structural_role=parent."""
    from app.services.chunker.canonical_parent_child import structural_role_for

    assert structural_role_for({}, chunk_type="parent_text") == "parent"


def test_structural_role_for_child():
    """Case 5: Normal child with parent → structural_role=child."""
    from app.services.chunker.canonical_parent_child import structural_role_for

    assert structural_role_for({}, parent_chunk_id="parent-123", chunk_type="text") == "child"


def test_structural_role_for_standalone_root():
    """Standalone root → structural_role=standalone_root."""
    from app.services.chunker.canonical_parent_child import structural_role_for

    meta = {
        "standalone_root_child": True,
        "standalone_root_category": "standalone_root",
    }
    assert structural_role_for(meta, parent_chunk_id=None, chunk_type="text") == "standalone_root"


def test_structural_role_for_skipped_noise():
    """Skipped noise → structural_role=skipped_noise."""
    from app.services.chunker.canonical_parent_child import structural_role_for

    meta = {
        "standalone_root_child": True,
        "standalone_root_category": "skipped_noise",
        "skip_embedding_recommended": True,
    }
    assert structural_role_for(meta, parent_chunk_id=None, chunk_type="text") == "skipped_noise"


def test_structural_role_for_true_orphan():
    """Case 11: True orphan → structural_role=true_orphan."""
    from app.services.chunker.canonical_parent_child import structural_role_for

    meta = {"chunk_role": "child"}
    assert structural_role_for(meta, parent_chunk_id=None, chunk_type="text") == "true_orphan"


def test_structural_role_for_standalone_root_backward_compat():
    """Old data without category → structural_role=standalone_root (backward compat)."""
    from app.services.chunker.canonical_parent_child import structural_role_for

    meta = {
        "standalone_root_child": True,
    }
    assert structural_role_for(meta, parent_chunk_id=None, chunk_type="text") == "standalone_root"


def test_four_state_mutual_exclusivity():
    """Case 7, 8: Four states are mutually exclusive and cover all non-parent chunks."""
    from app.services.chunker.canonical_parent_child import (
        is_skipped_noise,
        is_standalone_root,
        is_true_orphan,
    )

    test_cases = [
        # (metadata, parent_chunk_id, chunk_type, expected_standalone, expected_noise, expected_orphan)
        # parented child
        ({"chunk_role": "child"}, "parent-1", "text", False, False, False),
        # standalone root (new data)
        ({"standalone_root_child": True, "standalone_root_category": "standalone_root"}, None, "text", True, False, False),
        # standalone root (old data, backward compat)
        ({"standalone_root_child": True}, None, "text", True, False, False),
        # skipped noise (new data)
        ({"standalone_root_child": True, "standalone_root_category": "skipped_noise", "skip_embedding_recommended": True}, None, "text", False, True, False),
        # skipped noise (old data, backward compat)
        ({"standalone_root_child": True, "skip_embedding_recommended": True}, None, "text", False, True, False),
        # true orphan
        ({"chunk_role": "child"}, None, "text", False, False, True),
        # parent_text
        ({}, None, "parent_text", False, False, False),
    ]

    for i, (meta, parent_id, ctype, exp_standalone, exp_noise, exp_orphan) in enumerate(test_cases):
        standalone = is_standalone_root(meta, parent_chunk_id=parent_id)
        noise = is_skipped_noise(meta)
        orphan = is_true_orphan(meta, parent_chunk_id=parent_id, chunk_type=ctype)

        assert standalone == exp_standalone, (
            f"Case {i}: expected is_standalone_root={exp_standalone}, got {standalone}"
        )
        assert noise == exp_noise, (
            f"Case {i}: expected is_skipped_noise={exp_noise}, got {noise}"
        )
        assert orphan == exp_orphan, (
            f"Case {i}: expected is_true_orphan={exp_orphan}, got {orphan}"
        )

        # Mutual exclusivity: at most one can be True for non-parent chunks
        if ctype != "parent_text":
            true_count = sum([standalone, noise, orphan])
            assert true_count <= 1, (
                f"Case {i}: states are not mutually exclusive — "
                f"standalone={standalone}, noise={noise}, orphan={orphan}"
            )
