from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.mcp_client import McpVerifyRequest
from app.services.mcp_access import service

router = APIRouter(prefix="/internal/mcp-access", tags=["mcp-access"])


@router.post("/verify")
async def verify_mcp_access(
    payload: McpVerifyRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Internal endpoint used by the MCP auth middleware.

    Protected by the existing service-token path (``KNOW_RAG_SERVICE_TOKEN``)
    in ``get_current_user`` — the middleware sends that token when calling in.
    """
    return service.verify_request(db, payload)
