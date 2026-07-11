from fastapi import APIRouter

from app.schemas.mcp_placeholder import MCPPlaceholderSearchRequest
from app.services import mcp_placeholder_service as service


router = APIRouter(prefix="/mcp-placeholder", tags=["mcp-placeholder"])


@router.get("/health")
async def mcp_placeholder_health():
    return service.health()


@router.post("/search_documents")
async def search_documents(payload: MCPPlaceholderSearchRequest):
    return service.placeholder("search_documents", payload.model_dump())


@router.post("/search_faq")
async def search_faq(payload: MCPPlaceholderSearchRequest):
    return service.placeholder("search_faq", payload.model_dump())


@router.post("/get_context_pack")
async def get_context_pack(payload: dict):
    return service.placeholder("get_context_pack", payload)


@router.get("/chunks/{chunk_id}")
async def chunk_detail(chunk_id: str):
    return service.placeholder("chunk_detail", {"chunk_id": chunk_id})
