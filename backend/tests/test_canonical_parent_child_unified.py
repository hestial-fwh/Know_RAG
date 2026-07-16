from app.services.chunker.canonical_parent_child import (
    build_canonical_parent_child_plan,
    is_structural_only,
)
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import build_canonical_document
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
