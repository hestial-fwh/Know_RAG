# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`know_rag` is a local-first RAG knowledge-base construction and maintenance system. All data (files, parsed text, chunks, embeddings, vector indexes) stays local — no cloud APIs by default.

**Stack:** FastAPI (Python 3.11) backend + Vue 3 / Vite / TypeScript frontend, orchestrated with Docker Compose.

## Commands

### Start / Build

```bash
# Full start (first time or after dependency changes)
docker compose up -d --build

# Run migrations (always after backend build)
docker compose exec backend alembic upgrade head

# Rebuild only backend (after code changes)
docker compose build backend && docker compose up -d --force-recreate backend

# Rebuild only frontend
docker compose build frontend && docker compose up -d --force-recreate frontend
```

### Frontend Dev (outside Docker)

```bash
cd frontend
npm run dev              # start dev server on :5174
npm run build            # type-check + build (vue-tsc --noEmit && vite build)
npm run check            # type-check + ESLint + Vitest
```

### Tests

```bash
# Run all tests
docker compose exec backend python -m pytest tests/ -v
docker compose exec backend ruff check app tests

# Run single test file
docker compose exec backend python -m pytest tests/test_chunker.py -v

# Run a specific test
docker compose exec backend python -m pytest tests/test_chunker.py::test_something -v
```

### Database

```bash
# Migration status
docker compose exec backend alembic current

# Auto-generate migration
docker compose exec backend alembic revision --autogenerate -m "description"

# Connect to PostgreSQL
docker compose exec postgres psql -U know_rag -d know_rag
```

### Optional Services

```bash
# With GPU MinerU
docker compose --profile gpu up -d --build

# With Qdrant vector store
docker compose --profile qdrant up -d --build
```

### Access

- Frontend: `http://localhost:5174`
- Backend: `http://localhost:8000/health`
- Default admin: `admin` / `admin123`

## Architecture

### Service Topology

```
Vue + Vite (:5174)  ──HTTP──▶  FastAPI (:8000)  ──▶  PostgreSQL/pgvector (:5434)
                                          │              Redis (:6380)
                                          │              Elasticsearch (:9200)
                                          │              MinIO (:9000)
                                          ├──▶  MinerU CPU (:8001)  — parse service
                                          ├──▶  MinerU GPU (:8002)  — optional, profile=gpu
                                          └──▶  MCP Server (:8010)
                               nginx gateway (:8483)  — unified entry
```

### Backend Structure (`backend/app/`)

| Directory | Purpose |
|-----------|---------|
| `api/` | Route handlers — one file per resource (auth, documents, chunks, knowledge_base, retrieval, chat, etc.) |
| `core/` | `config.py` (pydantic-settings, reads `.env`), `security.py` (JWT), `response.py` (unified `success_response`/`error_response`) |
| `db/` | SQLAlchemy `models.py`, `session.py` (yields `get_db`), `migrations/` (Alembic) |
| `services/` | All business logic — see key services below |
| `integrations/mcp/` | MCP server with streamable-http transport, tool definitions |
| `plugins/` | Extensible plugin system (e.g., `parse_eval`) |
| `schemas/` | Pydantic request/response schemas |

**Key service subdirectories:**

- `services/document_parser/` — Multi-engine parse pipeline: `mineru_adapter.py`, `ragflow_format_adapter.py` (RAGFlow DeepDoc), `pdf_ragflow_advanced.py`, `docx_multimodal.py`, `types.py` (ParsedDocument/ParsedBlock)
- `services/chunker/` — Structure-aware chunking: `heading_splitter.py`, `markdown_splitter.py`, `recursive_splitter.py`, `parent_child_splitter.py`, `weknora_splitter.py`
- `services/embedding/` — Ollama embedding + `embedding_content_builder.py` (title + context header + chunk body)
- `services/storage/` — MinIO object storage
- `services/ocr/` — DeepDoc OCR (identifier, text repair, templates)

**Parser engine priority** (env `PARSER_PRIORITY`): `mineru,ragflow,legacy` — auto-fallback on failure.

### Frontend Structure (`frontend/src/`)

| Directory | Purpose |
|-----------|---------|
| `api/` | Per-resource API modules (auth, documents, chunks, knowledgeBases, retrieval, parseTasks) — all use shared `client.ts` fetch wrapper |
| `views/` | One Vue SFC per route (DashboardView, DocumentDetailView, SearchDebugView, ParseWorkbenchView, etc.) |
| `components/common/` | Shared UI: EmptyState, PageHeader, StatCard, StatusBadge |
| `router/` | Vue Router with `beforeEach` auth guard checking `localStorage.know_rag_token` |
| `layouts/` | MainLayout.vue (sidebar + content area) |

### Key Data Flow

```
Upload → MinIO storage → Parse (MinerU/RAGFlow/Legacy)
  → ParsedDocument (text/table/image blocks)
  → Chunk (structure-aware splitting)
  → Embed (Ollama → pgvector chunk_embeddings)
  → Retrieve (hybrid: vector cosine + Elasticsearch BM25, RRF fusion)
```

### Database

- **PostgreSQL 16 + pgvector** — vector storage (`chunk_embeddings.embedding` with ivfflat index)
- **Elasticsearch 8** — BM25 keyword retrieval (`know_rag_chunks` index)
- **Alembic** — migrations in `backend/app/db/migrations/versions/`
- Key tables: `users`, `knowledge_bases`, `documents`, `chunks`, `chunk_embeddings`, `retrieval_config`, `model_config`, `vector_store_config`, `chunker_config`, `audit_logs`, `parse_tasks`, `parse_task_events`

### Configuration

All config via `.env` (see `.env.example` for all options). Key env vars:

- `DATABASE_URL`, `REDIS_URL` — infrastructure
- `OLLAMA_BASE_URL`, `OLLAMA_EMBEDDING_MODEL` — embedding
- `DOCUMENT_PARSER_ENGINE`, `PARSER_PRIORITY` — parse pipeline
- `HYBRID_FUSION_STRATEGY` — `rrf` (default) or `weighted`
- `KEYWORD_SEARCH_BACKEND` — `elasticsearch` (default)
- `STORAGE_BACKEND` — `minio` (default)
- `CHUNK_STRATEGY` — `auto` / `heading` / `recursive` / `legacy`

### RAGFlow Vendor

`backend/ragflow_vendor/` contains vendored RAGFlow DeepDoc parser/vision code. The `entrypoint.sh` symlinks `/app/ragflow_vendor/rag/res/deepdoc` → `$DEEPDOC_MODEL_DIR` so models are shared via a Docker volume rather than downloaded per container. `PYTHONPATH` includes `/app/ragflow_vendor` for imports.
