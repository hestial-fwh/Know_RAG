from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.db.models import User
from app.utils.ids import new_id
from app.utils.tokens import issue_token


def ensure_default_admin(db: Session) -> User:
    user = db.scalar(select(User).limit(1))
    if user is not None:
        return user
    admin = User(
        id=new_id(),
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        role="admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def login_admin(db: Session, username: str, password: str) -> dict | None:
    ensure_default_admin(db)
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.password_hash):
        return None
    return {
        "access_token": issue_token(user.id),
        "token_type": "bearer",
        "user": user_to_dict(user),
    }


def user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }
