"""Nonce replay protection — Redis SET NX semantics + memory fallback."""

import time

from app.services.mcp_access import nonce_store


class FakeRedis:
    """Minimal fake with redis-py ``set(nx=True, ex=...)`` semantics."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._expiry: dict[str, float] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int = 0) -> bool:
        now = time.time()
        expires = self._expiry.get(key)
        if expires is not None and expires > now and nx:
            return False
        self._data[key] = value
        self._expiry[key] = now + ex
        return True


def test_redis_set_nx_first_ok_then_replay_rejected(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(nonce_store, "_redis_client", lambda: fake)

    assert nonce_store.consume_nonce("kr_c1", "nonce-1", ttl_seconds=600) is True
    assert nonce_store.consume_nonce("kr_c1", "nonce-1", ttl_seconds=600) is False


def test_redis_nonce_scoped_per_client(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(nonce_store, "_redis_client", lambda: fake)

    assert nonce_store.consume_nonce("kr_c1", "nonce-x", ttl_seconds=600) is True
    assert nonce_store.consume_nonce("kr_c2", "nonce-x", ttl_seconds=600) is True


def test_memory_fallback_replay_rejected(monkeypatch):
    monkeypatch.setattr(nonce_store, "_redis_client", lambda: None)
    monkeypatch.setattr(nonce_store, "_memory_store", {})

    assert nonce_store.consume_nonce("kr_c1", "nonce-m", ttl_seconds=600) is True
    assert nonce_store.consume_nonce("kr_c1", "nonce-m", ttl_seconds=600) is False


def test_memory_fallback_expires(monkeypatch):
    monkeypatch.setattr(nonce_store, "_redis_client", lambda: None)
    monkeypatch.setattr(nonce_store, "_memory_store", {})

    assert nonce_store.consume_nonce("kr_c1", "nonce-e", ttl_seconds=0) is True
    time.sleep(0.01)
    # ttl 0 → immediately expired → a fresh consume succeeds again
    assert nonce_store.consume_nonce("kr_c1", "nonce-e", ttl_seconds=0) is True


def test_empty_inputs_rejected():
    assert nonce_store.consume_nonce("", "nonce", 600) is False
    assert nonce_store.consume_nonce("kr_c1", "", 600) is False
