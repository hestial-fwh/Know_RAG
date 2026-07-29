---
name: doc-maintenance
description: Proactive documentation maintenance after each development phase. Use when a feature, fix, or refactoring task is completed to update docs/index.md, docs/architecture.md, know_rag_目录结构说明_最新维护版.md, CLAUDE.md, and other affected docs.
---

# Documentation Maintenance

After every non-trivial development phase completes, proactively maintain documentation. This is not a separate step to ask about — it should be done as the final step of the development work itself.

## When to Trigger

Any of these signals mean documentation needs attention:

- A new config flag (env var) was added → `.env.example` + `docs/architecture.md` + directory structure doc
- A new function/module was added to the main pipeline → directory structure doc call tree + `docs/architecture.md`
- API response fields changed → `docs/api.md` + frontend types
- A new skip/guard/classification rule was added → `docs/architecture.md` + directory structure doc
- Behavior of an existing feature changed → `docs/architecture.md` + any affected plan docs
- A security boundary changed → `docs/security.md` + `.env.example`
- A new chunk type, metadata key, or flag was introduced → directory structure doc

## What to Update

### Always: `docs/index.md`
- Bump the maintenance date to today.

### High Priority: `docs/architecture.md`
- If the main data flow, chunking pipeline, embedding/retrieval logic, or any described subsystem changed, update the relevant paragraph.
- Match the existing style: concise, declarative, no marketing language.

### High Priority: `know_rag_目录结构说明_最新维护版.md`
- Add new items describing what changed, referencing the key files and functions.
- Update the call tree section if a new function was inserted into the pipeline.
- Update the "代码基线" commit hash and date at the top.

### Conditional: `CLAUDE.md`
- If a new common command, build step, or test invocation was added.
- If a new service subdirectory or major module was created.
- Keep it brief — CLAUDE.md is loaded every session.

### Conditional: Plan Docs (`docs/*.md`)
- If a plan doc's phase status changed (e.g., "PASS" → "FAIL", "Draft" → "Current"), update the status in both the plan doc itself and `docs/index.md`'s description of it.

### Conditional: `.env.example`
- If a new config variable was added to `backend/app/core/config.py`.

### Conditional: `docs/api.md`
- If API response fields, status values, or query parameters changed.

## What NOT to Do

- Do NOT create new one-time analysis/incident reports in `docs/`. Those belong in git commit messages.
- Do NOT delete existing docs unless they are provably superseded or redundant.
- Do NOT rewrite docs from scratch — make surgical updates.
- Do NOT update docs for trivial changes (typo fix, formatting, single-line log change).

## Post-Update Check

After updating docs, briefly confirm in conversation:
1. Which docs were updated
2. What changed in each
3. Whether `docs/index.md` date was bumped
