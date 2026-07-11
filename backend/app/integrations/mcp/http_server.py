"""Remote Know_RAG MCP server.

This starts the same Know_RAG MCP tools through Streamable HTTP transport.

Run:
    python -m app.integrations.mcp.http_server

Default endpoint:
    http://0.0.0.0:8010/mcp

Environment variables:
    MCP_TRANSPORT=streamable-http
    MCP_MOUNT_PATH=/mcp
    MCP_HOST=0.0.0.0
    MCP_PORT=8010

Know_RAG API variables used by http_client.py:
    KNOW_RAG_API_BASE=http://backend:8000
    KNOW_RAG_API_TOKEN=<optional JWT>
    KNOW_RAG_USERNAME=admin
    KNOW_RAG_PASSWORD=admin123
"""

import os

from app.integrations.mcp.server import mcp


def main() -> None:
    transport = (os.getenv("MCP_TRANSPORT") or "streamable-http").strip().lower()
    mount_path = (os.getenv("MCP_MOUNT_PATH") or "/mcp").strip() or "/mcp"

    if transport not in {"streamable-http", "sse"}:
        transport = "streamable-http"

    mcp.run(
        transport=transport,
        mount_path=mount_path,
    )


if __name__ == "__main__":
    main()