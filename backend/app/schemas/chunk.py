from pydantic import BaseModel, Field


class ChunkView(BaseModel):
    id: str
    chunk_id: str | None = None
    document_id: str
    knowledge_id: str | None = None
    knowledge_base_id: str
    content: str = ""
    preview: str = ""
    content_preview: str = ""
    chunk_index: int
    seq: int | None = None
    token_count: int
    is_enabled: bool = True
    status: str = "ready"
    start_at: int | None = None
    end_at: int | None = None
    pre_chunk_id: str | None = None
    next_chunk_id: str | None = None
    chunk_type: str = "text"
    parent_chunk_id: str | None = None
    content_hash: str = ""
    image_info: dict = Field(default_factory=dict)
    relation_chunks: list = Field(default_factory=list)
    indirect_relation_chunks: list = Field(default_factory=list)
    context_header: str = ""
    flags: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    metadata_json: dict = Field(default_factory=dict)
