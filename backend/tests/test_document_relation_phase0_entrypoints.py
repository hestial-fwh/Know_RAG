from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.chunk_service import rebuild_document_chunks
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.mineru_adapter import _content_list_to_blocks, _find_content_list
from app.services.document_parser.types import ParsedDocument
from app.services.retrieval_service import SearchOptions, hybrid_search
from scripts.document_relation_dataset.generator import SCENARIOS, generate_dataset


def _parsed_generated_document(tmp_path):
    manifest = generate_dataset(tmp_path, pages_per_type=len(SCENARIOS), seed=31)
    document = manifest["documents"][0]
    import json

    payload = json.loads((tmp_path / document["raw_response_path"]).read_text(encoding="utf-8"))
    content_list = _find_content_list(payload)
    for item in content_list:
        if item.get("type") == "image":
            item.pop("img_path", None)
    blocks = _content_list_to_blocks(
        content_list,
        {},
        knowledge_base_id="phase0-kb",
        document_id="phase0-doc",
    )
    return ParsedDocument(
        content="\n\n".join(block.index_text() for block in blocks),
        blocks=blocks,
        metadata={"parser": "mineru", "source": "phase0_generated_entrypoint"},
    )


def test_generated_dataset_reaches_rebuild_document_chunks_entrypoint(tmp_path):
    parsed = _parsed_generated_document(tmp_path)
    document = SimpleNamespace(
        id="phase0-doc",
        knowledge_base_id="phase0-kb",
        title="Generated Phase 0",
        file_name="generated.pdf",
        file_type="pdf",
    )
    db = MagicMock()
    config = ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
        separators=["\n\n", "\n", ". "],
    )

    with (
        patch("app.services.chunk_service._replace_canonical_artifact") as replace_artifact,
        patch("app.services.chunk_service.update_knowledge_base_chunk_count"),
    ):
        inserted = rebuild_document_chunks(db, document, parsed, cfg_override=config)

    assert inserted > 0
    assert db.add.call_count == inserted
    db.flush.assert_called_once()
    replace_artifact.assert_called_once()


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def all(self):
        return self.rows


class _HybridDB:
    def __init__(self, rows):
        self.rows = rows
        self.committed = False

    def get(self, model, item_id):
        return SimpleNamespace(id=item_id)

    def query(self, model):
        return _Query(self.rows)

    def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_generated_relation_metadata_reaches_hybrid_search_entrypoint():
    image = SimpleNamespace(
        id="image-a",
        content="generated image evidence",
        chunk_type="image",
        document_id="doc-a",
        knowledge_base_id="kb-a",
        metadata_json={},
        image_info={},
        parent_chunk_id=None,
        context_header="",
        start_at=100,
        end_at=120,
        is_enabled=True,
    )
    direct = {
        "id": "text-a",
        "content": "See Figure 1",
        "chunk_type": "text",
        "document_id": "doc-a",
        "knowledge_base_id": "kb-a",
        "metadata_json": {"figure_references": [{"image_chunk_ids": ["image-a"]}]},
        "score": 1.0,
    }
    options = SearchOptions(
        query="Figure 1",
        top_k=5,
        vector_threshold=0.0,
        keyword_threshold=0.0,
        use_vector=True,
        use_keyword=False,
        include_content=True,
        include_context=False,
    )
    db = _HybridDB([image])

    with (
        patch("app.services.retrieval_service.normalize_options", return_value=(options, None)),
        patch("app.services.retrieval_service.vector_search", return_value=([direct], "")),
        patch("app.services.retrieval_service.merge_results", return_value=[direct]),
        patch(
            "app.services.retrieval_service.settings.document_relation_retrieval_expansion_enabled",
            True,
        ),
        patch(
            "app.services.retrieval_service.get_embedding_runtime",
            return_value=SimpleNamespace(provider="phase0", model_name="none"),
        ),
        patch("app.services.retrieval_service.record_audit"),
    ):
        response = await hybrid_search(db, None, "kb-a", {"query": "Figure 1"})

    assert response["success"] is True
    assert [item["id"] for item in response["data"]["results"]] == ["text-a", "image-a"]
    assert response["data"]["results"][1]["retrieval_expansion"] is True
    assert db.committed is True


@pytest.mark.asyncio
async def test_hybrid_search_relation_expansion_flag_off_preserves_direct_results():
    direct = {
        "id": "text-a",
        "content": "See Figure 1",
        "chunk_type": "text",
        "document_id": "doc-a",
        "knowledge_base_id": "kb-a",
        "metadata_json": {"figure_references": [{"image_chunk_ids": ["image-a"]}]},
        "score": 1.0,
    }
    options = SearchOptions(
        query="Figure 1",
        top_k=5,
        vector_threshold=0.0,
        keyword_threshold=0.0,
        use_vector=True,
        use_keyword=False,
        include_content=True,
        include_context=False,
    )
    db = _HybridDB([])

    with (
        patch("app.services.retrieval_service.normalize_options", return_value=(options, None)),
        patch("app.services.retrieval_service.vector_search", return_value=([direct], "")),
        patch("app.services.retrieval_service.merge_results", return_value=[direct]),
        patch(
            "app.services.retrieval_service.settings.document_relation_retrieval_expansion_enabled",
            False,
        ),
        patch(
            "app.services.retrieval_service.get_embedding_runtime",
            return_value=SimpleNamespace(provider="phase1", model_name="none"),
        ),
        patch("app.services.retrieval_service.record_audit"),
    ):
        response = await hybrid_search(db, None, "kb-a", {"query": "Figure 1"})

    assert response["data"]["results"] == [direct]
