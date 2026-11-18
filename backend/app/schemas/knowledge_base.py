from pydantic import BaseModel, Field

from app.schemas.retrieval_strategy import RetrievalProfile


class KnowledgeBaseCreate(BaseModel):
    # 新建库必须显式提供 family_scope：a 关系/社区，b 事件。
    retrieval_profile: RetrievalProfile | None = None
    name: str
    description: str = ""
    embedding_model_id: str = ""
    summary_model_id: str = ""
    chunking_config: dict = Field(default_factory=dict)


class KnowledgeBaseUpdate(BaseModel):
    retrieval_profile: RetrievalProfile | None = None
    name: str | None = None
    description: str | None = None
    embedding_model_id: str | None = None
    summary_model_id: str | None = None
