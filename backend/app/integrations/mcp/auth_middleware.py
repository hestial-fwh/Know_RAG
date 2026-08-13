"""ASGI middleware enforcing signed MCP access.

Sits in front of the FastMCP Starlette app and requires every request to the
MCP endpoint to carry::

    X-Client-Id / X-Timestamp / X-Nonce / X-Signature

The middleware itself never sees client secrets.  It buffers the request
body, computes its SHA-256, extracts the JSON-RPC tool name (only for
``tools/call``), and forwards everything to the backend internal verify
endpoint (protected by the internal service token).  The backend performs
all checks — client lookup, enable/expiry, timestamp window, nonce replay,
HMAC comparison and tool scope — and the middleware simply enforces the
verdict (fail-closed).

Response pass-through is untouched, so SSE streaming keeps working.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 10 * 1024 * 1024
_VERIFY_TIMEOUT_SECONDS = 5.0


def _extract_tool_name(body: bytes) -> str | None:
    """Return the tool name for JSON-RPC ``tools/call`` requests.

    Structured parse of the standard JSON-RPC payload; returns None for
    non-tool requests (initialize / tools/list / ping), which still require
    a valid signature but are not scope-checked.
    """
    if not body:
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("method") != "tools/call":
        return None
    params = data.get("params")
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    return name if isinstance(name, str) and name else None


class McpAccessAuthMiddleware:
    """Pure ASGI middleware — no dependency on FastMCP or Starlette internals.

    ``verifier`` is an injectable async callable ``(payload: dict) -> (status,
    message)`` used by tests; when omitted it calls the backend internal
    verify endpoint with the internal service token.
    """

    def __init__(
        self,
        app: Any,
        *,
        protect_path: str = "/mcp",
        verifier: Any = None,
    ) -> None:
        self.app = app
        self.protect_path = protect_path or "/mcp"
        self._verifier = verifier
        api_base = (os.getenv("KNOW_RAG_API_BASE") or "http://127.0.0.1:8000").rstrip("/")
        self.verify_url = f"{api_base}/api/v1/internal/mcp-access/verify"
        self.api_token = (os.getenv("KNOW_RAG_API_TOKEN") or "").strip()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith(self.protect_path):
            await self.app(scope, receive, send)
            return

        # ── Buffer the request body (cap size) ────────────────────────────
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > _MAX_BODY_BYTES:
                await self._reject(send, 413, "MCP 请求体过大")
                return
            if not message.get("more_body", False):
                break
        body_bytes = bytes(body)

        headers: dict[str, str] = {}
        for key, value in scope.get("headers", []):
            headers.setdefault(key.decode("latin-1").lower(), value.decode("latin-1"))

        status, message = await self._verify(
            client_id=(headers.get("x-client-id") or "").strip(),
            timestamp=(headers.get("x-timestamp") or "").strip(),
            nonce=(headers.get("x-nonce") or "").strip(),
            signature=(headers.get("x-signature") or "").strip(),
            method=(scope.get("method") or "GET").upper(),
            path=path,
            body_bytes=body_bytes,
        )
        if status != 200:
            await self._reject(send, status, message)
            return

        # ── Replay the buffered body to the downstream MCP app ────────────
        sent = False

        async def replayed_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            return await receive()

        await self.app(scope, replayed_receive, send)

    async def _verify(
        self,
        *,
        client_id: str,
        timestamp: str,
        nonce: str,
        signature: str,
        method: str,
        path: str,
        body_bytes: bytes,
    ) -> tuple[int, str]:
        """Ask the backend to verify the signed request.  Returns (status, message)."""
        if not (client_id and timestamp and nonce and signature):
            return 401, "缺少签名头（X-Client-Id / X-Timestamp / X-Nonce / X-Signature）"

        payload = {
            "client_id": client_id,
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": signature,
            "method": method,
            "path": path,
            "body_sha256": hashlib.sha256(body_bytes).hexdigest(),
            "tool_name": _extract_tool_name(body_bytes),
        }

        if self._verifier is not None:
            try:
                result = self._verifier(payload)
                if inspect.isawaitable(result):
                    result = await result
                status, message = result
            except Exception:
                logger.exception("MCP access verify: custom verifier failed")
                return 503, "MCP 接入校验服务不可用"
            return (200, "") if status == 200 else (status, message)

        request_headers = {"Content-Type": "application/json"}
        if self.api_token:
            request_headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    self.verify_url, json=payload, headers=request_headers
                )
        except Exception:
            # Fail closed: never let MCP traffic through an unverified path.
            logger.exception("MCP access verify: backend unreachable")
            return 503, "MCP 接入校验服务不可用"

        if response.status_code == 200:
            return 200, ""

        message = f"MCP 接入校验失败（HTTP {response.status_code}）"
        try:
            data = response.json()
            if isinstance(data, dict) and data.get("message"):
                message = str(data["message"])
        except Exception:
            pass
        status = response.status_code if response.status_code in (401, 403, 422) else 503
        return status, message

    async def _reject(self, send, status: int, message: str) -> None:
        """Send a JSON-RPC shaped error response for the MCP client."""
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32001, "message": f"MCP access denied: {message}"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
