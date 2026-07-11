from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.vector_store import VectorStoreConfig
from app.services import vector_store_service as service


router = APIRouter(prefix="/vector-store", tags=["vector-store"])


@router.get("/providers")
async def vector_store_provider_presets(_: User = Depends(get_current_user)):
    return service.provider_presets()


@router.get("/config")
async def get_vector_store_config(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return service.get_config(db)


@router.put("/config")
async def update_vector_store_config(
    payload: VectorStoreConfig,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.update_config(db, payload.model_dump())


@router.post("/test")
async def test_vector_store(payload: dict | None = None, _: User = Depends(get_current_user)):
    return service.test_vector_store(payload)
