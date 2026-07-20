from collections import Counter
from types import SimpleNamespace

from app.services.chunk_service import (
    _rebuild_canonical_weknora_parent_child,
    _replace_canonical_artifact,
)
from app.services.document_parser.types import ParsedBlock, ParsedDocument


def _cfg():
    return SimpleNamespace(
        chunk_size=512,
        chunk_overlap=80,
        strategy="auto",
        separators=["\n\n", "\n", "。"],
        parent_chunk_size=1200,
        child_chunk_size=384,
        child_chunk_overlap=64,
        token_limit=0,
        languages=[],
        enable_parent_child=True,
    )


def _doc(file_type="pdf"):
    return SimpleNamespace(
        id=f"doc-{file_type}",
        knowledge_base_id="kb-test",
        title="统一切分测试",
        file_type=file_type,
    )


def test_database_chunk_drafts_use_canonical_path_and_image_semantics():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="架构说明",
                order_index=0,
                metadata={"is_heading": True, "heading_level": 2},
            ),
            ParsedBlock(block_type="text", text="前置正文。" * 60, order_index=1),
            ParsedBlock(
                block_type="image",
                text="图 1 架构",
                order_index=2,
                image_url="/files/arch.png",
                metadata={
                    "caption": "图 1 架构",
                    "vlm_description": "前端连接后端，后端连接数据库。",
                    "ocr_text": "Frontend Backend Database",
                },
            ),
            ParsedBlock(block_type="text", text="后置正文。" * 60, order_index=3),
        ],
    )

    chunks, count = _rebuild_canonical_weknora_parent_child(_doc("pdf"), parsed, "pdf", _cfg())
    assert count == len(chunks)
    assert any(chunk.chunk_type == "image" for chunk in chunks)
    image = next(chunk for chunk in chunks if chunk.chunk_type == "image")
    assert "图片描述：前端连接后端" in image.content
    assert "图片文字：Frontend Backend Database" in image.content
    assert image.image_info["image_url"] == "/files/arch.png"
    assert image.metadata_json["parser"] == "canonical_weknora_parent_child"

    counts = Counter(
        chunk.parent_chunk_id
        for chunk in chunks
        if chunk.chunk_type != "parent_text" and chunk.parent_chunk_id
    )
    for parent in [chunk for chunk in chunks if chunk.chunk_type == "parent_text"]:
        assert counts[parent.id] >= 2


def test_markdown_directory_heading_is_not_a_standalone_database_chunk():
    body = "\n".join(f"backend/path/{i}/file.py # description {i}" for i in range(50))
    markdown = f"---\n\n## 3. 目录结构\n\n```text\n{body}\n```\n"
    chunks, _ = _rebuild_canonical_weknora_parent_child(
        _doc("md"),
        ParsedDocument.from_text(markdown),
        "md",
        _cfg(),
    )
    children = [chunk for chunk in chunks if chunk.chunk_type != "parent_text"]
    assert len(children) > 1
    assert not any(chunk.content.strip() == "## 3. 目录结构" for chunk in children)
    assert not any(chunk.content.strip() in {"---", "---\n\n## 3. 目录结构"} for chunk in children)
    assert all(len(chunk.content) < 600 for chunk in children)


def test_p25_builds_durable_canonical_provenance_artifact_and_chunk_refs():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="如图1所示，系统完成初始化。",
                order_index=0,
                page_no=2,
                bbox=(10, 20, 300, 60),
                metadata={"parser": "mineru"},
            ),
            ParsedBlock(
                block_type="image",
                text="图1 系统结构",
                order_index=1,
                page_no=2,
                bbox=(10, 80, 300, 260),
                image_base64="do-not-persist-this-payload",
                metadata={
                    "parser": "mineru",
                    "caption": "图1 系统结构",
                    "nested": {"image_base64": "also-do-not-persist"},
                },
            ),
        ],
    )
    artifact = {}

    chunks, _ = _rebuild_canonical_weknora_parent_child(
        _doc("pdf"),
        parsed,
        "pdf",
        _cfg(),
        artifact_out=artifact,
    )

    assert artifact["algorithm_version"] == "canonical-provenance-p2.5-v1"
    assert len(artifact["canonical_hash"]) == 64
    assert artifact["provenance_json"]["coordinate_space"] == (
        "normalized_canonical_markdown"
    )
    nodes = artifact["provenance_json"]["nodes"]
    assert nodes[0]["page_no"] == 2
    assert nodes[0]["bbox"] == [10.0, 20.0, 300.0, 60.0]
    assert nodes[0]["normalized_start"] is not None
    assert artifact["figure_links_json"]["links"][0]["status"] == "bound"
    durable_asset = artifact["assets_json"]["items"][0]
    assert durable_asset["binary_payload_persisted"] is False
    assert "image_base64" not in durable_asset
    assert "image_base64" not in durable_asset["metadata"]["nested"]
    assert all(chunk.metadata_json.get("source_node_ids") for chunk in chunks)
    assert all(
        chunk.metadata_json["canonical_parse_revision"] == artifact["parse_revision"]
        for chunk in chunks
    )


def test_p25_artifact_upsert_uses_caller_session_without_committing():
    class FakeSession:
        def __init__(self):
            self.added = []

        def get(self, _model, _document_id):
            return None

        def add(self, value):
            self.added.append(value)

    db = FakeSession()
    payload = {
        "parse_revision": "revision-1",
        "schema_version": 1,
        "algorithm_version": "canonical-provenance-p2.5-v1",
        "source_format": "pdf",
        "canonical_hash": "a" * 64,
        "canonical_markdown": "正文",
        "provenance_json": {"nodes": []},
        "assets_json": {"items": []},
        "figure_links_json": {"links": []},
        "stats_json": {"asset_count": 0},
    }

    _replace_canonical_artifact(db, _doc("pdf"), payload)

    assert len(db.added) == 1
    artifact = db.added[0]
    assert artifact.document_id == "doc-pdf"
    assert artifact.parse_revision == "revision-1"
    assert artifact.canonical_markdown == "正文"


def test_p3_precise_source_ranges_for_image_children():
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="架构说明",
                order_index=0,
                metadata={"is_heading": True, "heading_level": 2},
            ),
            ParsedBlock(block_type="text", text="前置正文。" * 60, order_index=1),
            ParsedBlock(
                block_type="image",
                text="图1 架构",
                order_index=2,
                image_url="/files/arch.png",
                metadata={
                    "caption": "图1 架构",
                    "vlm_description": "前端连接后端",
                    "ocr_text": "Frontend Backend",
                },
            ),
            ParsedBlock(block_type="text", text="后置正文。" * 60, order_index=3),
        ],
    )

    chunks, _ = _rebuild_canonical_weknora_parent_child(_doc("pdf"), parsed, "pdf", _cfg())

    image_chunks = [chunk for chunk in chunks if chunk.chunk_type == "image"]
    text_chunks = [chunk for chunk in chunks if chunk.chunk_type != "image" and chunk.chunk_type != "parent_text"]

    assert len(image_chunks) == 1
    img = image_chunks[0]

    # Image child range must be precise: exact marker position, NOT full source child range
    assert img.start_at is not None and img.end_at is not None, "Image child must have distinct start/end"
    assert img.start_at != img.end_at, "Image child must have distinct start/end"
    img_range_len = img.end_at - img.start_at
    # An asset://marker is roughly 20-40 chars, NOT the 300+ chars of the full source
    assert img_range_len < 100, f"Image range should be small (marker only), got {img_range_len}"

    # Text children must have non-overlapping precise ranges
    ranges = [(chunk.start_at, chunk.end_at) for chunk in text_chunks if chunk.start_at is not None]
    assert len(ranges) >= 2, "Should have at least 2 text children with ranges"
    for i in range(len(ranges)):
        for j in range(i + 1, len(ranges)):
            s1, e1 = ranges[i]
            s2, e2 = ranges[j]
            # Ranges should not be identical (old behavior had same range for all)
            assert not (s1 == s2 and e1 == e2), f"Text children should have distinct ranges, got identical [{s1}, {e1}]"


def test_p3_source_ranges_without_assets_are_precise():
    parsed = ParsedDocument(content="", blocks=[
        ParsedBlock(block_type="text", text="段落一。" * 40, order_index=0),
        ParsedBlock(block_type="text", text="段落二。" * 40, order_index=1),
    ])

    chunks, _ = _rebuild_canonical_weknora_parent_child(_doc("pdf"), parsed, "pdf", _cfg())

    text_chunks = [chunk for chunk in chunks if chunk.chunk_type != "parent_text"]
    ranges = [(chunk.start_at, chunk.end_at) for chunk in text_chunks if chunk.start_at is not None]

    for s, e in ranges:
        assert s >= 0, f"start must be >= 0, got {s}"
        assert e > s, f"end must be > start, got start={s}, end={e}"
        assert e <= 3000, f"range must be within reasonable bounds, got end={e}"


def test_p3_table_child_has_segment_precise_range():
    markdown = "## 表格示例\n\n前置文本。\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n后置文本。\n"
    parsed = ParsedDocument.from_text(markdown)

    chunks, _ = _rebuild_canonical_weknora_parent_child(_doc("md"), parsed, "md", _cfg())

    table_chunks = [chunk for chunk in chunks if chunk.chunk_type == "table"]
    for tc in table_chunks:
        assert tc.start_at < tc.end_at, "Table child range must be valid"
        # Table range should be less than the full document length
        assert (tc.end_at - tc.start_at) < 500, f"Table range should be precise, got {tc.end_at - tc.start_at} chars"

def test_p4_figure_bindings_materialize_on_chunk_metadata():
    """P4: Bound figure links produce figure_references + image_binding on chunks."""
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="如图1所示，系统完成初始化。",
                order_index=0,
                page_no=2,
                bbox=(10, 20, 300, 60),
                metadata={"parser": "mineru"},
            ),
            ParsedBlock(
                block_type="image",
                text="图1 系统结构",
                order_index=1,
                page_no=2,
                bbox=(10, 80, 300, 260),
                metadata={
                    "parser": "mineru",
                    "caption": "图1 系统结构",
                },
            ),
        ],
    )

    chunks, _ = _rebuild_canonical_weknora_parent_child(_doc("pdf"), parsed, "pdf", _cfg())

    text_chunks = [c for c in chunks if c.chunk_type == "text" and c.chunk_type != "parent_text"]
    image_chunks = [c for c in chunks if c.chunk_type == "image"]

    assert len(text_chunks) >= 1, "Should have at least one text chunk"
    assert len(image_chunks) == 1, "Should have exactly one image chunk"

    img = image_chunks[0]
    img_binding = (img.metadata_json or {}).get("image_binding")
    assert img_binding is not None, f"Image chunk should have image_binding, got metadata={img.metadata_json}"
    assert img_binding["figure_id"] == "figure:1"
    assert img_binding["version"] == 1
    assert "reference_chunk_ids" in img_binding
    assert len(img_binding["reference_chunk_ids"]) >= 1

    # At least one text chunk has figure_references pointing to the image
    found = False
    for tc in text_chunks:
        refs = (tc.metadata_json or {}).get("figure_references", [])
        for ref in refs:
            if ref["figure_id"] == "figure:1":
                found = True
                assert img.id in ref["image_chunk_ids"], (
                    f"image_chunk_ids should contain {img.id}, got {ref['image_chunk_ids']}"
                )
    assert found, "No text chunk has figure_references pointing to figure:1"
