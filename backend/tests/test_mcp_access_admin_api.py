"""Integration tests for the admin MCP client management API.

Runs against a minimal FastAPI app with a SQLite in-memory backend (see
``conftest.py``).
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import McpClient

CREATE_PAYLOAD = {
    "name": "Test App",
    "description": "desc",
    "expires_in_days": 30,
    "scopes": ["know_rag_list_knowledge_bases", "know_rag_search_context"],
}

BASE = "/api/v1/admin/mcp-clients"


def _create(client) -> dict:
    resp = client.post(BASE, json=CREATE_PAYLOAD)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _session(mcp_access) -> Session:
    return Session(bind=mcp_access.engine)


# ── create ────────────────────────────────────────────────────────────────

def test_create_returns_secret_once(mcp_access):
    data = _create(mcp_access.client)
    assert data["client_id"].startswith("kr_")
    assert len(data["client_secret"]) >= 32
    assert data["status"] == "enabled"
    assert data["scopes"] == CREATE_PAYLOAD["scopes"]
    assert data["expires_at"] is not None
    assert data["is_expired"] is False


def test_client_ids_unique_across_creates(mcp_access):
    ids = {_create(mcp_access.client)["client_id"] for _ in range(20)}
    assert len(ids) == 20


def test_permanent_expiry(mcp_access):
    resp = mcp_access.client.post(BASE, json={**CREATE_PAYLOAD, "expires_in_days": None})
    data = resp.json()["data"]
    assert data["expires_at"] is None


def test_invalid_scope_rejected(mcp_access):
    resp = mcp_access.client.post(BASE, json={**CREATE_PAYLOAD, "scopes": ["bogus_tool"]})
    assert resp.status_code == 422


def test_empty_scopes_rejected(mcp_access):
    resp = mcp_access.client.post(BASE, json={**CREATE_PAYLOAD, "scopes": []})
    assert resp.status_code == 422


def test_blank_name_rejected(mcp_access):
    resp = mcp_access.client.post(BASE, json={**CREATE_PAYLOAD, "name": "   "})
    assert resp.status_code == 422


def test_zero_days_rejected(mcp_access):
    resp = mcp_access.client.post(BASE, json={**CREATE_PAYLOAD, "expires_in_days": 0})
    assert resp.status_code == 422


# ── secret never leaks ────────────────────────────────────────────────────

def test_list_never_returns_secret(mcp_access):
    _create(mcp_access.client)
    listing = mcp_access.client.get(BASE).json()["data"]
    assert listing["total"] == 1
    item = listing["items"][0]
    assert "client_secret" not in item
    assert "client_secret_encrypted" not in item


def test_detail_never_returns_secret(mcp_access):
    created = _create(mcp_access.client)
    detail = mcp_access.client.get(f"{BASE}/{created['id']}").json()["data"]
    assert "client_secret" not in detail
    assert "client_secret_encrypted" not in detail


# ── update / enable / disable / delete ────────────────────────────────────

def test_update_fields(mcp_access):
    created = _create(mcp_access.client)
    resp = mcp_access.client.patch(
        f"{BASE}/{created['id']}",
        json={
            "name": "Renamed",
            "description": "d2",
            "scopes": ["know_rag_list_knowledge_bases"],
            "expires_in_days": 90,
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "Renamed"
    assert data["description"] == "d2"
    assert data["scopes"] == ["know_rag_list_knowledge_bases"]
    assert data["expires_at"] is not None


def test_update_to_permanent(mcp_access):
    created = _create(mcp_access.client)
    resp = mcp_access.client.patch(f"{BASE}/{created['id']}", json={"expires_in_days": None})
    assert resp.json()["data"]["expires_at"] is None


def test_enable_disable_cycle(mcp_access):
    created = _create(mcp_access.client)
    assert (
        mcp_access.client.post(f"{BASE}/{created['id']}/disable").json()["data"]["status"]
        == "disabled"
    )
    assert (
        mcp_access.client.post(f"{BASE}/{created['id']}/enable").json()["data"]["status"]
        == "enabled"
    )


def test_reset_secret_returns_new_secret_once(mcp_access):
    created = _create(mcp_access.client)
    old_secret = created["client_secret"]
    resp = mcp_access.client.post(f"{BASE}/{created['id']}/reset-secret")
    data = resp.json()["data"]
    assert data["client_secret"] != old_secret
    assert len(data["client_secret"]) >= 32
    detail = mcp_access.client.get(f"{BASE}/{created['id']}").json()["data"]
    assert "client_secret" not in detail


def test_expired_status_computed(mcp_access):
    created = _create(mcp_access.client)
    with _session(mcp_access) as db:
        db.execute(
            update(McpClient)
            .where(McpClient.id == created["id"])
            .values(expires_at=datetime(2000, 1, 1))
        )
        db.commit()
    listing = mcp_access.client.get(BASE).json()["data"]
    item = listing["items"][0]
    assert item["status"] == "expired"
    assert item["is_expired"] is True


def test_delete_removes_client(mcp_access):
    created = _create(mcp_access.client)
    assert mcp_access.client.delete(f"{BASE}/{created['id']}").status_code == 200
    assert mcp_access.client.get(f"{BASE}/{created['id']}").status_code == 404
    assert mcp_access.client.get(BASE).json()["data"]["total"] == 0


def test_unknown_id_404(mcp_access):
    assert mcp_access.client.get(f"{BASE}/no-such-id").status_code == 404
    assert mcp_access.client.patch(f"{BASE}/no-such-id", json={"name": "x"}).status_code == 404
    assert mcp_access.client.post(f"{BASE}/no-such-id/reset-secret").status_code == 404
    assert mcp_access.client.post(f"{BASE}/no-such-id/disable").status_code == 404
    assert mcp_access.client.delete(f"{BASE}/no-such-id").status_code == 404
