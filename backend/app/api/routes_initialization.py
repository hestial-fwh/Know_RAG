from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.response import placeholder_response, success_response
from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services import embedding_service, ollama_service


router = APIRouter(prefix="/initialization", tags=["initialization"])


@router.get("/ollama/status")
async def ollama_status(_: User = Depends(get_current_user)):
    return success_response(await ollama_service.ollama_status())


@router.get("/ollama/models")
async def ollama_models(_: User = Depends(get_current_user)):
    return success_response(await ollama_service.list_ollama_models())


@router.post("/embedding/test")
async def embedding_test(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await embedding_service.test_embedding(payload, actor=current_user, db=db)


@router.post("/llm/test")
async def llm_test(payload: dict, _: User = Depends(get_current_user)):
    return placeholder_response("llm test", payload)
