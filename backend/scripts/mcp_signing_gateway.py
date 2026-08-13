"""HMAC 签名网关 — 让标准 MCP 客户端无需感知 HMAC 即可调用受保护的 Know_RAG MCP。

标准 MCP 客户端（MCP Inspector / Claude Desktop / Cursor / Cherry Studio 等）只支持
固定 header（``Authorization: Bearer ...``）或 OAuth，无法对每个请求动态计算
HMAC-SHA256（签名依赖 body_sha256 + timestamp + nonce，逐请求变化）。本网关坐在客户端
与真实 MCP 之间：客户端用静态 Bearer token 连网关，网关持有 MCP client_id + client_secret，
对每个转发请求注入 ``X-Client-Id / X-Timestamp / X-Nonce / X-Signature`` 后送到真实 MCP。

    MCP Inspector / Claude Desktop
        └─ http://localhost:8011/mcp  +  Authorization: Bearer <GATEWAY_TOKEN>
            └─ 网关(8011) 注入 HMAC 头
                └─ http://127.0.0.1:8010/mcp（真实 MCP，签名中间件在 8010 前）

环境变量：
    GATEWAY_HOST      监听地址，默认 0.0.0.0
    GATEWAY_PORT      监听端口，默认 8011
    GATEWAY_TOKEN     客户端需携带的静态 Bearer token（必填）
    MCP_URL           真实 MCP 基地址，默认 http://127.0.0.1:8010
    CLIENT_ID         MCP 客户端 ID（必填）
    CLIENT_SECRET     MCP 客户端 Secret 明文（必填，仅网关持有，不落库明文）

在 mcp 容器内启动（该容器与 MCP 服务同机，已含 app 包 + httpx/uvicorn）：

    docker compose exec -d \
        -e GATEWAY_TOKEN=your-static-token \
        -e CLIENT_ID=kr_xxx \
        -e CLIENT_SECRET=xxx \
        mcp python scripts/mcp_signing_gateway.py
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app.services.mcp_access.signature import compute_signature, sha256_hex

GATEWAY_HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8011"))
GATEWAY_TOKEN = (os.getenv("GATEWAY_TOKEN") or "").strip()
UPSTREAM = (os.getenv("MCP_URL") or "http://127.0.0.1:8010").rstrip("/")
CLIENT_ID = (os.getenv("CLIENT_ID") or "").strip()
CLIENT_SECRET = (os.getenv("CLIENT_SECRET") or "").strip()

_PROTOCOL_VERSION = "2025-06-18"
# 逐跳头：转发响应时剔除，交给 StreamingResponse 重新计算。
_HOP_HEADERS = {"content-length", "transfer-encoding", "connection", "keep-alive", "upgrade"}


def _sign(method: str, path: str, body: bytes) -> dict[str, str]:
    """按后端唯一 canonical 规则（app/services/mcp_access/signature.py）生成 HMAC 头。"""
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = compute_signature(
        client_secret=CLIENT_SECRET,
        client_id=CLIENT_ID,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=path,
        body_sha256=sha256_hex(body),
    )
    return {
        "X-Client-Id": CLIENT_ID,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


def _authorized(request: Request) -> bool:
    """常量时间比较网关自身的静态 Bearer token。"""
    provided = request.headers.get("authorization", "")
    if provided.lower().startswith("bearer "):
        provided = provided[7:]
    return hmac.compare_digest(provided.encode("utf-8"), GATEWAY_TOKEN.encode("utf-8"))


async def _proxy(request: Request) -> Response:
    if not _authorized(request):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32001, "message": "gateway: unauthorized"},
            },
            status_code=401,
        )

    body = await request.body()  # 原样转发，body_sha256 才不会失配
    path = request.url.path or "/mcp"

    headers = _sign(request.method, path, body)
    headers["Content-Type"] = request.headers.get("content-type", "application/json")
    headers["Accept"] = request.headers.get(
        "accept", "application/json, text/event-stream"
    )
    headers["Mcp-Protocol-Version"] = request.headers.get(
        "mcp-protocol-version", _PROTOCOL_VERSION
    )
    session_id = request.headers.get("mcp-session-id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id  # 会话必须透传，否则握手失败

    upstream = request.app.state.upstream
    req = upstream.build_request(request.method, path, content=body, headers=headers)
    resp = await upstream.send(req, stream=True)

    resp_headers = {
        k: v for k, v in resp.headers.items() if k.lower() not in _HOP_HEADERS
    }

    async def stream():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(stream(), status_code=resp.status_code, headers=resp_headers)


async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok", "client_id": CLIENT_ID})


@asynccontextmanager
async def lifespan(app: Starlette):
    # SSE 长连接需要较大 read 超时；connect/write 保持紧凑。
    app.state.upstream = httpx.AsyncClient(
        base_url=UPSTREAM,
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
        follow_redirects=True,
    )
    try:
        yield
    finally:
        await app.state.upstream.aclose()


app = Starlette(
    lifespan=lifespan,
    routes=[Route("/health", health, methods=["GET"]), Route("/mcp", _proxy, methods=["POST", "GET"])],
)


def _main() -> None:
    missing = [name for name, val in (
        ("GATEWAY_TOKEN", GATEWAY_TOKEN),
        ("CLIENT_ID", CLIENT_ID),
        ("CLIENT_SECRET", CLIENT_SECRET),
    ) if not val]
    if missing:
        raise SystemExit(f"缺少环境变量: {', '.join(missing)}")

    import uvicorn

    print(
        f"签名网关启动: http://{GATEWAY_HOST}:{GATEWAY_PORT}/mcp -> {UPSTREAM}/mcp "
        f"(client_id={CLIENT_ID})"
    )
    uvicorn.run(app, host=GATEWAY_HOST, port=GATEWAY_PORT, log_level="info")


if __name__ == "__main__":
    _main()
