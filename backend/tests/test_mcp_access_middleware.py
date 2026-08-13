"""Unit tests for the MCP ASGI auth middleware (no network, injected verifier)."""

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.integrations.mcp import auth_middleware
from app.integrations.mcp.auth_middleware import McpAccessAuthMiddleware, _extract_tool_name
from app.services.mcp_access.signature import sha256_hex

SIGNED_HEADERS = {
    "X-Client-Id": "kr_abc",
    "X-Timestamp": "1789300000",
    "X-Nonce": "nonce-1",
    "X-Signature": "deadbeef",
}


async def _echo(request):
    body = await request.body()
    return JSONResponse(
        {"method": request.method, "path": request.url.path, "body": body.decode("utf-8")}
    )


def _make_app(verifier=None, protect_path="/mcp"):
    inner = Starlette(
        routes=[
            Route("/mcp", _echo, methods=["POST", "GET"]),
            Route("/other", _echo, methods=["GET", "POST"]),
        ]
    )
    return McpAccessAuthMiddleware(inner, protect_path=protect_path, verifier=verifier)


async def _request(app, method, path, body=b"", headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, content=body, headers=headers or {})


# ── _extract_tool_name ────────────────────────────────────────────────────

def test_extract_tool_name_tools_call():
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        b'"params":{"name":"know_rag_search_context","arguments":{}}}'
    )
    assert _extract_tool_name(body) == "know_rag_search_context"


def test_extract_tool_name_non_tool_methods():
    assert _extract_tool_name(b'{"jsonrpc":"2.0","method":"initialize","params":{}}') is None
    assert _extract_tool_name(b'{"jsonrpc":"2.0","method":"tools/list"}') is None


def test_extract_tool_name_malformed():
    assert _extract_tool_name(b"") is None
    assert _extract_tool_name(b"not json") is None
    assert _extract_tool_name(b'["array"]') is None
    assert _extract_tool_name(b'{"method":"tools/call","params":{}}') is None
    assert _extract_tool_name(b'{"method":"tools/call","params":{"name":""}}') is None


# ── rejection path ────────────────────────────────────────────────────────

async def test_unsigned_request_rejected_with_jsonrpc_error():
    app = _make_app(verifier=lambda payload: (200, ""))
    resp = await _request(app, "POST", "/mcp", body=b"{}")
    assert resp.status_code == 401
    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["error"]["code"] == -32001
    assert "MCP access denied" in data["error"]["message"]


async def test_missing_single_header_rejected():
    app = _make_app(verifier=lambda payload: (200, ""))
    headers = dict(SIGNED_HEADERS)
    headers.pop("X-Nonce")
    resp = await _request(app, "POST", "/mcp", body=b"{}", headers=headers)
    assert resp.status_code == 401


async def test_verifier_403_surfaces_as_403():
    app = _make_app(verifier=lambda payload: (403, "Client 已禁用"))
    resp = await _request(app, "POST", "/mcp", body=b"{}", headers=SIGNED_HEADERS)
    assert resp.status_code == 403
    assert "Client 已禁用" in resp.json()["error"]["message"]


async def test_verifier_exception_fails_closed():
    async def broken(payload):
        raise RuntimeError("boom")

    app = _make_app(verifier=broken)
    resp = await _request(app, "POST", "/mcp", body=b"{}", headers=SIGNED_HEADERS)
    assert resp.status_code == 503


async def test_oversized_body_rejected(monkeypatch):
    monkeypatch.setattr(auth_middleware, "_MAX_BODY_BYTES", 16)
    app = _make_app(verifier=lambda payload: (200, ""))
    resp = await _request(app, "POST", "/mcp", body=b"x" * 17, headers=SIGNED_HEADERS)
    assert resp.status_code == 413


# ── pass-through path ─────────────────────────────────────────────────────

async def test_verified_request_body_passes_through_intact():
    captured = {}

    async def verifier(payload):
        captured.update(payload)
        return 200, ""

    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        b'"params":{"name":"know_rag_list_knowledge_bases","arguments":{"page":1}}}'
    )
    app = _make_app(verifier=verifier)
    resp = await _request(app, "POST", "/mcp", body=body, headers=SIGNED_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["body"] == body.decode("utf-8")

    # verifier received exactly the parsed signature context
    assert captured["client_id"] == "kr_abc"
    assert captured["timestamp"] == "1789300000"
    assert captured["nonce"] == "nonce-1"
    assert captured["signature"] == "deadbeef"
    assert captured["method"] == "POST"
    assert captured["path"] == "/mcp"
    assert captured["body_sha256"] == sha256_hex(body)
    assert captured["tool_name"] == "know_rag_list_knowledge_bases"


async def test_get_request_empty_body_hashed():
    captured = {}

    async def verifier(payload):
        captured.update(payload)
        return 200, ""

    app = _make_app(verifier=verifier)
    resp = await _request(app, "GET", "/mcp", headers=SIGNED_HEADERS)
    assert resp.status_code == 200
    assert captured["method"] == "GET"
    assert captured["body_sha256"] == sha256_hex(b"")


async def test_non_protected_path_skipped():
    async def verifier(payload):
        raise AssertionError("verifier must not run for /other")

    app = _make_app(verifier=verifier)
    resp = await _request(app, "GET", "/other")
    assert resp.status_code == 200
    assert resp.json()["path"] == "/other"


async def test_non_http_scope_passes_through_untouched():
    received = []

    async def stub(scope, receive, send):
        received.append(scope)

    app = McpAccessAuthMiddleware(stub, verifier=lambda payload: (200, ""))
    scope = {"type": "lifespan", "message": "lifespan.startup"}
    await app(scope, None, None)
    assert received == [scope]
