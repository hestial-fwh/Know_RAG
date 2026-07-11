# MCP Reserved

Full MCP Gateway is not implemented in Phase 1.

The project only reserves:

- API route location
- Schema location
- Service location
- Documentation

Reserved routes:

- `GET /api/v1/mcp-placeholder/health`
- `POST /api/v1/mcp-placeholder/search_documents`
- `POST /api/v1/mcp-placeholder/search_faq`
- `POST /api/v1/mcp-placeholder/get_context_pack`
- `GET /api/v1/mcp-placeholder/chunks/{chunk_id}`

Current behavior:

- Routes return placeholder JSON.
- No MCP protocol server is started.
- No cloud-side agent access is enabled.
- No knowledge assets are exposed through MCP.

Future MCP Gateway must enforce local permission checks, audit logs, redaction, context trimming and source tracing before any external caller can retrieve content.
