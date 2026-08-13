"""Remote Know_RAG MCP server.

This starts the same Know_RAG MCP tools through Streamable HTTP transport
(default) or SSE, with signed-access auth enforced in front of the MCP
endpoint (see ``auth_middleware.py``).

Run:
    python -m app.integrations.mcp.http_server

Default endpoint:
    http://0.0.0.0:8010/mcp

Environment variables:
    MCP_TRANSPORT=streamable-http
    MCP_MOUNT_PATH=/mcp
    MCP_HOST=0.0.0.0
    MCP_PORT=8010

Know_RAG API variables used by the auth middleware and http_client.py:
    KNOW_RAG_API_BASE=http://backend:8000
    KNOW_RAG_API_TOKEN=<optional service token>
    KNOW_RAG_USERNAME=admin
    KNOW_RAG_PASSWORD=admin123
"""

import os

import uvicorn

from app.integrations.mcp.auth_middleware import McpAccessAuthMiddleware
from app.integrations.mcp.server import mcp


def main() -> None:
    transport = (os.getenv("MCP_TRANSPORT") or "streamable-http").strip().lower()
    mount_path = (os.getenv("MCP_MOUNT_PATH") or "/mcp").strip() or "/mcp"
    host = (os.getenv("MCP_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    try:
        port = int((os.getenv("MCP_PORT") or "8010").strip() or 8010)
    except ValueError:
        port = 8010

    if transport not in {"streamable-http", "sse"}:
        transport = "streamable-http"

    if transport == "sse":
        mcp_app = mcp.sse_app()
    else:
        mcp_app = mcp.streamable_http_app()

    # Signed access auth in front of the MCP endpoint (fail-closed).
    app = McpAccessAuthMiddleware(mcp_app, protect_path=mount_path)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
