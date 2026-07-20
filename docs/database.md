# Database

> 状态：Current（后续 Phase 小节为历史增量记录）
> 维护日期：2026-07-17

执行迁移：

```bash
docker compose exec backend alembic upgrade head
```

## Phase 4 强依赖

Phase 4 开始 `pgvector` 是强依赖。迁移 `0003_embedding_retrieval_phase4.py` 会执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

如果 pgvector 不可用，迁移会失败，需要先确认 PostgreSQL 镜像或数据库已安装 pgvector。当前 Compose 使用 `pgvector/pgvector:pg16`。

`pg_trgm` 会尝试启用。默认关键词检索使用 Elasticsearch BM25；只有显式启用
`ELASTICSEARCH_FALLBACK_TO_SQL=true` 时，Elasticsearch 故障才回退到 ILIKE。

## 主要表

### document_canonical_artifacts

迁移 `0011_canonical_provenance_p25.py` 为每个文档保存一份当前 Canonical revision：

- 主键 `document_id`，删除 document 时级联删除；
- `parse_revision`、`schema_version`、`algorithm_version` 用于区分数据和算法版本；
- `canonical_hash` 与 `canonical_markdown` 保存规范化后的正式切分输入；
- `provenance_json` 保存 source node、页码、bbox、语义角色和 normalize 前后字符范围；
- `assets_json` 保存不含图片 base64 的 asset sidecar；
- `figure_links_json` 保存引用状态和完整候选证据；
- `stats_json` 保存 grounding、asset 和引用状态统计。

该表与新 chunks 在 `rebuild_document_chunks()` 的同一调用方事务内替换。它是当前 revision
账本，不是 ParsedBlock 历史归档；旧文档需要 reparse 才会生成记录。

### chunk_embeddings

- `id`
- `chunk_id`
- `document_id`
- `knowledge_base_id`
- `embedding`
- `model_name`
- `dimension`
- `created_at`
- `updated_at`

关系：

- `chunk_id -> chunks.id`
- `document_id -> documents.id`
- `knowledge_base_id -> knowledge_bases.id`

索引：

- `chunk_id` 唯一索引，避免同一 chunk 重复写多条 embedding。
- `knowledge_base_id` 索引。
- `document_id` 索引。
- `embedding vector_cosine_ops` ivfflat 索引。

迁移：

- `0003_embedding_retrieval_phase4.py` 创建 `chunk_embeddings`。
- `0004_ensure_chunk_embeddings.py` 是幂等兜底迁移，用于修复版本已升级但表或索引缺失的环境。

检查表：

```bash
docker compose exec postgres psql -U know_rag -d know_rag -c "\d chunk_embeddings"
```

## 向量维度策略

默认：

```text
OLLAMA_EMBEDDING_DIMENSION=1024
OLLAMA_EMBEDDING_MODEL=bge-m3
```

后端会校验 Ollama 返回的 embedding 维度，维度不一致时拒绝写入。

## 检索能力

- 向量检索：基于 pgvector cosine distance。
- 关键词检索：默认 Elasticsearch BM25，PostgreSQL ILIKE 为可选应急回退。
- hybrid search：合并向量分和关键词分。

当前不保存 FAQ embedding，不实现 RAG 问答生成。

## TEXT 字段清洗

PostgreSQL `TEXT` 不允许保存 NUL 字符。系统会在文档解析和 chunk 入库前清洗文本：

- 删除 `\x00`。
- 删除大部分不可见控制字符。
- 保留 `\n`、`\t`、中文、英文、数字、标点和数学符号。

清洗后为空的 chunk 不会入库。文档解析、chunk 入库和 embedding 生成保持解耦；chunk 入库不依赖 `chunk_embeddings` 表。
 
## WeKnora 对齐 Phase A

新增迁移：`0005_weknora_alignment_phase_a.py`。

本迁移不删除数据、不重命名旧表，采用增量字段兼容方式：

- `documents` 新增 `source`、`channel`、`metadata_json`、`processed_at`、`enable_status`。
- `chunks` 新增 `knowledge_id`、`start_at`、`end_at`、`pre_chunk_id`、`next_chunk_id`、`chunk_type`、`parent_chunk_id`、`content_hash`、`image_info`、`relation_chunks`、`indirect_relation_chunks`、`context_header`、`flags`。
- `chunk_embeddings` 新增 `knowledge_id`、`source_id`、`source_type`、`content`、`is_enabled`。

回填策略：

- `chunks.knowledge_id = chunks.document_id`。
- `chunk_embeddings.knowledge_id = chunk_embeddings.document_id`。
- `chunk_embeddings.source_id = chunk_embeddings.chunk_id`。
- `chunk_embeddings.source_type = 'chunk'`。
- `chunk_embeddings.is_enabled = true`。
- `documents.source = 'file'`，`documents.channel = 'local'`，`documents.enable_status = 'enabled'`。

Phase A 暂不为 `knowledge_id`、`parent_chunk_id`、`pre_chunk_id`、`next_chunk_id` 增加外键，避免旧数据和循环关系导致迁移风险；这些关系当前由代码层维护。当前仍未实现结构感知 chunker、parent-child chunking 和 WeKnora RRF hybrid search。
 
## Phase B chunk 字段写入规则

新上传或重新解析文档会使用结构感知 chunker 写入以下字段：

- `start_at/end_at`：chunk 内容在解析文本中的字符位置，heading/recursive 策略尽量精确，保护块场景使用顺序扫描近似。
- `context_header`：Markdown heading path 拼接结果，不强制写入 content。
- `chunk_type`：默认 `text`；开启 parent-child 后 parent 为 `parent_text`，child 为 `text`。
- `parent_chunk_id`：parent-child 开启时 child 指向 parent chunk。
- `pre_chunk_id/next_chunk_id`：flat text chunks 或 child text chunks 的相邻关系。
- `content_hash`：`sha256(content)`。
- `metadata_json`：包含 `source`、`parser`、`file_type`、`strategy`、`heading_path`、`seq`，parent-child 时补充 `role` 和 `parent_seq`。

重新解析会先删除该 document 下旧 `chunk_embeddings` 和旧 `chunks`，防止旧向量继续参与检索。
## WeKnora 对齐 Phase C

`chunk_embeddings.content` 表示实际用于 embedding 和关键词检索的索引文本，当前由“文档标题 + 上下文标题 + 切片正文”构成。

`chunk_embeddings` 中以下字段已经进入实际逻辑：

- `source_id`：当前等于 `chunk_id`。
- `source_type`：当前固定为 `chunk`。
- `knowledge_id`：当前等于 `chunks.knowledge_id`，为空时回退到 `document_id`。
- `is_enabled`：用于检索过滤，禁用切片时同步更新。

embedding 向量属于本地知识资产，只保存在 PostgreSQL/pgvector 中。API 和前端只返回索引状态与索引文本预览，不返回 embedding 数组。

## 解析任务事件保留策略

`parse_tasks` 持久化任务状态、阶段、进度、统计和错误。`parse_task_events` 仅承载任务运行期间
的轻量进度事件，不保存 `parsed_block` 或 `chunk_created` 载荷，并在任务完成或失败时清空。
迁移 `0010_parse_event_retention.py` 会删除升级前遗留的过程事件。

## chunker_config 表

`chunker_config` 保存运行时切分配置。后端上传文档和重新解析文档时会读取该表，而不是只读取环境变量。

字段：

- `strategy`
- `chunk_size`
- `chunk_overlap`
- `separators`
- `enable_parent_child`
- `parent_chunk_size`
- `child_chunk_size`
- `child_chunk_overlap`

执行 `alembic upgrade head` 后创建该表。已有文档不会自动重切，重新解析后才会应用新配置。
## retrieval_config Phase D 字段

Phase D 新增：

- `fusion_strategy`：`rrf` 或 `weighted`，默认 `rrf`
- `rrf_k`：RRF 平滑参数，默认 60
- `rrf_vector_weight`：向量召回 RRF 权重，默认 1.0
- `rrf_keyword_weight`：关键词召回 RRF 权重，默认 1.0
- `include_context`：是否默认返回上下文预览，默认 true

执行 `alembic upgrade head` 会通过 `0007_retrieval_rrf_phase_d.py` 增量添加字段。
