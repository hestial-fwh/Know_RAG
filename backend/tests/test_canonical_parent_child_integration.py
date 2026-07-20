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
