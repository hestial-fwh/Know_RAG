from pydantic import BaseModel


class ChatRequest(BaseModel):
    query: str
    kb_ids: list[str] = []


class ChatResponse(BaseModel):
    answer: str
    references: list[dict] = []
