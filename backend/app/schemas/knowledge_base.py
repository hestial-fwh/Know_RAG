from pydantic import BaseModel, Field


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: str = ""
    embedding_model_id: str = ""
    summary_model_id: str = ""
    chunking_config: dict = Field(default_factory=dict)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    embedding_model_id: str | None = None
    summary_model_id: str | None = None
