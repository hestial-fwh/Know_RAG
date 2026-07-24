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
