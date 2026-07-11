from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.response import success_response
from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import LoginRequest
from app.services.auth_service import login_admin, user_to_dict
from app.services.captcha_service import generate_captcha, verify_captcha


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(payload: LoginRequest, db: Session = Depends(get_db)):
    if not verify_captcha(payload.captcha_id, payload.captcha_code):
        raise HTTPException(status_code=401, detail="验证码错误或已过期")
    result = login_admin(db, payload.username, payload.password)
    if result is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return success_response(result)


@router.get("/captcha")
async def captcha():
    return success_response(generate_captcha())


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return success_response(user_to_dict(current_user))


@router.post("/logout")
async def logout():
    return success_response({"logged_out": True})
