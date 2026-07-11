# Know_RAG MCP Adapter

This is the first read-only MCP adapter for Know_RAG.

It exposes two tools:

- `know_rag_list_knowledge_bases`
- `know_rag_search_context`

The MCP adapter is intentionally decoupled from retrieval internals. It calls the
existing Know_RAG HTTP API:

- `GET /api/v1/knowledge-bases`
- `POST /api/v1/knowledge-bases/{kb_id}/llm-context`

So MCP uses the same `KnowledgeContextService` and `llm_context_builder.py` as
the frontend Search Debug page.

## Environment

When running on the host machine:

```bash
set KNOW_RAG_API_BASE=http://127.0.0.1:8000
set KNOW_RAG_USERNAME=admin
set KNOW_RAG_PASSWORD=admin123
```

You may also use an existing token:

```bash
set KNOW_RAG_API_TOKEN=<jwt>
```

If both token and username/password are present, token is used first; on 401 the
client will login and retry.

## Start stdio MCP server

From `backend` with `PYTHONPATH` pointing to the app root:

```bash
cd backend
python -m app.integrations.mcp.server
```

In Docker backend container:

```bash
docker exec -it know-rag-backend python -m app.integrations.mcp.server
```

## MCP Inspector

After installing MCP Inspector, use this command:

```bash
python -m app.integrations.mcp.server
```

Suggested test payload:

```json
{
  "query_text": "退移速率曲线说明了什么？",
  "knowledge_base_id": "你的知识库ID",
  "match_count": 10,
  "include_parent_context": true,
  "parent_context_max_chars": 2000,
  "parent_children_limit": 10,
  "max_parent_contexts": 5,
  "max_context_chars": 8000,
  "max_sources": 8,
  "include_images": true,
  "include_image_urls": false
}
```

`include_image_urls` defaults to `false` to avoid exposing local object-storage
URLs to remote models unless explicitly enabled.
