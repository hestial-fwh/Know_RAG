# API

> 状态：Current（完整实时契约以 FastAPI `/docs` 为准）
> 维护日期：2026-07-23

统一响应：

```json
{
  "success": true,
  "data": {},
  "message": ""
}
```

除 `/health` 和 `/api/v1/auth/login` 外，业务接口需要 `Authorization: Bearer <token>`。

Chunk 返回中的 `embedding_index_info` 可以包含模型、维度、向量长度、范数和最多 16 维
`vector_preview`，不会包含完整 embedding 向量。

Phase 2 开启 graph 与 materialization 后，Chunk `metadata_json` 可包含
`relation_summary_version`、`relation_ids`、`relation_summaries`、
`outgoing_asset_chunk_ids`、`incoming_reference_chunk_ids` 和 `group_ids`。每条 summary 只包含
relation type/status/method/score；完整 evidence 与 candidates 只保存在内部 Canonical artifact，
不会被 Phase 2 relation summary 再复制到每个 Chunk；既有图片局部 `image_relation` metadata
保持兼容返回。

## Phase 4 新增真实接口

### 测试 Embedding

`POST /api/v1/initialization/embedding/test`

```json
{
  "text": "这是一个测试文本"
}
```

返回：

```json
{
  "success": true,
  "data": {
    "model": "bge-m3",
    "dimension": 1024,
    "sample_size": 1024
  },
  "message": ""
}
```

### 文档向量化

- `POST /api/v1/documents/{id}/embedding`
- `GET /api/v1/documents/{id}/embedding/status`

返回字段包含：

- `document_id`
- `total_chunks`
- `embedded_chunks`
- `missing_chunks`
- `model`
- `dimension`

### 知识库向量化

- `POST /api/v1/knowledge-bases/{kb_id}/embedding`
- `GET /api/v1/knowledge-bases/{kb_id}/embedding/status`

返回字段包含：

- `knowledge_base_id`
- `total_chunks`
- `embedded_chunks`
- `missing_chunks`
- `model`
- `dimension`

### Hybrid Search

`POST /api/v1/knowledge-bases/{kb_id}/hybrid-search`

```json
{
  "query": "用户问题",
  "top_k": 5,
  "score_threshold": 0.3,
  "use_vector": true,
  "use_keyword": true,
  "include_content": true
}
```

返回包含：

- `chunk_id`
- `document_id`
- `knowledge_base_id`
- `title`
- `file_name`
- `content` 或 `preview`
- `chunk_index`
- `score`
- `vector_score`
- `keyword_score`
- `match_type`

## 仍为 placeholder

- `POST /api/v1/knowledge-chat/{session_id}`
- FAQ search / FAQ embedding
- MCP placeholder API

## 文档解析清洗

文档上传、解析、chunk 入库与 embedding 生成是解耦的。上传和解析流程不会依赖 `chunk_embeddings` 表；只有用户点击“生成向量”或调用 embedding API 时才会写入向量。

上传接口不设置应用层文件大小上限，并以分块读取、临时文件落盘和 MinIO 流式写入控制内存。
实际可上传大小仍受可用磁盘、MinIO 容量及外部代理限制。

`GET /api/v1/parse-tasks/{task_id}/events` 只返回任务运行期间的轻量阶段事件。解析 block
和 chunk 预览不写入事件表；任务完成或失败后，临时事件会被自动清空，最终内容统一从文档和
chunk API 查询。

PDF、txt、md、docx 解析出的文本会在入库前自动删除 PostgreSQL `TEXT` 不允许的 NUL 字符和大部分不可见控制字符。如果 PDF 是扫描件或图片型 PDF，基础解析可能无法抽取文本。
 
## WeKnora 对齐 Phase A API 返回兼容

以下接口继续保留 know_rag 原字段，同时补充 WeKnora-compatible 字段：

- `GET /api/v1/documents/{id}`
- `GET /api/v1/knowledge-bases/{kb_id}/documents`
- `GET /api/v1/documents/{doc_id}/chunks`
- `GET /api/v1/chunks/by-id/{id}`

文档返回新增或补齐：

- `document_id`
- `knowledge_id`
- `source`
- `channel`
- `metadata`
- `metadata_json`
- `enable_status`
- `processed_at`

chunk 返回新增或补齐：

- `chunk_id`
- `knowledge_id`
- `seq`
- `start_at`
- `end_at`
- `pre_chunk_id`
- `next_chunk_id`
- `chunk_type`
- `parent_chunk_id`
- `metadata_json`
- `image_info`
- `relation_chunks`
- `indirect_relation_chunks`
- `context_header`
- `content_hash`
- `flags`

`knowledge_id` 当前等于 `document_id`。Phase A 只做返回结构兼容，不改变 hybrid-search 的融合策略和排序算法。
 
## Phase B chunk API

`GET /api/v1/documents/{doc_id}/chunks` 与 `GET /api/v1/chunks/by-id/{id}` 返回结构化 chunk 字段：

- `content`
- `preview`
- `chunk_id`
- `knowledge_id`
- `seq`
- `start_at`
- `end_at`
- `pre_chunk_id`
- `next_chunk_id`
- `chunk_type`
- `parent_chunk_id`
- `context_header`
- `content_hash`
- `metadata`
- `metadata_json`
- `is_enabled`
- `status`

`POST /api/v1/knowledge-bases/{kb_id}/hybrid-search` 继续保持当前加权融合策略，但结果会补充 `knowledge_id`、`start_at`、`end_at`、`context_header`、`chunk_type`、`parent_chunk_id`、`metadata`、`metadata_json` 等字段。

### 文档向量状态的字段级准入统计

`GET /api/v1/documents/{id}/embedding/status` 在原向量统计外返回
`content_admission_summary`：

- `chunks_with_decision / chunks_without_decision`
- `channel_status_counts`
- `source_counts`
- `recovered_legacy_skip_chunks / stale_legacy_skip_chunks`
- `skip_reason_counts`

切片详情的 `metadata_json.content_admission` 返回 version、最终 decision、`index_sources`、
`shadow_sources`、排除原因及通道预览。该诊断不返回完整 embedding 向量。

重新解析接口会删除旧 chunks 和旧 embeddings；新返回的 chunks 使用结构感知切分策略。
## WeKnora 对齐 Phase C API 更新

### POST `/api/v1/initialization/embedding/test`

请求可包含：

```json
{
  "text": "这是正文",
  "title": "测试文档",
  "context_header": "第一章 > 第二节"
}
```

返回增加 `input_preview` 和 `input_length`，用于确认实际 embedding 输入文本。

### Embedding 状态接口

`GET /api/v1/documents/{id}/embedding/status` 和 `GET /api/v1/knowledge-bases/{kb_id}/embedding/status` 返回增加：

- `has_context_header_count`
- `indexed_content_count`

### Hybrid Search 返回字段

检索结果保留旧字段，并补充：

- `id`
- `knowledge_id`
- `knowledge_title`
- `knowledge_filename`
- `knowledge_source`
- `knowledge_channel`
- `matched_content`
- `seq`
- `start_at`
- `end_at`
- `chunk_type`
- `parent_chunk_id`
- `context_header`
- `metadata_json`
- `chunk_metadata`
- `image_info`
- `file_hash`：源文件内容的 SHA-256；客户端可用它合并内容相同但 `document_id` 不同的历史重复上传记录。

`include_content=false` 时不会返回完整 `content`，但会返回 `preview` 和最多 1000 字符的 `matched_content`。
## 切分配置 API

### GET `/api/v1/chunker-config`

返回当前切分配置，包括 `strategy`、`chunk_size`、`chunk_overlap`、`separators`、`enable_parent_child`、`parent_chunk_size`、`child_chunk_size`、`child_chunk_overlap`。

### PUT `/api/v1/chunker-config`

保存切分配置。配置不会自动重切已有文档，新上传或重新解析文档时生效。

```json
{
  "strategy": "auto",
  "chunk_size": 512,
  "chunk_overlap": 80,
  "separators": ["\n\n", "\n", "。", ".", "；", ";", "，", ",", " "],
  "enable_parent_child": false,
  "parent_chunk_size": 4096,
  "child_chunk_size": 384,
  "child_chunk_overlap": 64
}
```
## WeKnora 对齐 Phase D Hybrid Search

`POST /api/v1/knowledge-bases/{kb_id}/hybrid-search` 同时支持旧参数和 WeKnora 风格参数：

```json
{
  "query_text": "测试问题",
  "match_count": 5,
  "vector_threshold": 0.3,
  "keyword_threshold": 0.3,
  "disable_vector_match": false,
  "disable_keywords_match": false,
  "include_content": true,
  "include_context": true,
  "fusion_strategy": "rrf",
  "rrf_k": 60,
  "rrf_vector_weight": 1.0,
  "rrf_keyword_weight": 1.0
}
```

返回结果补充：

- `rrf_score`
- `vector_rank`
- `keyword_rank`
- `context.parent`
- `context.previous`
- `context.next`

当 `DOCUMENT_RELATION_RETRIEVAL_EXPANSION_ENABLED=true` 时，显式图文关系可以在 direct hits
之后追加同知识库、同文档的关联 Chunk。追加项包含：

- 标准结果字段（包括 `chunk_id`、`chunk_index`、文档信息和 `match_type="relation_expansion"`）
- `retrieval_expansion=true`
- `expansion_source="figure_binding"`
- `expansion_source_id`：触发扩展的原始 hit ID

每个原始 hit 独立限制最多 3 张图片和 5 个正文 child，并保持 metadata 声明顺序。扩展失败时
`results` 保持为未扩展结果，响应 `message` 标记已降级；该开关默认关闭。

`include_content=false` 时不返回完整 `content`，仍返回 `preview`、`matched_content` 和上下文预览。
### DCP-0.1 数据集验收

- `GET /api/v1/parse-eval/datasets/document-profile`
  - 需要登录；
  - 只读返回本地生成的 manifest、覆盖率报告、30 个对抗 case 的 gold/actual/diff 和产物路径；
  - 未生成数据集时返回 `available=false` 及复现命令，不触发后台生成；
  - 合成报告始终保留 `accuracy_claim_allowed=false`。

### DCP-1.1 文档视觉布局验收

- `GET /api/v1/documents/{document_id}/visual-layout`
  - 需要登录，只读；
  - Canonical artifact 尚未生成时返回 `available=false` 和原因，不触发重新解析；
  - 不带 `page_no` 时只返回 `available_pages`、文档级 summary 和空的 `layout.pages`；
  - `page_no` 为可选整数；指定后只返回该页的 `LayoutRegion`、`VisualItem`、
    `VisualTextRelation` 和该页引用的紧凑 asset 映射；
  - `shadow_only=true` 表示结果仅用于验收，不改变内容准入、切分或检索；
  - asset 映射只包含页面、caption、图片地址、bbox 和 visual role，不返回 OCR 全文等无关字段。
