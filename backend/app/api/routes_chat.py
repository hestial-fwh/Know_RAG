from fastapi import APIRouter

from app.schemas.chat import ChatRequest
from app.services import chat_service


router = APIRouter(tags=["chat"])


@router.post("/knowledge-chat/{session_id}")
async def knowledge_chat(session_id: str, payload: ChatRequest):
    return chat_service.knowledge_chat(session_id, payload.model_dump())
