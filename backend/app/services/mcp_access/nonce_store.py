"""Nonce replay protection for MCP signed requests.

Primary storage is Redis with ``SET NX`` semantics: a nonce can be consumed
exactly once.  TTL is set to twice the timestamp window so a nonce outlives
any request that could legitimately reuse it.

When Redis is unreachable a process-local in-memory store is used (dev
fallback, same approach as ``app/services/captcha_service.py``).  It does not
protect across multiple backend workers — that is a documented dev-only
limitation.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "mcp:nonce:"

# ── in-memory fallback store ──────────────────────────────────────────────
_memory_store: dict[str, float] = {}  # key -> expires_at (monotonic-ish epoch)
_memory_lock = threading.Lock()


def nonce_key(client_id: str, nonce: str) -> str:
    return f"{_KEY_PREFIX}{client_id}:{nonce}"


def _redis_client() -> Optional[redis.Redis]:
    """Return a Redis client, or None when Redis is unreachable."""
    try:
        r = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=2, decode_responses=True
        )
        r.ping()
        return r
    except Exception:
        logger.warning("mcp_access: Redis unavailable, using in-memory nonce fallback (dev only)")
        return None


def consume_nonce(client_id: str, nonce: str, ttl_seconds: int) -> bool:
    """Atomically consume a nonce.

    Returns True when this nonce was never seen before (and is now marked
    used); False when it is a replay.
    """
    if not client_id or not nonce:
        return False

    r = _redis_client()
    if r is not None:
        try:
            # SET NX: returns True only if the key did not exist.
            return bool(r.set(nonce_key(client_id, nonce), "1", nx=True, ex=ttl_seconds))
        except Exception:
            logger.warning("mcp_access: Redis nonce write failed, falling back to memory")

    key = nonce_key(client_id, nonce)
    now = time.time()
    with _memory_lock:
        expires_at = _memory_store.get(key)
        if expires_at is not None and expires_at > now:
            return False
        _memory_store[key] = now + ttl_seconds
        # Opportunistic cleanup of expired entries to bound memory growth.
        if len(_memory_store) > 10_000:
            _memory_store.clear()
        return True
