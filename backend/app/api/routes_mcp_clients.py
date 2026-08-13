from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.mcp_client import McpClientCreate, McpClientUpdate
from app.services.mcp_access import service

router = APIRouter(prefix="/admin/mcp-clients", tags=["mcp-clients"])


@router.get("")
async def list_mcp_clients(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.list_clients(db, page, page_size)


@router.post("")
async def create_mcp_client(
    payload: McpClientCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.create_client(db, payload.model_dump())


@router.get("/{id}")
async def get_mcp_client(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_client(db, id)


@router.patch("/{id}")
async def update_mcp_client(
    id: str,
    payload: McpClientUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.update_client(db, id, payload.model_dump(exclude_unset=True))


@router.post("/{id}/reset-secret")
async def reset_mcp_client_secret(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.reset_secret(db, id)


@router.post("/{id}/enable")
async def enable_mcp_client(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.set_enabled(db, id, True)


@router.post("/{id}/disable")
async def disable_mcp_client(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.set_enabled(db, id, False)


@router.delete("/{id}")
async def delete_mcp_client(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.delete_client(db, id)
