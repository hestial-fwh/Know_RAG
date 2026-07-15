# Repository Guidelines

## Project Structure & Module Organization

`know_rag` is a local-first RAG system with a FastAPI backend and Vue 3/Vite frontend. Backend code lives in `backend/app/`: `api/` contains route handlers, `services/` business logic, `schemas/` Pydantic models, and `db/` SQLAlchemy models plus Alembic migrations under `db/migrations/`. Backend tests are in `backend/tests/`. Frontend code lives in `frontend/src/`: `api/` for client modules, `views/` for route pages, `components/` for reusable UI, `router/` for navigation, and `layouts/` for shell layout. Documentation is in `docs/`; deployment and service wiring are in `docker-compose.yml` and `docker/`.

## Build, Test, and Development Commands

- `docker compose up -d --build`: build and start the full local stack.
- `docker compose exec backend alembic upgrade head`: apply database migrations after backend changes.
- `docker compose build backend` then `docker compose up -d --force-recreate backend`: rebuild only the backend.
- `docker compose build frontend` then `docker compose up -d --force-recreate frontend`: rebuild only the frontend.
- `cd frontend; npm run dev`: run the Vite dev server on port `5174`.
- `cd frontend; npm run build`: type-check with `vue-tsc` and build frontend assets.
- `cd frontend; npm run check`: run type checking, ESLint, and Vitest.
- `docker compose exec backend ruff check app tests`: lint backend application and tests.
- `docker compose exec backend python -m pytest tests/ -v`: run backend tests.

## Coding Style & Naming Conventions

Use Python 3.11 style in the backend. `backend/pyproject.toml` configures Ruff with `line-length = 100`; keep imports tidy and functions focused. Use `snake_case` for Python files, functions, and variables. Vue components should use `PascalCase.vue`; API modules and utilities should follow existing local patterns. Keep changes surgical.

## Testing Guidelines

Add or update backend tests in `backend/tests/` for parser, chunking, embedding, retrieval, and persistence behavior. Name files `test_<feature>.py` and test functions `test_<expected_behavior>`. Prefer focused tests for the changed service or route before the full suite.

## Commit & Pull Request Guidelines

No strict commit convention is enforced in this workspace. Use short, imperative subjects such as `Fix chunk filter pagination` or `Add parse task event tests`. PRs should describe the change, list verification commands, mention migrations or environment changes, and include screenshots for UI changes.

## Security & Configuration Tips

Keep secrets in `.env` and use `.env.example` for documented defaults. This project is local-first: do not add cloud APIs, external search, or remote model calls unless explicitly requested. Preserve local storage boundaries for uploaded files, parsed blocks, chunks, embeddings, and vector indexes.

## Documentation Authority

Read `docs/index.md` before relying on phase reports. Current runtime behavior is documented in
`docs/architecture.md` and `know_rag_目录结构说明_最新维护版.md`; files named `phase_*`, reports,
and repair notes are historical context. When changing a main flow, API contract, security boundary,
or directory responsibility, update the corresponding Current document in the same change.
