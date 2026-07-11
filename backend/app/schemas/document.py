from pydantic import BaseModel, Field


class DocumentView(BaseModel):
    id: str
    document_id: str | None = None
    knowledge_id: str | None = None
    knowledge_base_id: str
    title: str
    file_name: str
    file_type: str
    source: str = "file"
    channel: str = "local"
    metadata: dict = Field(default_factory=dict)
    metadata_json: dict = Field(default_factory=dict)
    parse_statistics: dict = Field(default_factory=dict)
    enable_status: str = "enabled"
    parse_status: str
    error_message: str = ""
    processed_at: str | None = None
    chunk_count: int = 0


class DocumentStatus(BaseModel):
    id: str
    parse_status: str
    error_message: str = ""
    chunk_count: int = 0
