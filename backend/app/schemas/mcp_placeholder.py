from pydantic import BaseModel


class MCPPlaceholderSearchRequest(BaseModel):
    query: str
    kb_ids: list[str] = []
    top_k: int | None = None
