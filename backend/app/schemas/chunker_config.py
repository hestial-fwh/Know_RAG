from pydantic import BaseModel, Field


class ChunkerConfigPayload(BaseModel):
    strategy: str = Field("auto")
    chunk_size: int = Field(512, ge=100, le=10000)
    chunk_overlap: int = Field(80, ge=0, le=5000)
    separators: list[str] = Field(default_factory=list)
    enable_parent_child: bool = False
    parent_chunk_size: int = Field(4096, ge=512, le=50000)
    child_chunk_size: int = Field(384, ge=100, le=10000)
    child_chunk_overlap: int = Field(64, ge=0, le=5000)
