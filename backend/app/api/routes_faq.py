from fastapi import APIRouter

from app.schemas.faq import FAQCreate, FAQUpdate
from app.services import faq_service as service


router = APIRouter(tags=["faqs"])


@router.get("/knowledge-bases/{kb_id}/faqs")
async def list_faqs(kb_id: str):
    return service.list_faqs(kb_id)


@router.post("/knowledge-bases/{kb_id}/faqs")
async def create_faq(kb_id: str, payload: FAQCreate):
    return service.create_faq(kb_id, payload.model_dump())


@router.get("/faqs/{id}")
async def get_faq(id: str):
    return service.get_faq(id)


@router.put("/faqs/{id}")
async def update_faq(id: str, payload: FAQUpdate):
    return service.update_faq(id, payload.model_dump(exclude_none=True))


@router.delete("/faqs/{id}")
async def delete_faq(id: str):
    return service.delete_faq(id)


@router.post("/knowledge-bases/{kb_id}/faqs/search")
async def search_faqs(kb_id: str, payload: dict):
    return service.search_faqs(kb_id, payload)
