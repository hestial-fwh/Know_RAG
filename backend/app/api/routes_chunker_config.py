from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.chunker_config import ChunkerConfigPayload
from app.services import chunker_config_service as service


router = APIRouter(prefix="/chunker-config", tags=["chunker-config"])


@router.get("")
async def get_chunker_config(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return service.get_chunker_config(db)


@router.put("")
async def update_chunker_config(
    payload: ChunkerConfigPayload,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.update_chunker_config(db, payload.model_dump())
