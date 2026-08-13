"""Signed MCP test client for Know_RAG.

Sends real JSON-RPC requests to the Know_RAG MCP Streamable HTTP endpoint
with the full protocol session flow:

1. POST ``initialize`` (signed) → read ``Mcp-Session-Id`` from the response.
2. POST ``tools/call`` (signed) with the session id.

The canonicalization used here is imported from the same backend module the
server verifies with (``app.services.mcp_access.signature``), so client and
server can never disagree about the string to sign.

Usage (inside the backend container, or any env with the backend on
PYTHONPATH):

    # Case A — list knowledge bases
    python scripts/test_mcp_signed_client.py \\
        --url http://localhost:8010/mcp \\
        --client-id kr_xxx --client-secret <secret> \\
        --tool list

    # Case B — search context in the TEST1 knowledge base
    python scripts/test_mcp_signed_client.py \\
        --url http://localhost:8010/mcp \\
        --client-id kr_xxx --client-secret <secret> \\
        --tool search --kb-id 9bdbd596-... --query "什么是专利"

Environment fallbacks: MCP_URL, CLIENT_ID, CLIENT_SECRET, KB_ID.

Negative / security test flags (applied to the tools/call request only —
the initialize request is always signed correctly):
    --no-sign             omit signature headers entirely
    --wrong-secret        sign with a tampered secret
    --timestamp-offset N  shift the signed timestamp by N seconds
                          (e.g. -600 = expired)
    --fixed-nonce VALUE   reuse the given nonce — run the same command twice
                          to demonstrate replay rejection
    --tamper-body         alter the JSON body AFTER signing (body hash breaks)
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    print("缺少 httpx：请在 backend 容器内运行本脚本（docker compose exec backend python scripts/test_mcp_signed_client.py）")
    raise SystemExit(1) from exc

try:
    from app.services.mcp_access.signature import compute_signature, sha256_hex
except ImportError as exc:  # pragma: no cover
    print("无法导入 app.services.mcp_access.signature：请在 backend 容器内运行本脚本")
    raise SystemExit(1) from exc

_PROTOCOL_VERSION = "2025-06-18"
_MAX_BODY_PRINT = 1200


def build_initialize() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "know-rag-test-client", "version": "0.1.0"},
        },
    }


def build_initialized_notification() -> dict:
    """MCP 握手第二步：initialize 之后必须发 initialized 通知才算完成。"""
    return {"jsonrpc": "2.0", "method": "notifications/initialized"}


def build_tools_call(args: argparse.Namespace) -> dict:
    tool_name = (
        "know_rag_list_knowledge_bases"
        if args.tool == "list"
        else "know_rag_search_context"
    )
    if args.tool == "list":
        arguments = {"page": args.page, "page_size": args.page_size}
    else:
        if not args.kb_id:
            print("search 模式需要 --kb-id（知识库 ID）")
            raise SystemExit(2)
        arguments = {
            "query_text": args.query,
            "knowledge_base_id": args.kb_id,
            "match_count": args.match_count,
        }
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def _post(
    url: str,
    envelope: dict,
    args: argparse.Namespace,
    *,
    session_id: str = "",
    unsigned: bool = False,
    apply_flags: bool = True,
) -> httpx.Response:
    body_bytes = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        # MCP Streamable HTTP 规范要求客户端必须同时接受两种响应格式
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": _PROTOCOL_VERSION,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    if not unsigned:
        if apply_flags:
            timestamp = str(int(time.time()) + args.timestamp_offset)
            nonce = args.fixed_nonce or secrets.token_hex(16)
            secret_for_sign = (
                args.client_secret + "-tampered"
            ) if args.wrong_secret else args.client_secret
        else:
            # initialize 始终用全新时间戳 / nonce / 正确 secret 签名
            timestamp = str(int(time.time()))
            nonce = secrets.token_hex(16)
            secret_for_sign = args.client_secret
        signature = compute_signature(
            client_secret=secret_for_sign,
            client_id=args.client_id,
            timestamp=timestamp,
            nonce=nonce,
            method="POST",
            path="/mcp",
            body_sha256=sha256_hex(body_bytes),
        )
        headers.update(
            {
                "X-Client-Id": args.client_id,
                "X-Timestamp": timestamp,
                "X-Nonce": nonce,
                "X-Signature": signature,
            }
        )
        if args.tamper_body and apply_flags:
            # 签名之后再篡改正文：签名不变但 body 变了 → body hash 失配
            tampered = json.loads(body_bytes.decode("utf-8"))
            tampered["id"] = 999
            body_bytes = json.dumps(
                tampered, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
    return httpx.post(url, content=body_bytes, headers=headers, timeout=60)


def _print_response(response: httpx.Response) -> None:
    print(f"  HTTP     : {response.status_code}")
    content_type = response.headers.get("content-type", "")
    text = response.text
    if "text/event-stream" in content_type:
        # SSE 流：提取 data: 行（MCP streamable-http 的 message 事件）
        data_lines = [
            line[5:].strip()
            for line in text.splitlines()
            if line.startswith("data:")
        ]
        text = "\n".join(data_lines) or text
    if len(text) > _MAX_BODY_PRINT:
        text = text[:_MAX_BODY_PRINT] + f"\n... (截断，共 {len(response.text)} 字符)"
    print(f"  body     :\n{text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Know_RAG 签名 MCP 测试客户端")
    parser.add_argument("--url", default=os.getenv("MCP_URL", "http://localhost:8010/mcp"))
    parser.add_argument("--client-id", default=os.getenv("CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.getenv("CLIENT_SECRET", ""))
    parser.add_argument("--tool", choices=["list", "search"], default="list")
    parser.add_argument("--query", default="什么是专利？")
    parser.add_argument("--kb-id", default=os.getenv("KB_ID", ""))
    parser.add_argument("--match-count", type=int, default=5)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=20)
    # ── security test flags ───────────────────────────────────────────────
    parser.add_argument("--no-sign", action="store_true", help="不携带任何签名头")
    parser.add_argument("--wrong-secret", action="store_true", help="用被篡改的 secret 签名")
    parser.add_argument("--timestamp-offset", type=int, default=0, help="时间戳偏移秒数，-600 模拟过期")
    parser.add_argument("--fixed-nonce", default="", help="固定 nonce（重复运行演示重放拒绝）")
    parser.add_argument("--tamper-body", action="store_true", help="签名后篡改请求体")
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        if args.no_sign:
            args.client_id = args.client_id or "kr_missing"
        else:
            print("需要 --client-id 与 --client-secret（或环境变量 CLIENT_ID / CLIENT_SECRET）")
            raise SystemExit(2)

    print(f"POST {args.url}")
    print(f"  client_id: {args.client_id}")

    if args.no_sign:
        # 负例：直接发未签名的 tools/call
        envelope = build_tools_call(args)
        response = _post(args.url, envelope, args, unsigned=True)
        _print_response(response)
        sys.exit(0 if response.status_code == 200 else 1)

    # ── 1. initialize（始终正确签名）建立会话 ────────────────────────────
    init_envelope = build_initialize()
    init_response = _post(args.url, init_envelope, args, apply_flags=False)
    print("  [initialize]")
    _print_response(init_response)
    if init_response.status_code != 200:
        sys.exit(1)

    session_id = init_response.headers.get("Mcp-Session-Id", "")

    # ── 2. initialized 通知（完成握手，服务端不返回业务响应） ─────────────
    _post(
        args.url,
        build_initialized_notification(),
        args,
        session_id=session_id,
        apply_flags=False,
    )

    # ── 3. tools/call（应用负例旗标） ─────────────────────────────────────
    call_envelope = build_tools_call(args)
    print(f"  [tools/call: {call_envelope['params']['name']}]")
    response = _post(args.url, call_envelope, args, session_id=session_id)
    _print_response(response)
    sys.exit(0 if response.status_code == 200 else 1)


if __name__ == "__main__":
    main()
