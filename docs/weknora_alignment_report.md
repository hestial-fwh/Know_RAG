# WeKnora 对齐分析报告

## 1. 分析范围

本报告只对比 WeKnora 原始实现与 know_rag 当前实现中以下链路：

- 文档上传与解析
- chunk 切分策略
- chunk 数据模型与入库
- embedding 生成与向量/关键词索引入库
- hybrid-search 请求、融合逻辑与返回字段

本次只阅读代码并输出方案，没有修改业务代码。

主要阅读文件：

- WeKnora：`internal/handler/knowledge.go`、`internal/application/service/knowledge_create.go`、`internal/application/service/knowledge_process.go`
- WeKnora：`internal/infrastructure/docparser/*`、`docreader/proto/docreader.proto`、`docreader/main.py`
- WeKnora：`internal/infrastructure/chunker/*`、`internal/types/chunk.go`、`internal/types/docparser.go`、`internal/types/knowledge.go`、`internal/types/knowledgebase.go`
- WeKnora：`migrations/versioned/000000_init.up.sql`、`000001_agent.up.sql`、`000002_embeddings.up.sql`、`000003_chunk_flags.up.sql`
- WeKnora：`internal/application/service/knowledgebase_search*.go`、`internal/application/repository/retriever/postgres/*`
- know_rag：`backend/app/services/parser_service.py`、`chunk_service.py`、`document_service.py`、`embedding_service.py`、`retrieval_service.py`
- know_rag：`backend/app/db/models.py`、`backend/app/db/migrations/versions/*.py`

## 2. WeKnora 原始链路

### 2.1 文档上传与解析

WeKnora 的文件上传入口是：

- 路由：`POST /api/v1/knowledge-bases/:id/knowledge/file`
- 注册位置：`internal/router/router.go`
- Handler：`internal/handler/knowledge.go` 的 `CreateKnowledgeFromFile`
- Service：`internal/application/service/knowledge_create.go` 的 `CreateKnowledgeFromFile`

上传后，WeKnora 先创建 `knowledges` 记录，再保存原始文件，然后投递 `TypeDocumentProcess` 异步任务。`knowledge_create.go` 中可以看到：

- 创建 `types.Knowledge`
- `ParseStatus: "pending"`
- `EnableStatus: "disabled"`
- 保存文件到 KB 对应的 storage service
- 构造 `types.DocumentProcessPayload`
- 使用 Asynq 入队处理

解析阶段在 `internal/application/service/knowledge_process.go` 中执行。`convert()` 会根据 `kb.ChunkingConfig.ResolveParserEngine(fileType)` 选择解析引擎，并通过 `resolveDocReader()` 得到 DocumentReader。WeKnora 支持 gRPC 与 HTTP docreader：

- gRPC：`internal/infrastructure/docparser/grpc_parser.go`
- HTTP：`internal/infrastructure/docparser/http_parser.go`

docreader 协议由 `docreader/proto/docreader.proto` 定义：

- `ReadRequest`：`file_content`、`file_name`、`file_type`、`url`、`title`、`config`、`request_id`
- `ReadResponse`：`markdown_content`、`image_refs`、`image_dir_path`、`metadata`、`error`
- `ImageRef`：`filename`、`original_ref`、`mime_type`、`storage_key`、`image_data`

因此 WeKnora 的解析输出主干是 Markdown，而不是纯文本。`docreader/main.py` 将 parser 返回的 `result.content` 写入 `markdown_content`，同时返回 `image_refs` 与 `metadata`。

metadata 情况：

- docreader 协议显式返回 `map<string,string> metadata`。
- `docreader/parser/docx_parser.py` 内部保留 page、section、table、image 处理逻辑，但最终主链路暴露给 Go 后端的是 Markdown、图片引用和 metadata map。
- WeKnora 的 chunk 表不直接有 `page` 或 `bbox` 字段；它主要保留 `start_at/end_at`、`image_info`、`metadata`、父子/前后关系字段。页码类信息若存在，应通过 parser metadata 或 image info 扩展承载，而不是固定列。

### 2.2 chunk 切分策略

WeKnora 不是简单固定字符切分。

相关文件：

- `internal/infrastructure/chunker/splitter.go`
- `internal/infrastructure/chunker/strategy.go`
- `internal/infrastructure/chunker/heading_splitter.go`
- `internal/infrastructure/chunker/heuristic_splitter.go`
- `internal/types/knowledgebase.go`

WeKnora `ChunkingConfig` 包含：

- `chunk_size`
- `chunk_overlap`
- `separators`
- `parser_engine_rules`
- `enable_parent_child`
- `parent_chunk_size`
- `child_chunk_size`
- `strategy`
- `token_limit`
- `languages`

默认值在 `splitter.go` 中定义：

- `DefaultChunkSize = 512`
- `DefaultChunkOverlap = 80`
- 默认 separators：空行、换行、句号类分隔符

切分策略由 `strategy.go` 控制：

- `auto`
- `heading`
- `heuristic`
- `recursive`
- `legacy`

`Split()` 会根据文档 profile 选择 strategy，失败后 fallback 到 legacy splitter。legacy splitter 也不是裸字符切分，而是：

- 使用 separators 递归切分；
- 保护 Markdown 图片、链接、表格、代码块、LaTeX 公式等 protected patterns；
- merge 时按 chunk_size 和 overlap 合并；
- 保留 `Seq`、`Start`、`End`；
- 保留 `ContextHeader`，用于 embedding/retrieval 时补充标题面包屑。

WeKnora 还支持 parent-child chunk：

- `SplitParentChild()`
- parent chunk 作为较大上下文窗口；
- child chunk 用于 embedding/retrieval；
- child 通过 `ParentIndex` 关联 parent；
- 入库后 child 的 `parent_chunk_id` 指向 parent chunk。

### 2.3 chunk 数据模型和入库

WeKnora 的文档实体叫 `knowledges`，不是 `documents`。chunk 关联字段是 `knowledge_id`，不是 `document_id`。

迁移文件 `migrations/versioned/000000_init.up.sql` 定义：

`knowledges` 关键字段：

- `id`
- `tenant_id`
- `knowledge_base_id`
- `type`
- `title`
- `description`
- `source`
- `parse_status`
- `enable_status`
- `embedding_model_id`
- `file_name`
- `file_type`
- `file_size`
- `file_path`
- `file_hash`
- `storage_size`
- `metadata`
- `processed_at`
- `error_message`
- `deleted_at`

`chunks` 关键字段：

- `id`
- `tenant_id`
- `knowledge_base_id`
- `knowledge_id`
- `content`
- `chunk_index`
- `is_enabled`
- `start_at`
- `end_at`
- `pre_chunk_id`
- `next_chunk_id`
- `chunk_type`
- `parent_chunk_id`
- `image_info`
- `relation_chunks`
- `indirect_relation_chunks`
- `created_at`
- `updated_at`
- `deleted_at`

后续迁移补充：

- `000001_agent.up.sql`：`metadata`、`tag_id`、`status`、`content_hash`
- `000003_chunk_flags.up.sql`：`flags`
- `000010_add_seq_id.up.sql`：`seq_id`
- `000033_add_video_info_to_chunks.up.sql`：`video_info`

Go 类型 `internal/types/chunk.go` 还定义了多种 `ChunkType`：

- `text`
- `parent_text`
- `image_ocr`
- `image_caption`
- `summary`
- `entity`
- `relationship`
- `faq`
- `web_search`
- `table_summary`
- `table_column`
- `wiki_page`

chunk 入库流程在 `knowledge_process.go` 的 `processChunks()` 中：

- 先清理旧 chunk 和索引；
- 先创建 parent chunks；
- 再创建 text child/flat chunks；
- 设置 `start_at/end_at`；
- flat 模式下设置 `pre_chunk_id/next_chunk_id`；
- parent-child 模式下设置 `parent_chunk_id`；
- 保存 chunks 到数据库；
- 之后再构造 `IndexInfo` 并批量索引。

### 2.4 embedding 和向量/关键词索引入库

WeKnora 将 chunk 表和检索索引表分离。

迁移 `migrations/versioned/000002_embeddings.up.sql` 创建 `embeddings` 表：

- `id`
- `source_id`
- `source_type`
- `chunk_id`
- `knowledge_id`
- `knowledge_base_id`
- `content`
- `dimension`
- `embedding halfvec`
- `is_enabled`

索引策略：

- `CREATE EXTENSION IF NOT EXISTS vector`
- `CREATE EXTENSION IF NOT EXISTS pg_trgm`
- `CREATE EXTENSION IF NOT EXISTS pg_search`
- ParadeDB BM25：`embeddings_search_idx`
- HNSW halfvec 索引：按维度分别建索引，例如 3584、798
- `idx_embeddings_is_enabled`
- `idx_embeddings_knowledge_base_id`

embedding 写入入口：

- `internal/application/service/retriever/keywords_vector_hybrid_indexer.go`
- `BatchIndex()` 会调用 embedding model 的 `BatchEmbedWithPool`
- 然后调用检索仓库的 `BatchSave`
- Postgres 仓库使用 `internal/application/repository/retriever/postgres/repository.go`

重要差异：

- WeKnora `embeddings.content` 保存的是用于检索索引的内容。
- `knowledge_process.go` 中构造 `IndexInfo.Content` 时，会拼接文档标题，并调用 `chunk.EmbeddingContent()`；也就是 embedding 内容可能是 `title + ContextHeader + chunk.Content`，不是数据库 `chunks.content` 的简单拷贝。
- parent chunk 入库，但不参与向量索引；child/flat text chunk 参与索引。
- `embeddings` 表同时服务关键词 BM25 与向量检索。

### 2.5 hybrid-search

WeKnora hybrid-search 路由：

- `GET /api/v1/knowledge-bases/:id/hybrid-search`
- 注册位置：`internal/router/router.go`
- Handler：`internal/handler/knowledgebase.go` 的 `HybridSearch`
- Client：`client/knowledgebase.go` 的 `HybridSearch`

请求参数由 `client/knowledgebase.go` 的 `SearchParams` 体现：

- `query_text`
- `vector_threshold`
- `keyword_threshold`
- `match_count`
- `disable_keywords_match`
- `disable_vector_match`

返回字段由 `client/knowledgebase.go` 的 `SearchResult` 体现：

- `id`
- `content`
- `knowledge_id`
- `chunk_index`
- `knowledge_title`
- `start_at`
- `end_at`
- `seq`
- `score`
- `match_type`
- `chunk_type`
- `image_info`
- `metadata`
- `knowledge_filename`
- `knowledge_source`
- `knowledge_channel`
- `matched_content`

服务端融合逻辑：

- `internal/application/service/knowledgebase_search.go`
- `internal/application/service/knowledgebase_search_fusion.go`
- `internal/application/service/knowledgebase_search_results.go`

检索流程：

1. 校验 KB 范围和 embedding model 一致性。
2. 若启用向量检索，先生成 query embedding。
3. 按 vector store 分组 fan-out。
4. 分别调用 vector 与 keyword retriever。
5. `classifyRetrievalResults()` 区分向量/关键词结果。
6. `fuseOrDeduplicate()` 融合。
7. 向量单路或关键词单路时按原 score 去重。
8. 双路命中时使用 RRF：`vectorWeight/(k+vectorRank) + keywordWeight/(k+keywordRank)`。
9. `processSearchResults()` 批量回表取 `knowledges` 与 `chunks`。
10. 默认进行上下文增强：parent、nearby、relation chunks。

Postgres 检索：

- keyword 使用 ParadeDB `content ||| ?` 和 `paradedb.score(id)`。
- vector 使用 `embedding::halfvec(dim) <=> query`，score 为 `1 - distance`。
- 两路都过滤 `is_enabled IS NULL OR is_enabled = true`。
- `processSearchResults()` 还会过滤可搜索 chunk type。

## 3. know_rag 当前链路

### 3.1 文档解析

know_rag 当前解析在 `backend/app/services/parser_service.py`：

- `.txt/.md`：按文本读取；
- `.pdf`：使用 `pypdf` 每页抽取文本；
- `.docx`：使用 `python-docx` 抽取段落文本；
- 返回前调用 `sanitize_text_for_postgres()`；
- 不调用 WeKnora docreader；
- 不产生 Markdown 结构增强；
- 不保留 image_refs；
- 不保留页码、bbox、表格结构、图片引用等结构化 metadata。

### 3.2 chunk 切分

know_rag 当前切分在 `backend/app/services/chunk_service.py`：

- 使用 `DEFAULT_CHUNK_SIZE`，默认约 800；
- 使用 `DEFAULT_CHUNK_OVERLAP`，默认约 100；
- 直接按字符窗口切分；
- 每个 chunk 只保留 `content`、`chunk_index`、`token_count`、`metadata_json`；
- metadata 仅含 `source=parser`、`parser=basic`、`file_type`；
- 不保留 start/end；
- 不保留 pre/next；
- 不保留 parent/child；
- 不保留 ContextHeader；
- 不识别 Markdown heading、paragraph、table、code block、image refs。

### 3.3 数据模型

know_rag 当前模型在 `backend/app/db/models.py`：

- `documents` 表相当于 WeKnora 的 `knowledges` 简化版；
- `chunks` 使用 `document_id`，不是 `knowledge_id`；
- `chunks` 字段包括：`id`、`document_id`、`knowledge_base_id`、`content`、`chunk_index`、`token_count`、`is_enabled`、`status`、`metadata_json`；
- `chunk_embeddings` 字段包括：`chunk_id`、`document_id`、`knowledge_base_id`、`embedding Vector(1024)`、`model_name`、`dimension`。

### 3.4 embedding 入库

know_rag 当前 embedding 在 `backend/app/services/embedding_service.py`：

- 调用本地 Ollama；
- 校验维度；
- 对 enabled + ready chunks 逐个生成 embedding；
- upsert 到 `chunk_embeddings`；
- embedding 内容是 `chunk.content`，没有拼接 title 或 ContextHeader；
- `chunk_embeddings` 不保存 content；
- keyword 检索仍回查 `chunks.content` 并使用 ILIKE。

### 3.5 hybrid-search

know_rag 当前检索在 `backend/app/services/retrieval_service.py`：

- vector：`chunk_embeddings` join `chunks/documents`，使用 pgvector `<=>`；
- keyword：`chunks.content ILIKE :pattern`；
- 融合：`vector_score * HYBRID_VECTOR_WEIGHT + keyword_score * HYBRID_KEYWORD_WEIGHT`；
- 返回结构：`chunk_id`、`document_id`、`knowledge_base_id`、`title`、`file_name`、`content/preview`、`chunk_index`、`score`、`vector_score`、`keyword_score`、`match_type`。

## 4. 差异对比表

| 维度 | WeKnora | know_rag 当前 | 影响 |
|---|---|---|---|
| 文档实体命名 | `knowledges` | `documents` | MCP/接口兼容需要映射 |
| 文档解析输出 | Markdown + image_refs + metadata | 纯文本为主 | 影响标题、表格、图片、段落结构保留 |
| docreader | 支持 gRPC/HTTP docreader | 未接入 | 解析质量与 metadata 差异明显 |
| 切分策略 | strategy-aware，heading/heuristic/recursive/legacy | 简单字符窗口 | 直接影响检索质量 |
| protected patterns | 保护表格、图片、链接、代码块、公式 | 无 | 易切坏结构化内容 |
| start/end | 保留 | 未保留 | 影响来源追溯和高亮 |
| pre/next | 保留 | 未保留 | 影响上下文扩展 |
| parent/child | 支持 | 未支持 | 影响长文档召回与答案上下文 |
| chunk type | 多类型 | 只有文本 ready/disabled | 影响 FAQ、图片、summary、table 扩展 |
| chunk metadata | chunk metadata + image_info + relations | 简单 metadata_json | 来源追溯不足 |
| embedding 表 | `embeddings`，同时含 content、halfvec、source 信息 | `chunk_embeddings`，只存向量 | keyword/vector 索引分离方式不同 |
| embedding 内容 | title + ContextHeader + chunk content | chunk content | 语义召回弱于 WeKnora |
| keyword 检索 | ParadeDB BM25 `content ||| ?` | ILIKE 命中次数近似 | 中文关键词质量差距较大 |
| hybrid 融合 | RRF | 加权分数相加 | 排序语义不一致 |
| 返回字段 | `knowledge_id`、`start_at/end_at`、`match_type` 数值、metadata、source/channel | `document_id`、file/title、字符串 match_type | MCP/WeKnora 兼容需要适配 |
| 上下文增强 | parent/nearby/relation chunks | 未实现 | 后续 RAG context pack 质量受影响 |

## 5. 必须修正项

以下差异会影响后续检索质量或 MCP 召回兼容，建议优先改：

1. 增加 chunk 位置字段：`start_at`、`end_at`。
2. 增加 chunk 关系字段：`pre_chunk_id`、`next_chunk_id`、`parent_chunk_id`。
3. 增加 `chunk_type`，至少支持 `text`、`parent_text`。
4. 增加或兼容 `knowledge_id` 概念。可以先让 `documents.id` 作为 `knowledge_id`，但对外和检索结果中提供 `knowledge_id` 字段。
5. 解析结果改为 Markdown 优先，保留 parser metadata。
6. chunk metadata 扩展为可承载 `source`、`parser`、`file_type`、`page`、`section`、`heading_path`、`image_refs`。
7. 切分策略从简单字符窗口升级到结构感知 splitter。
8. embedding 内容需要支持 `title + context_header + content`。
9. hybrid-search 返回字段应补齐 `knowledge_id`、`knowledge_title`、`start_at`、`end_at`、`chunk_type`、`metadata`、`matched_content`。
10. 融合策略建议切换为 RRF，或至少提供 RRF 选项。

## 6. 可接受简化项

以下简化在 know_rag 的本地 RAG 底座目标下可以接受：

1. 保留 `documents` 表名，不强制重命名为 `knowledges`；但需要提供 `knowledge_id` 别名/兼容字段。
2. 暂不迁移 WeKnora 多租户、复杂 RBAC、Agent、Web Search、外部 SaaS、Wiki、Graph RAG。
3. 暂不支持 WeKnora 全量多向量库适配，只保留 PostgreSQL/pgvector。
4. 暂不支持图片 OCR、caption、VLM，但数据模型应预留 `image_info` 或 metadata。
5. 暂不强制引入 Asynq/Redis 队列；MVP 可同步处理，但状态字段要能支撑未来异步化。
6. 暂不使用 ParadeDB BM25，短期可保留 ILIKE；但中文关键词检索质量会弱。

## 7. 数据模型对齐建议

### Phase A：数据模型对齐

建议不立即破坏现有 `documents/chunks/chunk_embeddings`，采用增量字段兼容。

`documents` 建议新增：

- `source`
- `channel`
- `metadata_json`
- `processed_at`
- `enable_status`
- 可选：`knowledge_id_alias` 不推荐；直接使用 `documents.id` 作为 `knowledge_id` 更简单。

`chunks` 建议新增：

- `knowledge_id`：默认等于 `document_id`，用于兼容 WeKnora/MCP 返回。
- `start_at`
- `end_at`
- `pre_chunk_id`
- `next_chunk_id`
- `chunk_type`
- `parent_chunk_id`
- `content_hash`
- `image_info`
- `relation_chunks`
- `indirect_relation_chunks`
- `context_header`：可持久化，方便重新生成 embedding。
- `metadata_json` 扩展为统一 metadata 容器。

`chunk_embeddings` 建议评估两种路径：

1. 保持现有表，但增加 `content`、`source_id`、`source_type`、`knowledge_id`、`is_enabled`，向 WeKnora `embeddings` 靠齐。
2. 新增 `embeddings` 表，与 WeKnora 命名一致；后续迁移 `chunk_embeddings` 数据。

建议优先采用方案 1，避免一次性大迁移；对外 API 层返回 WeKnora 兼容字段。

## 8. chunk 策略对齐建议

### Phase B：切分策略对齐

建议实现 Python 版结构感知 splitter，行为向 WeKnora Go chunker 靠齐：

1. 输入统一使用 Markdown 文本。
2. 支持 `chunk_size`、`chunk_overlap`、`separators`。
3. 支持 protected patterns：
   - Markdown 图片
   - Markdown 链接
   - Markdown 表格 header/body
   - fenced code block
   - LaTeX block
4. 输出对象不再只是字符串，而是：
   - `content`
   - `context_header`
   - `seq`
   - `start`
   - `end`
   - `metadata`
5. 增加 heading-aware 切分：
   - 根据 Markdown `#` 标题形成 `heading_path`
   - `context_header` 不写入 content，但用于 embedding
6. 增加 parent-child 可选模式：
   - parent 默认 4096；
   - child 默认 384；
   - child 参与 embedding；
   - parent 用于 context pack。
7. 兼容已有 chunks：
   - 新字段 nullable；
   - 旧数据 `chunk_type='text'`；
   - `start_at/end_at` 可为空或根据旧 chunk 顺序近似补齐；
   - 重新解析后生成完整结构。

## 9. 入库策略对齐建议

### Phase C：入库流程对齐

WeKnora 将流程拆为：

1. 上传并创建 knowledge。
2. 保存文件。
3. 异步解析。
4. Markdown 化。
5. chunk 入库。
6. embedding/keyword 索引入库。
7. 后处理任务。

know_rag 当前同步闭环简单直接。建议逐步拆分，而不是马上引入完整任务系统：

1. 保留同步接口，但内部状态拆清：
   - `pending`
   - `parsing`
   - `chunking`
   - `parsed`
   - `indexing`
   - `indexed`
   - `failed`
2. 文档解析与 embedding 解耦，保持 Phase 4 的设计：上传/解析不依赖 embedding。
3. 新增可选任务表：
   - `tasks`
   - `task_type`
   - `resource_type`
   - `resource_id`
   - `status`
   - `error_message`
4. 暂不强制 Redis queue；后续可接 RQ/Celery/Arq。
5. 入库时先保存 chunks，再批量生成 embeddings。
6. embedding 失败不应删除 chunks；只标记索引状态失败。

## 10. 检索接口对齐建议

### Phase D：检索接口对齐

建议 hybrid-search 返回结构向 WeKnora 靠齐，同时保留 know_rag 当前字段：

```json
{
  "id": "chunk_id",
  "chunk_id": "chunk_id",
  "content": "...",
  "knowledge_id": "document_id",
  "document_id": "document_id",
  "knowledge_base_id": "kb_id",
  "chunk_index": 0,
  "knowledge_title": "文档标题",
  "title": "文档标题",
  "start_at": 0,
  "end_at": 512,
  "seq": 0,
  "score": 0.01,
  "match_type": "hybrid",
  "chunk_type": "text",
  "metadata": {},
  "chunk_metadata": {},
  "knowledge_filename": "file.pdf",
  "knowledge_source": "file",
  "matched_content": "用于索引命中的内容"
}
```

检索策略建议：

1. vector score 保留 `1 - distance`。
2. keyword 短期保留 ILIKE，但结果进入统一 rank list。
3. hybrid 融合改为 RRF：
   - 默认 `rrf_k=60`
   - `vector_weight=1.0`
   - `keyword_weight=1.0`
4. 如果暂不引入 ParadeDB，文档中明确 keyword 是简化实现。
5. 增加 context enrichment：
   - parent chunk
   - previous/next chunk
   - relation chunks
6. 检索过滤：
   - `chunks.is_enabled=true`
   - `chunks.status in ('ready', 'indexed')`
   - 排除软删除数据。

## 11. 后续 Codex 修改提示词草案

### 提示词 1：数据模型对齐

```text
你现在在 know_rag 项目中工作。请只修改 know_rag，不修改 WeKnora。

目标：将 know_rag 的 documents/chunks/chunk_embeddings 数据模型向 WeKnora 对齐，但不破坏已有数据。

要求：
1. 新增 Alembic migration。
2. chunks 增加 knowledge_id、start_at、end_at、pre_chunk_id、next_chunk_id、chunk_type、parent_chunk_id、content_hash、image_info、relation_chunks、indirect_relation_chunks、context_header。
3. documents 增加 source、channel、metadata_json、processed_at、enable_status。
4. 旧数据兼容：knowledge_id 默认等于 document_id，chunk_type 默认 text。
5. 更新 SQLAlchemy models、schemas、docs/database.md。
6. 不实现新切分逻辑。
```

### 提示词 2：结构感知 chunker

```text
你现在在 know_rag 项目中工作。请实现 Python 版结构感知 chunker，行为向 WeKnora internal/infrastructure/chunker 对齐。

要求：
1. 输入 Markdown 文本。
2. 输出 ParsedChunk 对象：content、context_header、seq、start、end、metadata。
3. 支持 chunk_size、chunk_overlap、separators。
4. 保护 Markdown 表格、图片、链接、代码块、LaTeX。
5. 支持 heading-aware context_header。
6. 可选支持 parent-child chunking。
7. 文档重新解析时写入 start/end/pre/next/parent/chunk_type。
8. 更新 docs/backend.md、docs/database.md。
```

### 提示词 3：embedding 内容与检索返回对齐

```text
你现在在 know_rag 项目中工作。请对齐 embedding 和 hybrid-search 字段。

要求：
1. embedding 内容改为 title + context_header + chunk.content。
2. chunk_embeddings 增加 content、source_id、source_type、knowledge_id、is_enabled，或新增 embeddings 表。
3. hybrid-search 返回 knowledge_id、knowledge_title、start_at、end_at、seq、chunk_type、metadata、chunk_metadata、knowledge_filename、knowledge_source、matched_content。
4. hybrid 融合改为 RRF。
5. 保留现有前端可用字段，避免破坏页面。
6. 不实现 RAG 问答和 MCP Gateway。
```

### 提示词 4：docreader 对接

```text
你现在在 know_rag 项目中工作。请增加可选 docreader 解析接口。

要求：
1. 新增 DOCREADER_BASE_URL / DOCREADER_GRPC_ADDR 配置。
2. parser_service 优先调用 docreader，失败时 fallback 到当前基础解析。
3. 保存 markdown_content、image_refs、metadata。
4. chunk_service 使用 Markdown 内容切分。
5. 不实现 OCR/VLM，只保存 image_refs metadata。
6. 更新 README 和 docs/deployment.md。
```
 
## Phase A 已执行状态

已执行 WeKnora 数据模型对齐第一步：

- 不重命名 `documents`、`chunks`、`chunk_embeddings`。
- 新增 `0005_weknora_alignment_phase_a.py` 迁移。
- `documents` 补充 source/channel/metadata_json/processed_at/enable_status。
- `chunks` 补充 knowledge_id、start/end、pre/next、chunk_type、parent_chunk_id、content_hash、image_info、relation_chunks、indirect_relation_chunks、context_header、flags。
- `chunk_embeddings` 补充 knowledge_id、source_id、source_type、content、is_enabled。
- `knowledge_id` 当前按兼容策略等于 `document_id`。
- 当前新生成 flat chunks 会写入字符窗口 `start_at/end_at` 和相邻 `pre_chunk_id/next_chunk_id`。

仍未执行：

- 结构感知 chunker。
- parent-child chunking。
- embedding 内容改为 title + context_header + content。
- WeKnora RRF hybrid-search。
- RAG 问答和 MCP Gateway。
 
## Phase B 已执行状态

已执行结构感知 chunker 对齐：

- 新增 `backend/app/services/chunker/`。
- 实现 `auto`、`heading`、`recursive`、`legacy` 策略。
- 支持 Markdown heading path，并将其写入 `context_header` 和 `metadata_json.heading_path`。
- 支持 fenced code block、Markdown 表格、Markdown 图片、Markdown 链接、LaTeX block、HTML table/pre/code 的基础保护。
- 新上传或重新解析文档会写入 `start_at/end_at`、`pre_chunk_id/next_chunk_id`、`chunk_type`、`parent_chunk_id`、`content_hash`、`context_header`。
- parent-child chunking 已实现但默认关闭。
- 重新解析会删除旧 chunks 与旧 `chunk_embeddings`。
- embedding 和 retrieval 已过滤 `chunk_type='text'`，跳过 `parent_text`。

仍未执行：

- docreader 对接。
- embedding 内容改为 title + context_header + content。
- WeKnora RRF hybrid-search。
- RAG 问答和 MCP Gateway。
## Phase C 已执行状态

Phase C 已完成 embedding 内容与索引表进一步对齐：

- embedding 输入由 `chunk.content` 调整为“文档标题 + context_header + chunk.content”。
- `chunk_embeddings.content` 保存实际用于 embedding 和关键词检索的索引文本。
- `chunk_embeddings.source_id/source_type/knowledge_id/is_enabled` 已参与写入和过滤。
- 文档和知识库 embedding 状态接口增加上下文标题统计和索引文本统计。
- hybrid-search 结果补充 `knowledge_id`、`knowledge_title`、`knowledge_filename`、`matched_content`、`context_header`、`metadata_json` 等 WeKnora-compatible 字段。

本阶段仍未实现 RRF；hybrid-search 融合策略将在 Phase D 继续对齐。
## Phase D 已执行状态

Phase D 已完成 hybrid-search RRF 融合与返回结构进一步对齐：

- 默认融合策略改为 RRF。
- 保留 weighted 兼容旧逻辑。
- 支持 `query_text`、`match_count`、`disable_vector_match`、`disable_keywords_match` 等 WeKnora 风格参数。
- 返回 `rrf_score`、`vector_rank`、`keyword_rank`。
- `include_context=true` 时返回 parent、previous、next 短预览。

本阶段仍未实现正式 context pack、本地 RAG 问答和 MCP Gateway。
