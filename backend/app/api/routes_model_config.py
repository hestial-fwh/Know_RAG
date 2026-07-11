from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.model_config import ModelConfig
from app.services import model_config_service as service


router = APIRouter(prefix="/model-config", tags=["model-config"])


@router.get("/providers")
async def provider_presets(_: User = Depends(get_current_user)):
    return service.provider_presets()


@router.get("")
async def get_model_config(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return service.get_model_config(db)


@router.put("")
async def update_model_config(
    payload: ModelConfig,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.update_model_config(db, payload.model_dump())


@router.post("/test")
async def test_model_config(payload: dict, _: User = Depends(get_current_user)):
    return await service.test_model_config(payload)


@router.post("/test/{model_type}")
async def test_single_model_config(model_type: str, payload: dict, _: User = Depends(get_current_user)):
    return await service.test_single_model_config(payload, model_type)


@router.post("/models")
async def list_provider_models(payload: dict, _: User = Depends(get_current_user)):
    model_type = payload.get("model_type") or ""
    return service.success_response(await service.list_available_models(payload, model_type))


@router.get("/deepdoc/status")
async def deepdoc_status(reload: bool = Query(False), _: User = Depends(get_current_user)):
    return service.deepdoc_status(reload=reload)
