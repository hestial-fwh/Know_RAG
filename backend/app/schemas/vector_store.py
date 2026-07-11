from pydantic import BaseModel, Field


class VectorStoreConfig(BaseModel):
    provider: str = "postgres"
    config: dict = Field(default_factory=dict)
    is_active: bool = True
