"""Integration tests for the internal MCP access verify endpoint.

Covers the full security matrix: valid signature, wrong secret, wrong
signature, expired timestamp, replayed nonce, disabled / expired client,
insufficient scope, tampered body, secret reset.
"""

import secrets
import time
from datetime import datetime

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import McpClient
from app.services.mcp_access import nonce_store, signature

VERIFY = "/api/v1/internal/mcp-access/verify"
CREATE = "/api/v1/admin/mcp-clients"
BOTH_SCOPES = ["know_rag_list_knowledge_bases", "know_rag_search_context"]
LIST_ONLY = ["know_rag_list_knowledge_bases"]

TOOL_BODY = (
    '{"jsonrpc":"2.0","id":1,"method":"tools/call",'
    '"params":{"name":"know_rag_list_knowledge_bases","arguments":{}}}'
)


@pytest.fixture
def isolated_nonce(monkeypatch):
    """Force the deterministic in-memory nonce store."""
    monkeypatch.setattr(nonce_store, "_redis_client", lambda: None)
    monkeypatch.setattr(nonce_store, "_memory_store", {})


def _create(client, scopes=BOTH_SCOPES) -> dict:
    resp = client.post(
        CREATE,
        json={"name": "App", "expires_in_days": 30, "scopes": scopes},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _session(mcp_access) -> Session:
    return Session(bind=mcp_access.engine)


def _verify_payload(
    *,
    client_id: str,
    secret: str,
    tool_name=None,
    body: bytes = None,
    timestamp: str = None,
    nonce: str = None,
    signature_value: str = None,
    body_sha256: str = None,
    method: str = "POST",
    path: str = "/mcp",
) -> dict:
    if body is None:
        body = TOOL_BODY.encode("utf-8")
    if body_sha256 is None:
        body_sha256 = signature.sha256_hex(body)
    ts = timestamp if timestamp is not None else str(int(time.time()))
    nonce = nonce or secrets.token_hex(16)
    if signature_value is None:
        signature_value = signature.compute_signature(
            client_secret=secret,
            client_id=client_id,
            timestamp=ts,
            nonce=nonce,
            method=method,
            path=path,
            body_sha256=body_sha256,
        )
    return {
        "client_id": client_id,
        "timestamp": ts,
        "nonce": nonce,
        "signature": signature_value,
        "method": method,
        "path": path,
        "body_sha256": body_sha256,
        "tool_name": tool_name,
    }


# ── happy path ────────────────────────────────────────────────────────────

def test_valid_signature_allowed_and_updates_last_used(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    payload = _verify_payload(
        client_id=created["client_id"],
        secret=created["client_secret"],
        tool_name="know_rag_list_knowledge_bases",
    )
    resp = mcp_access.client.post(VERIFY, json=payload)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["allowed"] is True
    assert data["scopes"] == BOTH_SCOPES
    detail = mcp_access.client.get(f"{CREATE}/{created['id']}").json()["data"]
    assert detail["last_used_at"] is not None


def test_non_tool_request_skips_scope_check(mcp_access, isolated_nonce):
    created = _create(mcp_access.client, scopes=LIST_ONLY)
    payload = _verify_payload(
        client_id=created["client_id"],
        secret=created["client_secret"],
        tool_name=None,
        body=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
    )
    assert mcp_access.client.post(VERIFY, json=payload).status_code == 200


# ── credential failures ───────────────────────────────────────────────────

def test_wrong_secret_rejected(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    payload = _verify_payload(
        client_id=created["client_id"], secret=created["client_secret"] + "-tampered"
    )
    assert mcp_access.client.post(VERIFY, json=payload).status_code == 401


def test_wrong_signature_rejected(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    payload = _verify_payload(
        client_id=created["client_id"],
        secret=created["client_secret"],
        signature_value="00" * 32,
    )
    assert mcp_access.client.post(VERIFY, json=payload).status_code == 401


def test_tampered_body_hash_rejected(mcp_access, isolated_nonce):
    """Sign over body A, present body hash of body B — signature must fail."""
    created = _create(mcp_access.client)
    payload = _verify_payload(client_id=created["client_id"], secret=created["client_secret"])
    tampered = TOOL_BODY.replace("arguments", "argum3nts").encode("utf-8")
    payload["body_sha256"] = signature.sha256_hex(tampered)
    resp = mcp_access.client.post(VERIFY, json=payload)
    assert resp.status_code == 401
    assert "X-Signature" in resp.json()["message"]


def test_unknown_client_rejected(mcp_access, isolated_nonce):
    payload = _verify_payload(client_id="kr_doesnotexist", secret="whatever")
    assert mcp_access.client.post(VERIFY, json=payload).status_code == 401


def test_missing_signature_params_rejected(mcp_access):
    # 完全缺字段 → pydantic 422（结构错误）
    resp = mcp_access.client.post(VERIFY, json={"client_id": "kr_x"})
    assert resp.status_code == 422
    # 字段存在但为空 → 业务 401
    resp = mcp_access.client.post(
        VERIFY,
        json={
            "client_id": "",
            "timestamp": "",
            "nonce": "",
            "signature": "",
            "method": "POST",
            "path": "/mcp",
            "body_sha256": "",
        },
    )
    assert resp.status_code == 401


def test_invalid_timestamp_rejected(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    payload = _verify_payload(
        client_id=created["client_id"],
        secret=created["client_secret"],
        timestamp="not-a-number",
    )
    assert mcp_access.client.post(VERIFY, json=payload).status_code == 401


# ── timestamp window ──────────────────────────────────────────────────────

def test_expired_timestamp_rejected(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    payload = _verify_payload(
        client_id=created["client_id"],
        secret=created["client_secret"],
        timestamp=str(int(time.time()) - 600),
    )
    resp = mcp_access.client.post(VERIFY, json=payload)
    assert resp.status_code == 401
    assert "Timestamp" in resp.json()["message"]


def test_future_timestamp_rejected(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    payload = _verify_payload(
        client_id=created["client_id"],
        secret=created["client_secret"],
        timestamp=str(int(time.time()) + 600),
    )
    assert mcp_access.client.post(VERIFY, json=payload).status_code == 401


# ── nonce replay ──────────────────────────────────────────────────────────

def test_replayed_nonce_rejected(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    nonce = secrets.token_hex(16)
    payload = _verify_payload(
        client_id=created["client_id"], secret=created["client_secret"], nonce=nonce
    )
    first = mcp_access.client.post(VERIFY, json=payload)
    assert first.status_code == 200
    second = mcp_access.client.post(VERIFY, json=payload)
    assert second.status_code == 401
    assert "Nonce" in second.json()["message"]


# ── client state ──────────────────────────────────────────────────────────

def test_disabled_client_rejected(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    mcp_access.client.post(f"{CREATE}/{created['id']}/disable")
    payload = _verify_payload(
        client_id=created["client_id"], secret=created["client_secret"]
    )
    assert mcp_access.client.post(VERIFY, json=payload).status_code == 403


def test_expired_client_rejected(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    with _session(mcp_access) as db:
        db.execute(
            update(McpClient)
            .where(McpClient.id == created["id"])
            .values(expires_at=datetime(2000, 1, 1))
        )
        db.commit()
    payload = _verify_payload(
        client_id=created["client_id"], secret=created["client_secret"]
    )
    resp = mcp_access.client.post(VERIFY, json=payload)
    assert resp.status_code == 403
    assert "过期" in resp.json()["message"]


# ── tool scope ────────────────────────────────────────────────────────────

def test_scope_denied_for_disallowed_tool(mcp_access, isolated_nonce):
    created = _create(mcp_access.client, scopes=LIST_ONLY)
    payload = _verify_payload(
        client_id=created["client_id"],
        secret=created["client_secret"],
        tool_name="know_rag_search_context",
    )
    resp = mcp_access.client.post(VERIFY, json=payload)
    assert resp.status_code == 403
    assert "know_rag_search_context" in resp.json()["message"]


def test_scope_allowed_for_granted_tool(mcp_access, isolated_nonce):
    created = _create(mcp_access.client, scopes=LIST_ONLY)
    payload = _verify_payload(
        client_id=created["client_id"],
        secret=created["client_secret"],
        tool_name="know_rag_list_knowledge_bases",
    )
    assert mcp_access.client.post(VERIFY, json=payload).status_code == 200


# ── secret reset ──────────────────────────────────────────────────────────

def test_secret_reset_invalidates_old_secret(mcp_access, isolated_nonce):
    created = _create(mcp_access.client)
    old_secret = created["client_secret"]
    reset = mcp_access.client.post(f"{CREATE}/{created['id']}/reset-secret")
    new_secret = reset.json()["data"]["client_secret"]

    old_payload = _verify_payload(
        client_id=created["client_id"], secret=old_secret
    )
    assert mcp_access.client.post(VERIFY, json=old_payload).status_code == 401

    new_payload = _verify_payload(
        client_id=created["client_id"], secret=new_secret
    )
    assert mcp_access.client.post(VERIFY, json=new_payload).status_code == 200
