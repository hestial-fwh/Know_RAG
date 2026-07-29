# Backend Module Map — Know_RAG

> 状态：Current
> 维护日期：2026-07-24
> 行数为近似值；生产入口以调用关系和测试为准。

## Document Parse Flow

```
upload_document (document_service.py)
  └─> parse_document (document_service.py)  [889 lines, 10-stage pipeline]
        ├─> resolve_parser_engine (parser_service.py)
        ├─> parse_local_document (parser_service.py → ragflow_adapter / mineru_adapter)
        ├─> PDF read-only page profile → optional selected page render / enrichment
        ├─> enhance_ocr_blocks (vision_service.py)
        ├─> enhance_image_blocks (vision_service.py)
        ├─> reflow_parsed_document (document_parser/block_reflow.py)
        ├─> rebuild_document_chunks (chunk_service.py → chunking/ modules)
        └─> embed_document (embedding_service.py → embedding/ modules)
```

**Modules:**
| Module | File | Lines | Responsibilities |
|--------|------|-------|-----------------|
| Document lifecycle | `document_service.py` | 889 | Upload, parse orchestration, delete, status |
| Parser router | `parser_service.py` | 210 | Engine selection (RAGFlow / MinerU), fallback |
| RAGFlow adapter | `document_parser/ragflow_format_adapter.py` | 743 | Multi-format parsing via RAGFlow vendor |
| MinerU adapter | `document_parser/mineru_adapter.py` | 1218 | External MinerU API adapter |
| Page visual profiler | `document_parser/page_visual_profile.py` | — | 始终采集 PDF facts、dominant-image 与 parser coverage 的纯只读判定 |
| Selected page renderer | `document_parser/pdf_page_renderer.py` | — | 仅渲染选中的 PDF 页面并保存到现有媒体存储 |
| Page visual enricher | `document_parser/page_visual_enricher.py` | — | 缺失页面主视觉注入、已有整页图标记、去重统计和 fail-open |
| Document profiler | `document_parser/document_profiler.py` | — | Canonical provenance/asset/page facts → 确定性文档与逐页画像（shadow） |
| Block reflow | `document_parser/block_reflow.py` | — | Layout-aware block reordering、视觉页 section scope 与普通正文跨页标题继承 |

## Chunk Rebuild Flow

```
rebuild_document_chunks (chunk_service.py)
  ├─> make_structured_sections (chunking/section_builder.py)    [NEW]
  │     ├─> is_heading_block, is_noise_block, is_equation_block
  │     ├─> block_parent_text, block_context_title
  │     └─> normalized_child_type, heading_level
  ├─> merge_short_text_around_code_blocks (chunking/code_context_merger.py) [NEW]
  │     ├─> is_code_block_block, is_fenced_code_text
  │     ├─> looks_like_code_lead_in, looks_like_code_tail_note
  │     └─> compose_code_block_content
  ├─> build_parent_content (chunking/parent_builder.py)         [NEW]
  ├─> build_parent_metadata (chunking/metadata_builder.py)      [NEW]
  ├─> build_child_metadata (chunking/metadata_builder.py)       [NEW]
  └─> split_document (chunker/ → text_unit, recursive_splitter, ...)
```

**Modules:**
| Module | File | Lines | Responsibilities |
|--------|------|-------|-----------------|
| Chunk orchestration | `chunk_service.py` | 2118 | CRUD, rebuild orchestration, DB persistence |
| Section builder | `chunking/section_builder.py` | 473 | ParsedBlock[] → SectionDraft[] (NEW, pure) |
| Code merger | `chunking/code_context_merger.py` | 210 | Short text → code block merge (NEW, pure) |
| Parent builder | `chunking/parent_builder.py` | 52 | Section → parent content (NEW, pure) |
| Metadata builder | `chunking/metadata_builder.py` | 244 | Unified metadata/flags (NEW, pure) |
| Chunk drafts | `chunking/chunk_draft.py` | 96 | Pure dataclasses (NEW) |
| Low-level splitting | `chunker/` (13 files) | ~4500 total | Recursive split, markdown split, heading split, text_unit, boundary utils |
| Chunk policy resolver | `chunker/chunk_policy_resolver.py` | — | PageProfile → versioned ResolvedChunkPolicy；标记 DCP-3 active/shadow |

## Embedding Flow

```
embed_document / embed_chunks (embedding_service.py)
  ├─> build_content_admission (embedding/embedding_content_builder.py)
  │     ├─> _content_channels_from_chunk (native/parser/caption/VLM/OCR/table)
  │     ├─> field-level index_sources / shadow_sources
  │     ├─> _semantic_body_from_structure_text (directory tree)
  │     └─> shared embedding/BM25 final content
  ├─> embed_text (embedding_service.py)
  │     ├─> _embed_text_ollama
  │     └─> _embed_text_openai_compatible
  ├─> _upsert_embedding (embedding_service.py → vector_store_adapters)
  └─> sync_document_index (keyword_index_service.py)
```

**Modules:**
| Module | File | Lines | Responsibilities |
|--------|------|-------|-----------------|
| Embedding facade | `embedding_service.py` | 609 | Runtime management, batch orchestration, DB |
| Content admission | `embedding/embedding_content_builder.py` | — | Chunk 字段来源 → 统一 embedding/BM25 文本与可解释诊断（纯逻辑） |
| Keyword index | `keyword_index_service.py` | — | 消费共享准入结果，写入 Elasticsearch 并刷新修复后的文档 |
| Vector store | `vector_store_adapters.py` | — | pgvector / Qdrant / Milvus upsert |

## Retrieval Flow

```
hybrid_search (retrieval_service.py)
  ├─> normalize_options (config merge)
  ├─> vector_search + keyword_search
  ├─> merge_results_rrf / merge_results_weighted (fusion)
  ├─> document_relation_expansion_service.py (flagged scoped relation expansion)
  ├─> attach_context (parent window + neighbor preview)
  └─> build_parent_groups (parent grouping)
```

**Modules:**
| Module | File | Lines | Responsibilities |
|--------|------|-------|-----------------|
| Retrieval service | `retrieval_service.py` | 1255 | Search, fusion, parent resolution, formatting |
| Relation materializer | `document_relation/materializer.py` | — | Pure occurrence carrier selection and bilateral metadata |
| Relation graph models | `document_relation/models.py` | — | Versioned edge/group/graph schema, validation and stable IDs |
| Relation graph merger | `document_relation/relation_graph_merger.py` | — | caption/footnote/explicit merge, stable order and legacy adapter |
| Page layout graph | `document_relation/page_layout_graph.py` | — | Authoritative/derived page extent and normalized page nodes |
| Asset group resolver | `document_relation/asset_group_resolver.py` | — | Same-page horizontal/vertical groups and member edges |
| Shared context resolver | `document_relation/shared_context_resolver.py` | — | Top shared text candidates, caption/section/geometry guards |
| Relation persistence | `document_relation_persistence_service.py` | — | Artifact graph ORM write and dual-read fallback |
| Relation expansion | `document_relation_expansion_service.py` | — | Ordered per-hit budgets, KB/doc scope and fail-open DB read |

## LLM Context Flow

```
build_knowledge_context (context/knowledge_context_service.py)
  └─> build_cloud_flat_chunks_payload (llm_context_builder.py)
        ├─> _cloud_collect_hits
        ├─> _cloud_build_window_for_parent (parent window)
        ├─> _cloud_flat_chunk (source formatting)
        └─> return {chunks, context_text}
```

**Modules:**
| Module | File | Lines | Responsibilities |
|--------|------|-------|-----------------|
| Context service | `context/knowledge_context_service.py` | — | Stable API boundary for MCP/Agent |
| Context builder | `llm_context_builder.py` | 1631 | v2 legacy + v4 cloud payload builders |

## Key Architectural Patterns

1. **chunker/ vs chunking/**: `chunker/` handles low-level text splitting (sentence boundaries, markdown, headings). `chunking/` handles high-level structured chunk construction from `ParsedBlock[]` (sections, parents, children, metadata).

2. **Pure vs DB-dependent**: Files under `chunking/`, `embedding/embedding_content_builder.py`, and `content_cleaning.py` are pure logic with no database dependency. They can be unit-tested with mock inputs.

3. **Facade pattern**: `chunk_service.py` and `embedding_service.py` are transitioning to thin facades that delegate to subpackage modules while preserving backward-compatible function signatures.

4. **Dataclass bridge**: `ChunkDraft`, `ParentDraft`, `ChildDraft` act as a bridge between pure logic and SQLAlchemy ORM objects. Pure logic produces drafts; persistence layer converts them to DB rows.
# Backend module map update: chunking phase 2

## Chunk rebuild modules

- `app/services/chunk_service.py`
  - Compatibility facade for chunk APIs and legacy chunk rebuild behavior.
  - Delegates structured parent-child rebuilds to
    `app/services/chunking/rebuild_service.py`.

- `app/services/chunking/rebuild_service.py`
  - Owns the structured parent-child rebuild pipeline.
  - Calls section, code-context, parent, child, linker, and persistence modules.

- `app/services/chunking/chunk_draft.py`
  - Pure draft dataclasses. No SQLAlchemy objects are stored here.

- `app/services/chunking/metadata_builder.py`
  - Unified metadata/flags construction for parent and child chunk drafts.

- `app/services/chunking/section_builder.py`
  - Groups parser blocks into semantic sections.

- `app/services/chunking/code_context_merger.py`
  - Merges short lead-in/tail-note text around code blocks before chunking.

- `app/services/chunking/parent_builder.py`
  - Builds parent content and `ParentDraft`.

- `app/services/chunking/child_builder.py`
  - Builds text, structure-like text, equation, code, table, image, and OCR
    children as `ChildDraft`.

- `app/services/chunking/table_chunk_builder.py`
  - Table-specific child construction.

- `app/services/chunking/image_chunk_builder.py`
  - Image and `image_ocr_text` child construction.

- `app/services/chunking/chunk_linker.py`
  - Draft ref linking for parent-child and prev/next relationships.

- `app/services/chunking/chunk_persistence.py`
  - Converts drafts into SQLAlchemy `Chunk` rows and persists them.
  - This is the only new chunking module that creates `Chunk(...)`.

## Remaining large services

The following modules still need future responsibility slicing:

- `document_service`
- `llm_context_builder`
- `retrieval_service`
### DCP-0.1 工程数据集

- `backend/scripts/document_profile_dataset/adversarial_templates.py`：30 个对抗/扰动页面与
  `VI-01`～`VI-10` 布局 gold。
- `generator.py`：生成 PDF、PNG、layout、MinerU-shaped raw 和 gold。
- `replay.py`：执行 source PDF → MinerU adapter → page visual → Canonical → Chunk 六层回放。
- `report.py`：保持旧 11 页生产门禁，另输出对抗 case/gold/actual/diff 的 JSON/HTML 报告。
- `app/plugins/parse_eval/service.py`：只读暴露已生成报告，不在 API 请求中执行生成器。
