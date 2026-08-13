"""Admin CRUD + request verification for MCP signed access.

Split into two halves:

- **Admin CRUD** — used by the frontend management page (any logged-in
  admin).  Client secrets are returned exactly once, on create / reset.
- **verify_request** — used by the internal verify endpoint that the MCP
  auth middleware calls on every signed MCP request.  All rejection paths
  raise :class:`fastapi.HTTPException` so the caller gets a clear status
  code and message.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import success_response
from app.db.models import McpClient
from app.schemas.mcp_client import McpVerifyRequest
from app.services.mcp_access import crypto, nonce_store, signature
from app.utils.ids import new_id

logger = logging.getLogger(__name__)

ALLOWED_SCOPES = frozenset(
    {
        "know_rag_list_knowledge_bases",
        "know_rag_search_context",
    }
)


# ── helpers ──────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(dt: datetime | None) -> datetime | None:
    """Normalize DB datetimes to tz-aware UTC (SQLite stores naive values)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    dt = _to_utc(dt)
    return dt.isoformat() if dt is not None else None


def _validate_scopes(scopes: list[str] | None) -> list[str]:
    if not scopes:
        raise HTTPException(status_code=422, detail="至少需要选择一个 MCP Tool 权限")
    cleaned: list[str] = []
    for scope in scopes:
        if scope not in ALLOWED_SCOPES:
            raise HTTPException(status_code=422, detail=f"无效的 MCP Tool 权限: {scope}")
        if scope not in cleaned:
            cleaned.append(scope)
    return cleaned


def _resolve_expires_at(expires_in_days: int | None) -> datetime | None:
    """None = permanent; N = now + N days."""
    if expires_in_days is None:
        return None
    if expires_in_days <= 0:
        raise HTTPException(status_code=422, detail="有效期天数必须大于 0")
    return _now_utc() + timedelta(days=expires_in_days)


def client_status(client: McpClient) -> str:
    """disabled > expired > enabled (display precedence)."""
    if not client.is_enabled:
        return "disabled"
    expires = _to_utc(client.expires_at)
    if expires is not None and expires <= _now_utc():
        return "expired"
    return "enabled"


def client_to_dict(client: McpClient) -> dict:
    """Public representation — never includes the secret."""
    status = client_status(client)
    return {
        "id": client.id,
        "name": client.name,
        "client_id": client.client_id,
        "is_enabled": client.is_enabled,
        "expires_at": _iso(client.expires_at),
        "scopes": client.scopes or [],
        "description": client.description or "",
        "last_used_at": _iso(client.last_used_at),
        "created_at": _iso(client.created_at),
        "updated_at": _iso(client.updated_at),
        "status": status,
        "is_expired": status == "expired",
    }


def _get_or_404(db: Session, row_id: str) -> McpClient:
    client = db.get(McpClient, row_id)
    if client is None:
        raise HTTPException(status_code=404, detail="MCP Client 不存在")
    return client


# ── admin CRUD ────────────────────────────────────────────────────────────

def list_clients(db: Session, page: int = 1, page_size: int = 20) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    total = db.scalar(select(func.count()).select_from(McpClient)) or 0
    rows = (
        db.execute(
            select(McpClient)
            .order_by(McpClient.created_at.desc(), McpClient.name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return success_response(
        {
            "items": [client_to_dict(c) for c in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


def create_client(db: Session, payload: dict) -> dict:
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="应用名称不能为空")
    scopes = _validate_scopes(payload.get("scopes") or [])
    expires_at = _resolve_expires_at(payload.get("expires_in_days"))

    client_id = crypto.generate_client_id()
    for _ in range(5):
        existing = db.scalar(select(McpClient).where(McpClient.client_id == client_id))
        if existing is None:
            break
        client_id = crypto.generate_client_id()
    else:
        raise HTTPException(status_code=500, detail="Client ID 生成冲突，请重试")

    secret = crypto.generate_client_secret()
    client = McpClient(
        id=new_id(),
        name=name,
        client_id=client_id,
        client_secret_encrypted=crypto.encrypt_secret(secret),
        is_enabled=True,
        expires_at=expires_at,
        scopes=scopes,
        description=(payload.get("description") or "").strip(),
    )
    db.add(client)
    db.commit()
    db.refresh(client)

    data = client_to_dict(client)
    # 唯一一次返回明文 secret。
    data["client_secret"] = secret
    return success_response(data, "创建成功。Client Secret 仅展示一次，请立即保存。")


def get_client(db: Session, row_id: str) -> dict:
    return success_response(client_to_dict(_get_or_404(db, row_id)))


def update_client(db: Session, row_id: str, payload: dict) -> dict:
    client = _get_or_404(db, row_id)
    if payload.get("name") is not None:
        name = (payload["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="应用名称不能为空")
        client.name = name
    if payload.get("description") is not None:
        client.description = (payload["description"] or "").strip()
    if payload.get("scopes") is not None:
        client.scopes = _validate_scopes(payload["scopes"])
    if "expires_in_days" in payload:
        client.expires_at = _resolve_expires_at(payload["expires_in_days"])
    db.commit()
    db.refresh(client)
    return success_response(client_to_dict(client))


def reset_secret(db: Session, row_id: str) -> dict:
    client = _get_or_404(db, row_id)
    secret = crypto.generate_client_secret()
    client.client_secret_encrypted = crypto.encrypt_secret(secret)
    db.commit()
    db.refresh(client)
    data = client_to_dict(client)
    data["client_secret"] = secret
    return success_response(
        data, "Secret 已重置。旧 Secret 立即失效，新 Secret 仅展示一次，请立即保存。"
    )


def set_enabled(db: Session, row_id: str, enabled: bool) -> dict:
    client = _get_or_404(db, row_id)
    client.is_enabled = enabled
    db.commit()
    db.refresh(client)
    return success_response(client_to_dict(client))


def delete_client(db: Session, row_id: str) -> dict:
    client = _get_or_404(db, row_id)
    db.delete(client)
    db.commit()
    return success_response({"id": row_id})


# ── request verification (internal verify endpoint) ───────────────────────

def verify_request(db: Session, payload: McpVerifyRequest) -> dict:
    """Run the full verification chain.  Raises HTTPException on rejection."""
    client_id = (payload.client_id or "").strip()
    timestamp = (payload.timestamp or "").strip()
    nonce = (payload.nonce or "").strip()
    signature_value = (payload.signature or "").strip()
    if not (client_id and timestamp and nonce and signature_value):
        raise HTTPException(status_code=401, detail="缺少签名参数")

    client = db.scalar(select(McpClient).where(McpClient.client_id == client_id))
    if client is None:
        raise HTTPException(status_code=401, detail="Client 不存在或已删除")

    if not client.is_enabled:
        raise HTTPException(status_code=403, detail="Client 已禁用")

    expires = _to_utc(client.expires_at)
    if expires is not None and expires <= _now_utc():
        raise HTTPException(status_code=403, detail="Client 已过期")

    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-Timestamp 无效")

    max_age = max(30, settings.mcp_signature_max_age_seconds)
    now_ts = int(_now_utc().timestamp())
    if abs(now_ts - ts) > max_age:
        raise HTTPException(status_code=401, detail="X-Timestamp 已过期（超出允许的时钟偏差）")

    if not nonce_store.consume_nonce(client_id, nonce, ttl_seconds=max_age * 2):
        raise HTTPException(status_code=401, detail="X-Nonce 已使用（疑似重放请求）")

    try:
        secret = crypto.decrypt_secret(client.client_secret_encrypted)
    except crypto.McpSecretCryptoError as exc:
        logger.error("MCP client %s secret decryption failed: %s", client_id, exc)
        raise HTTPException(
            status_code=500,
            detail="服务端凭据解密失败，请检查 MCP_CLIENT_SECRET_ENCRYPTION_KEY 配置",
        )

    if not signature.verify_signature(
        client_secret=secret,
        client_id=client_id,
        timestamp=timestamp,
        nonce=nonce,
        method=payload.method,
        path=payload.path,
        body_sha256=(payload.body_sha256 or "").strip().lower(),
        signature=signature_value,
    ):
        raise HTTPException(status_code=401, detail="X-Signature 校验失败")

    if payload.tool_name:
        if payload.tool_name not in set(client.scopes or []):
            raise HTTPException(status_code=403, detail=f"无权限调用工具 {payload.tool_name}")

    client.last_used_at = _now_utc()
    db.commit()

    return success_response(
        {
            "allowed": True,
            "client_id": client.client_id,
            "name": client.name,
            "scopes": client.scopes or [],
        }
    )
