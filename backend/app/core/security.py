from datetime import datetime, timedelta, timezone
import hmac

from fastapi import Depends, Header, HTTPException
from jose import jwt
from jose.exceptions import JWTError
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.db.models import User
from app.utils.ids import new_id


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(subject: str, expires_minutes: int = 480) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])


def _is_service_token(token: str) -> bool:
    """Check whether this token is the internal service token.

    This token is intended for service-to-service calls such as MCP → Know_RAG.
    It should be long, random, and never exposed in the browser.
    """
    configured = (settings.know_rag_service_token or "").strip()
    if not configured:
        return False
    return hmac.compare_digest(token, configured)


def _get_or_create_service_user(db: Session) -> User:
    """Return an admin-like user for internal service-token requests.

    Minimal implementation:
    - Prefer ADMIN_USERNAME user;
    - fallback to first user;
    - create default admin if no user exists.

    This keeps existing service function signatures unchanged because most APIs
    currently expect a User object.
    """
    user = db.scalar(select(User).where(User.username == settings.admin_username))
    if user is not None:
        return user

    user = db.scalar(select(User).limit(1))
    if user is not None:
        return user

    user = User(
        id=new_id(),
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        role="admin",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录或登录已失效")

    token = authorization.split(" ", 1)[1].strip()

    # Internal service-token path.
    # This bypasses browser captcha and is used by MCP / internal services only.
    if _is_service_token(token):
        return _get_or_create_service_user(db)

    try:
        payload = decode_access_token(token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="登录令牌无效") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="登录令牌无效")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    return user