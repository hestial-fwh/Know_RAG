# Backend Refactoring Plan — Know_RAG

## 1. Current Problems Overview

### 1.1 Top 10 Longest Python Files

| Rank | File | Lines | Status |
|------|------|-------|--------|
| 1 | `services/llm_context_builder.py` | 3876 | ~2244 lines commented-out dead code |
| 2 | `services/chunk_service.py` | 2119 | 67 functions, all module-level, mixes DB + pure logic |
| 3 | `services/retrieval_service.py` | 1255 | Hybrid retrieval, RRF fusion, parent resolution, formatting |
| 4 | `services/document_parser/mineru_adapter.py` | 1218 | External parser adapter (leave as-is) |
| 5 | `services/document_service.py` | 889 | Parse pipeline orchestration + status tracking |
| 6 | `services/document_parser/pdf_ragflow_advanced.py` | 848 | PDF parser (leave as-is) |
| 7 | `services/document_parser/ragflow_format_adapter.py` | 743 | Multi-format adapter (leave as-is) |
| 8 | `services/embedding_service.py` | 684 | Content building + embedding calls + DB persistence |
| 9 | `services/document_parser/deepdoc_runtime.py` | 657 | ONNX runtime (leave as-is) |
| 10 | `services/chunker/text_unit.py` | 653 | Recently refactored — good shape |

### 1.2 Key Issues

1. **llm_context_builder.py is 58% dead code**: ~2244 of 3876 lines are commented-out legacy v3 code. Only ~1630 lines are active (v2 + v4 builders).

2. **chunk_service.py (2119 lines)**: 67 module-level functions mixing:
   - Pure text/normalization logic
   - ParsedBlock processing (section building, parent/child construction)
   - Metadata dict construction (scattered across 5+ functions)
   - Code block context merging (2-phase: block-level + chunk-level)
   - DB operations (CRUD, neighbor linking, transaction management)
   - Image/table/equation/OCR chunk construction
   - No classes — all module-level functions

3. **embedding_service.py (684 lines)**: Mixes 4 distinct concerns:
   - Content building (`build_embedding_content` + 4 helpers)
   - Model runtime management (`get_embedding_runtime`, `EmbeddingRuntime`)
   - Embedding API calls (`_embed_text_ollama`, `_embed_text_openai_compatible`)
   - DB persistence (`_upsert_embedding`, `embed_chunks`, `embed_document`, `embed_knowledge_base`)

4. **retrieval_service.py (1255 lines)**: 32 functions mixing:
   - Query normalization + config merging
   - Vector search + keyword search
   - RRF/weighted fusion
   - Parent window context attachment
   - Result formatting

5. **document_service.py (889 lines)**: `parse_document()` is a 540-line function with 10 pipeline stages.

### 1.3 Functions That Are Too Long

| Function | File | Approx Lines |
|----------|------|-------------|
| `_rebuild_document_chunks_structured_parent_child` | chunk_service.py | ~509 |
| `parse_document` | document_service.py | ~540 |
| `build_cloud_flat_chunks_payload` | llm_context_builder.py | ~145 |
| `_cloud_build_window_for_parent` | llm_context_builder.py | ~155 |
| `hybrid_search` | retrieval_service.py | ~92 |
| `build_llm_context_payload` | llm_context_builder.py | ~280 |
| `_post_process_orphan_short_text_chunks` | chunk_service.py | ~81 |

### 1.4 Pure Logic vs DB-Dependent

- **Pure logic candidates** (no DB, testable in isolation):
  - `build_embedding_content()` + helpers in embedding_service.py
  - Most `_block_*` helpers in chunk_service.py
  - `_make_structured_sections()`, `_build_parent_content()`, `_structured_block_meta()`
  - Code block merging logic
  - OCR/text normalization helpers
  - Content cleaning (already in content_cleaning.py)

- **DB-dependent** (must stay in service layer):
  - CRUD operations (`list_chunks`, `get_chunk`, `set_chunk_enabled`, `delete_chunk`)
  - `rebuild_document_chunks()` (orchestrates delete + create + link)
  - `embed_document()` / `embed_knowledge_base()` (transaction management)
  - `hybrid_search()` (query execution)

## 2. Target Module Structure

```
app/services/
├── chunker/              # EXISTING — low-level splitting (text_unit, recursive_splitter, etc.)
├── chunk_service.py      # → thin facade, delegates to chunking/
├── chunking/             # NEW — structured chunk construction from ParsedBlock
│   ├── __init__.py
│   ├── chunk_draft.py          # ChunkDraft, ParentDraft, ChildDraft (pure dataclasses)
│   ├── metadata_builder.py     # Unified metadata/flags construction
│   ├── section_builder.py      # ParsedBlock → SectionDraft
│   ├── parent_builder.py       # SectionDraft → parent chunk draft
│   ├── child_builder.py        # SectionDraft → child chunk drafts (text/table/image/equation/code)
│   ├── code_context_merger.py  # Short text → code block merge (ParsedBlock phase)
│   ├── chunk_linker.py         # prev/next/parent-child wiring
│   └── chunk_persistence.py    # ChunkDraft → SQLAlchemy Chunk
├── context/              # EXISTING — knowledge_context_service
├── document_parser/      # EXISTING — leave as-is
├── embedding/            # NEW — embedding concerns
│   ├── __init__.py
│   ├── embedding_content_builder.py  # build_embedding_content (pure, no DB)
│   ├── embedding_runner.py           # embed_text, embed_chunks (model calls)
│   ├── embedding_persistence.py      # ChunkEmbedding upsert, vector store sync
│   └── embedding_batcher.py          # Batch/retry logic
├── retrieval/            # NEW (optional, lower priority)
│   ├── parent_resolver.py           # attach_context, parent window logic
│   └── result_formatter.py          # _row_to_result, preview builders
├── parsing/              # NEW (optional)
│   ├── parse_pipeline.py            # parse_document pipeline stages
│   └── parse_progress.py            # Task event/progress reporting
├── llm_context_builder.py  # → clean up dead code only
├── embedding_service.py    # → thin facade
├── retrieval_service.py    # → thin facade
├── document_service.py     # → thin facade
└── parser_service.py       # EXISTING — already well-structured (210 lines)
```

## 3. Risk Points

1. **Backward compatibility**: All API routes must keep the same response shape.
2. **Parent-child logic**: Must not break. The `_rebuild_document_chunks_structured_parent_child` function is complex (~509 lines) with many edge cases.
3. **OCR/VLM enhancement**: The parse pipeline has conditional enhancement stages; any reordering could break behavior.
4. **Chunk linking**: Three parallel linked-list views (`{"text"}`, `{"parent_text"}`, combined) must remain correct.
5. **No database migration**: Must not change table schemas.
6. **No new heavy dependencies**: Keep using existing dataclasses + TypedDicts.

## 4. Completed Work (2026-07-02)

### Phase 1: Dead Code Removal ✅
- `llm_context_builder.py`: 3876 → 1631 lines (removed 2245 lines of commented-out v3 legacy code)

### Phase 2: Chunking Subpackage ✅
- Created `app/services/chunking/` with:
  - `chunk_draft.py` (96 lines) — pure dataclasses
  - `metadata_builder.py` (244 lines) — unified metadata/flags construction
  - `section_builder.py` (473 lines) — **real logic migrated**: `make_structured_sections()`, block-type classifiers, block parent text, page helpers
  - `code_context_merger.py` (210 lines) — **real logic migrated**: code block detection, merging, lead-in/tail-note
  - `parent_builder.py` (52 lines) — **real logic migrated**: `build_parent_content()`
  - `__init__.py` — package exports
- `chunk_service.py`: Added delegation imports from chunking/ modules for block classifiers, code detection, and section building
- **Total extracted**: 1075 lines of pure logic in chunking/ subpackage

### Phase 3: Embedding Subpackage ✅
- Created `app/services/embedding/` with:
  - `embedding_content_builder.py` (209 lines) — `build_embedding_content()` + 5 helpers, pure logic
  - `__init__.py` — package exports
- `embedding_service.py`: 684 → 609 lines, delegates to `embedding/embedding_content_builder`

## 5. Phased Plan (Remaining)

### Phase 1: Dead Code Removal (LOW RISK)

**Target**: `llm_context_builder.py`

- Remove ~2244 lines of commented-out v3 legacy code (lines 1-2244)
- Result: ~1630 lines of active code only
- Verification: no imports reference anything in the deleted block

### Phase 2: Split chunk_service.py (MEDIUM RISK)

**Step 1**: Extract `chunk_draft.py` — pure dataclasses:
- `ChunkDraft(content, chunk_type, start_at, end_at, parent_chunk_id, context_header, metadata, flags, image_info)`
- `ParentDraft(content, context_header, metadata, flags, section_index)`
- `ChildDraft(content, chunk_type, ...)`

**Step 2**: Extract `metadata_builder.py` — unified metadata construction:
- `build_parent_metadata(section, ...)` → dict
- `build_child_metadata(block, parent_id, ...)` → dict
- `build_chunk_flags(...)` → dict

**Step 3**: Extract `section_builder.py`:
- `make_structured_sections(blocks, file_type, cfg)` → list[SectionDraft]

**Step 4**: Extract `parent_builder.py`:
- `build_parent_content(section, file_type)` → str
- `build_parent_draft(section, file_type)` → ParentDraft

**Step 5**: Extract `child_builder.py`:
- `build_text_child(block, parent_id, cfg)` → ChildDraft
- `build_code_child(block, parent_id)` → ChildDraft
- `build_table_child(block, parent_id)` → ChildDraft
- `build_image_child(block, parent_id)` → ChildDraft
- `build_equation_child(block, parent_id)` → ChildDraft

**Step 6**: Extract `code_context_merger.py`:
- `merge_short_text_around_code_blocks(blocks)` → list[ParsedBlock]

**Step 7**: Extract `chunk_persistence.py`:
- `persist_chunks(db, document, drafts)` → list[Chunk]
- `delete_document_chunks(db, document_id)` → None

**Step 8**: Trim `chunk_service.py` → thin facade:
- Keep `rebuild_document_chunks()` as orchestrator
- Keep CRUD functions
- Delegate to chunking/ modules

### Phase 3: Clean embedding_service.py (MEDIUM RISK)

- Extract `build_embedding_content()` and its 4 helpers into `embedding/embedding_content_builder.py`
- `embedding_service.py` becomes a thin facade importing from `embedding/`

### Phase 4: Clean document_service.py (HIGHER RISK, optional)

- Extract `parse_document()` pipeline stages into `parsing/parse_pipeline.py`
- Extract progress/event reporting into `parsing/parse_progress.py`

### Phase 5: Clean retrieval_service.py (HIGHER RISK, optional)

- Extract parent window logic into `retrieval/parent_resolver.py`
- Extract result formatting into `retrieval/result_formatter.py`

## 5. Verification Per Phase

| Phase | Tests to Run |
|-------|-------------|
| 1 | All existing tests (dead code removal should change nothing) |
| 2 | `test_chunker.py`, `test_chunk_boundary_quality.py`, `test_context_header_continuation.py`, `test_markdown_code_context_merge.py` |
| 3 | `test_embedding_content.py` (if exists), manual embedding verification |
| 4 | `test_parse_quality_fix.py`, full document upload + parse flow |
| 5 | `test_retrieval_mapping.py`, `test_rrf_fusion.py`, `test_hybrid_result_mapping.py` |

## 6. Execution Order

1. **Write audit plan** (this document) ✅
2. **Phase 1**: Remove dead code from llm_context_builder.py
3. **Phase 2**: Split chunk_service.py (steps 1-8, one step at a time)
4. **Phase 3**: Extract embedding content builder
5. **Phase 4**: (Optional) Clean document_service.py
6. **Phase 5**: (Optional) Clean retrieval_service.py

Each step should be a small, reviewable commit that doesn't change behavior.
# Chunk rebuild phase 2 refactor

## Old flow

Before this phase, `app/services/chunk_service.py` still owned the structured
parent-child rebuild path end to end: section grouping, code-context merging,
parent construction, child construction, metadata assembly, DB `Chunk(...)`
creation, parent-child links, prev/next links, persistence, and keyword
indexing orchestration all lived in one 2000+ line service module.

## New flow

The structured parent-child path is now orchestrated by
`app/services/chunking/rebuild_service.py`:

```text
ParsedBlock[]
  -> SectionBuilder.make_structured_sections(...)
  -> CodeContextMerger.merge_short_text_around_code_blocks(...)
  -> ParentBuilder.build_parent(...)
  -> ChildBuilder.build_children(...)
  -> ChunkLinker.link_parent_child_and_neighbors(...)
  -> ChunkPersistence.persist(...)
  -> ChunkRebuildService.rebuild_document_chunks(...)
```

`chunk_service.py` remains the compatible public entry point. When parent-child
chunking is enabled, it delegates to `ChunkRebuildService`; the legacy
non-parent-child path remains in place to avoid behavior changes.

## New module responsibilities

- `chunk_draft.py`: pure `ChunkDraft`, `ParentDraft`, `ChildDraft`, and
  `SectionDraft` dataclasses used before persistence.
- `metadata_builder.py`: central metadata/flags construction helpers for
  parent, text, table, image, OCR, equation, and code-block chunks.
- `parent_builder.py`: parent content and `ParentDraft` construction.
- `child_builder.py`: text, equation, code-block, structure-like text, table,
  image, and image OCR child draft construction.
- `table_chunk_builder.py`: table child draft construction.
- `image_chunk_builder.py`: image and OCR child draft construction.
- `chunk_linker.py`: draft-level parent/child and prev/next references.
- `chunk_persistence.py`: the only new module that creates SQLAlchemy
  `Chunk(...)` rows.
- `rebuild_service.py`: structured rebuild orchestration, persistence, keyword
  indexing, and knowledge-base chunk-count update.

## Still pending

The legacy non-parent-child branch in `chunk_service.py` is intentionally left
in place. Later backend refactors can apply the same facade pattern to
`document_service`, `llm_context_builder`, and `retrieval_service`.
