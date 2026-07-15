from datetime import UTC, datetime

from app.db.models import Chunk, ChunkEmbedding
from app.services.chunk_service import chunk_to_dict


def test_chunk_api_never_returns_full_embedding_vector():
    now = datetime.now(UTC)
    chunk = Chunk(
        id="chunk-1",
        document_id="document-1",
        knowledge_id="document-1",
        knowledge_base_id="kb-1",
        content="可检索的测试正文",
        chunk_index=0,
        token_count=8,
        is_enabled=True,
        status="ready",
        metadata_json={},
        chunk_type="text",
        image_info={},
        relation_chunks=[],
        indirect_relation_chunks=[],
        context_header="",
        flags={},
        created_at=now,
        updated_at=now,
    )
    chunk.embedding = ChunkEmbedding(
        id="embedding-1",
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        knowledge_id=chunk.knowledge_id,
        knowledge_base_id=chunk.knowledge_base_id,
        source_id=chunk.id,
        source_type="chunk",
        content=chunk.content,
        is_enabled=True,
        embedding=[0.25] * 1024,
        model_name="test-model",
        dimension=1024,
        created_at=now,
        updated_at=now,
    )

    payload = chunk_to_dict(chunk, include_content=True)
    index_info = payload["embedding_index_info"]

    assert "vector" not in index_info
    assert index_info["vector_preview"] == [0.25] * 16
    assert index_info["vector_size"] == 1024
