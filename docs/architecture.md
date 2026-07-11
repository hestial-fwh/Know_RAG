# Architecture

`know_rag` is a local-first RAG knowledge-base construction and maintenance system.

```text
Vue + Vite frontend
  |
  | HTTP /api/v1
  v
FastAPI backend
  |
  | local network only
  +-- PostgreSQL / pgvector / optional ParadeDB
  +-- Redis for future task queue
  +-- Ollama on host: http://host.docker.internal:11434
  +-- docreader service in a later phase
```

## Backend

The backend is Python FastAPI. It owns authentication, knowledge-base management, document metadata, parsing orchestration, chunk management, embedding, retrieval and local QA.

Phase 1 only implements route skeletons and placeholder services. The route layout is intentionally close to the migration plan so the frontend can stabilize while real services are added.

## Frontend

The frontend is Vue + Vite. Phase 1 includes page entries for the maintenance workflow:

- Dashboard
- Login
- Knowledge base list/detail/create/edit
- Document upload/list/detail
- Chunk list/detail
- FAQ management
- Model, vector store and retrieval settings
- Local search and QA debug
- MCP reserved page

## Data Boundary

All knowledge assets remain local:

- Original files
- Parsed text
- Chunks
- Embeddings
- Vector indexes
- FAQ answers
- QA references

## MCP Position

MCP Gateway is not implemented in Phase 1. The codebase reserves API routes, services, schemas and documentation so it can later expose controlled recall to a cloud-side MCP Client.
