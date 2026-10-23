from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pgvector")

from app.services.chunk_service import (
    _rebuild_canonical_weknora_parent_child,
    rebuild_document_chunks,
)
from app.services.document_parser.block_reflow import reflow_parsed_document
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.ragflow_format_adapter import (
    parse_binary_fallback,
    parse_markdown,
)
from app.services.document_parser.types import ParsedDocument
from app.services.document_service import mark_document_failed
from app.services.parser_service import _parse_with_ragflow


def _cfg(*, enable_parent_child: bool = False):
    return SimpleNamespace(
        chunk_size=512,
        chunk_overlap=80,
        strategy="auto",
        separators=[],
        enable_parent_child=enable_parent_child,
        parent_chunk_size=4096,
        child_chunk_size=160,
        child_chunk_overlap=24,
        token_limit=0,
        languages=[],
    )


def _document(file_type: str = "md"):
    return SimpleNamespace(
        id="doc-pipeline-safety",
        knowledge_base_id="kb-pipeline-safety",
        title="Pipeline safety",
        file_name=f"pipeline.{file_type}",
        file_type=file_type,
    )


def test_markdown_source_survives_parse_reflow_and_canonicalization_exactly(tmp_path: Path):
    source = (
        "# Title\n\n\n"
        "Paragraph one.\n\n\n\n"
        "```python\nprint('a|b')\n```\n\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |"
    )
    path = tmp_path / "exact.md"
    path.write_text(source, encoding="utf-8", newline="")

    parsed = parse_markdown(path)
    reflowed = reflow_parsed_document(parsed)
    canonical = build_canonical_document(reflowed, file_type="md")

    assert parsed.source_content == source
    assert reflowed.source_content == source
    assert canonical.markdown_content == source
    assert canonical.metadata["canonical_source"] == "native_markdown"


def test_attachment_fallback_is_explicitly_non_indexable(tmp_path: Path):
    path = tmp_path / "unsupported.bin"
    path.write_bytes(b"not parsed")

    parsed = parse_binary_fallback(path, parser="unsupported", reason="unsupported format")

    assert parsed.metadata["fallback_used"] is True
    assert parsed.metadata["content_available"] is False
    assert parsed.metadata["indexable"] is False
    assert parsed.blocks[0].metadata["skip_embedding_recommended"] is True


def test_supported_parser_runtime_failure_is_not_converted_to_placeholder(tmp_path: Path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"broken")
    document = _document("pdf")
    document.metadata_json = {"parse_options": {"parser_engine": "ragflow"}}

    with (
        patch(
            "app.services.parser_service.parse_document_with_ragflow",
            side_effect=RuntimeError("decoder crashed"),
        ),
        pytest.raises(ValueError, match="解析器执行失败"),
    ):
        _parse_with_ragflow(path, document, "pdf")


def test_mark_document_failed_preserves_last_known_good_chunks():
    document = _document("pdf")
    document.metadata_json = {}
    document.parse_status = "parsing"
    document.error_message = ""
    db = MagicMock()
    db.get.return_value = document
    db.scalar.return_value = 3

    with patch("app.services.document_service.update_knowledge_base_chunk_count"):
        mark_document_failed(db, document.id, "parser failed")

    db.query.assert_not_called()
    assert document.parse_status == "failed"
    assert document.metadata_json["previous_chunks_preserved"] is True
    assert document.metadata_json["chunk_count"] == 3
    db.commit.assert_called_once()


def test_empty_rebuild_does_not_touch_existing_database_rows():
    db = MagicMock()
    parsed = ParsedDocument(content="", blocks=[])

    with (
        patch("app.services.chunk_service.runtime_config_from_db", return_value=_cfg()),
        patch(
            "app.services.chunk_service._rebuild_canonical_weknora_parent_child",
            return_value=([], 0),
        ),
        pytest.raises(ValueError, match="切分结果为空"),
    ):
        rebuild_document_chunks(db, _document(), parsed)

    db.query.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_canonical_pipeline_is_used_without_materializing_parents_by_default():
    source = "# Title\n\n" + ("First semantic paragraph. " * 40)
    parsed = ParsedDocument.from_text(source)
    parsed.source_content = source

    chunks, inserted = _rebuild_canonical_weknora_parent_child(
        _document(),
        parsed,
        "md",
        _cfg(enable_parent_child=False),
    )

    assert inserted == len(chunks) > 0
    assert not any(chunk.chunk_type == "parent_text" for chunk in chunks)
    assert all(chunk.parent_chunk_id is None for chunk in chunks)
    assert all(
        chunk.metadata_json.get("coordinate_space") == "normalized_canonical_markdown"
        for chunk in chunks
    )


def test_flat_canonical_pipeline_uses_flat_chunk_size_and_overlap():
    from app.services.chunker.canonical_parent_child import (
        build_canonical_parent_child_plan as real_plan_builder,
    )

    source = "# Title\n\n" + ("Config-sensitive paragraph. " * 80)
    parsed = ParsedDocument.from_text(source)
    cfg = _cfg(enable_parent_child=False)
    cfg.chunk_size = 333
    cfg.chunk_overlap = 17
    cfg.child_chunk_size = 111
    cfg.child_chunk_overlap = 9
    captured = {}

    def capture_plan(canonical, runtime_cfg, **kwargs):
        captured["cfg"] = runtime_cfg
        return real_plan_builder(canonical, runtime_cfg, **kwargs)

    with patch(
        "app.services.chunk_service.build_canonical_parent_child_plan",
        side_effect=capture_plan,
    ):
        _rebuild_canonical_weknora_parent_child(_document(), parsed, "md", cfg)

    runtime_cfg = captured["cfg"]
    assert runtime_cfg.child_chunk_size == 333
    assert runtime_cfg.child_chunk_overlap == 17


def test_materialization_fallback_clears_non_matching_canonical_artifact():
    parsed = ParsedDocument.from_text("Fallback body with enough semantic content to retain.")
    artifact = {}

    with patch(
        "app.services.chunker.canonical_parent_child._materialize_source_child",
        return_value=[],
    ):
        chunks, inserted = _rebuild_canonical_weknora_parent_child(
            _document(),
            parsed,
            "txt",
            _cfg(),
            artifact_out=artifact,
        )

    assert inserted == len(chunks) > 0
    assert artifact == {}
    assert all(
        chunk.metadata_json.get("fallback_reason")
        == "canonical_weknora_produced_zero_chunks"
        for chunk in chunks
    )


def test_keyword_sync_indexes_new_chunks_before_stale_cleanup():
    from app.services import keyword_index_service

    chunk = SimpleNamespace(
        id="new-chunk",
        status="ready",
        is_enabled=True,
        chunk_type="text",
        # 本测试验证索引先写后清；正文需通过现有的低信息文本准入规则。
        content="searchable committed content with enough detail for keyword indexing",
        flags={},
        metadata_json={},
    )
    query = MagicMock()
    query.filter.return_value.order_by.return_value.all.return_value = [chunk]
    db = MagicMock()
    db.query.return_value = query
    document = _document()
    response = SimpleNamespace(status_code=200, text="", json=lambda: {"deleted": 1})
    events: list[str] = []

    with (
        patch.object(keyword_index_service, "keyword_search_enabled", return_value=True),
        patch.object(
            keyword_index_service,
            "index_document_chunks",
            side_effect=lambda *args, **kwargs: events.append("index") or 1,
        ),
        patch.object(keyword_index_service, "ensure_index"),
        patch.object(
            keyword_index_service,
            "_request",
            side_effect=lambda *args, **kwargs: events.append("cleanup") or response,
        ) as request,
    ):
        assert keyword_index_service.sync_document_index(db, document) == 1

    assert events == ["index", "cleanup"]
    body = request.call_args.kwargs["json_body"]
    assert body["query"]["bool"]["must_not"] == [{"ids": {"values": ["new-chunk"]}}]


def test_compatibility_rebuild_forwards_explicit_config_and_file_type():
    from app.services.chunking.rebuild_service import ChunkRebuildService

    cfg = _cfg(enable_parent_child=True)
    db = MagicMock()
    document = _document("pdf")
    parsed = ParsedDocument.from_text("content")

    with patch(
        "app.services.chunk_service.rebuild_document_chunks",
        return_value=2,
    ) as rebuild:
        result = ChunkRebuildService().rebuild_document_chunks(
            db=db,
            document=document,
            parsed_document=parsed,
            file_type="md",
            cfg=cfg,
        )

    assert result == 2
    rebuild.assert_called_once_with(
        db,
        document,
        parsed,
        cfg_override=cfg,
        file_type_override="md",
    )
