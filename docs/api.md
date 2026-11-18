# API

## 意图路由诊断字段（2026-11-18）

自动策略的 `meta.retrieval.routing` 增加：

| 字段 | 含义 |
| --- | --- |
| `intent_source` | `rules` / `jev` / `jev_cache` / `rules_fallback` / `rules_shadow` |
| `intent_confidence` | Jev top1 概率；规则路径为 null |
| `intent_margin` | Jev top1 与 top2 差值 |
| `intent_signals` | `matched`（全部命中意图）、`weak`（过宽触发）、`short`（短查询） |
| `intent_latency_ms` | Jev 调用耗时；缓存命中为 0 |
| `intent_model` | 实际返回模型 |
| `intent_fallback_reason` | 超时/低置信/熔断/缺 key 等回退原因 |
| `intent_shadow` | 是否 shadow 模式 |
| `intent_cache_hit` | 是否命中 Redis 意图缓存 |
| `model_calls` | 实际 Jev 请求数；缓存命中为 0 |

配置见 `.env.example` 的 `INTENT_*`。`INTENT_PROVIDER` 默认 `rules`；`hybrid` 只对白名单 KB 生效。

## 知识库独立抽取模型（2026-11-06）

retrieval_profile.index_model可指定本机Ollama的provider、model_name、base_url，以及可选think和
reference_mode。reference_mode支持indices和names，默认indices。该配置进入当前KB的来源指纹。
names模式通过实体名称引用参与者、通过quote_id选择服务器提供的真实来源片段，发布前恢复原文并
执行原有实体、关系和引文校验。HTTP图谱与证据出口继续返回quote及原文定位信息。
仅启用relations的names模式优先把规范Markdown表格的首列作为主体、列标题作为谓词、同一行的
命名值作为对象，日期进入时间限定，纯数值作为原文属性保留。说明性段落使用配置的本机模型。

## 知识库图谱浏览（2026-11-06）

GET `/api/v1/knowledge-bases/{kb_id}/knowledge-index/graph`提供已认证KB范围的当前有效子图。
参数q按实体规范名做字面包含搜索，最长100字，focus按实体ID加载一跳邻接，limit默认80、范围1至150。
响应nodes区分entity/event，edges区分relation/participation，均附derived与当前原文evidence。
来源包含document_id、chunk_id、parse_revision和投影引文，quote截断时标记quote_truncated。
meta返回enabled、state、build_indexes、truncated和展示上限，scope为bounded_current_subgraph。
计数只表示本次展示子图。未启用、未就绪或无匹配均返回明确空状态，浏览接口只读。

## 页码与可靠图片图号（2026-11-05）

检索结果的metadata、Context flat chunks以及Evidence Groups文本/媒体新增位置字段，MCP同样透传：

```json
{"page_no":null,"page_range":"2、4","source_pages":[2,4],"page_location_status":"resolved",
 "source_spans":[{"source_node_id":"node-id","page_no":2,"start":100,"end":180,
                  "coordinate_space":"normalized_canonical_markdown",
                  "bbox":[20,30,300,100],"bbox_precision":"source_block","bbox_coordinate_space":"parser_page"}]}
```

PDF页码为1基物理页码；打印页码仅在解析器明确提供时以span的 `page_label` 独立保留。
`page_no`仅在完整单页时非空；`page_range`只合并连续页。`source_spans`表示切片来源块与区间，
不是逐词定位，已有裁剪/预览也不能把块级bbox当作精确引文框。
`page_location_status`为resolved（已定位）、partial（仅部分来源已知）、unavailable（无可靠映射）或
legacy_page（只有旧显式页码，无来源span）。partial的页码集合只代表已知部分。
Evidence Groups仍为兼容的evidence_groups.v1，新增字段不改变原字段含义。

旧数据仅在KB、文档、当前解析版本与精确区间一致时只读补位置，不写业务数据。账本不足需重解析。
图片通用标题会让位给可靠图题；歧义不强绑。可靠短图题可以保留在Context中，显式禁用仍优先。
直接图片命中的media也可返回 `figure_ids` 与 `figure_id_source`，来源包含asset_caption及绑定方法。
关键词侧使用已有keywords.keyword对规范图号做精确匹配，不改ES mapping；更新既有图片图号需重建
相关文档的切片和检索索引。semantic保持纯向量；正文图文关联扩展继续受已有开关控制。

## v2 候选预算与同源表格（2026-11-04）

semantic、hybrid、graph/local、event_graph 均接受 `strategy.budget.candidate_k`（默认20，
1～100），表示**每路基础召回上限**。`top_k`/`match_count` 是重排后的最终证据数；
hybrid 的向量20与关键词20可形成最多40个基础候选，关联扩展另受 `max_candidates` 等预算限制。
不在融合后提前截到最终 K。无法装入的完整关联链不作为完整路径返回；唯一候选不足时不保证填满 K。
无 strategy 且知识库为 legacy_v1 的兼容请求不受该改动影响。

`meta.retrieval.candidate_budget`（Context/MCP 同样透传）字段为：`basis="per_channel"`、
`candidate_k`、`final_top_k`、`raw_counts`、`unique_counts`、`candidate_counts`（后三者各含 vector/keyword）、
`base_fused_count`、`merged_count_before_rerank`、`selected_count`。selected_count 是图片扩展和
Context 裁剪前的证据数，不能当作最终上下文条目数。旧请求不增加该诊断对象。
raw_counts为底层最多5倍预取并过滤后的数量，unique_counts为来源去重后的数量，candidate_counts为
按每路candidate_k截断后实际进入融合的数量；不要把预取数误当作预算失效或最终证据数。

v2 表格候选仅在同知识库、同文档解析版本、准确原文区间、指纹和来源节点一致时归一化，
保留代表 chunk_id 与 `source_chunk_ids` 原始 ID 列表。关联 paths 的支持 ID 同步映射，发生映射
的 steps 保留 `source_chunk_id`。独立位置相同内容及不同表格行组不会因文本相似被合并。

## P5 自动选择与DRIFT（2026-11-06）

strategy新增`{"family":"tiered","tier":"auto"}`，与旧低层召回开关互斥，返回meta.retrieval.routing。
`{"family":"tiered","tier":"graph","graph_method":"drift"}`执行社区引导的有界Local补查。
budget新增max_rounds（2，1至3）、max_searches（4，1至8）、timeout_seconds（120，1至180）和
max_context_chars（12000，500至40000）。括号中首数为默认值，DRIFT要求max_candidates至少3。
所有轮次共同消耗预算，max_model_calls包括规划尝试与最终重排请求。
meta.retrieval.drift记录实际请求、候选消耗、子问题、终止原因与覆盖限制。
Global与DRIFT新增token_usage，记录本机Ollama返回的实际计数与有计数的调用数。
llm-context的return_mode=debug增加retrieval_results，承载同次执行的原始召回结果。
同题对比、错误恢复与完整字段见retrieval_p5_implementation.md。

## P4 Global与社区（2026-11-05）

strategy使用`{"family":"tiered","tier":"graph","graph_method":"global","fallback_policy":"error"}`。
budget另支持max_reports（默认4，1～12）、max_model_calls（默认5，2～13）、
max_summary_chars（默认6000，500～20000）；top_k仍控制支持原文数。Global不调用基础向量/BM25，
报告不可用时遵循显式回退策略。meta.retrieval.global包含graph_revision、clustering_provider、
derived_summaries、reports_available/reports_mapped、model_calls、input_chars、summary_chars、
coverage、budget_truncated、coverage_incomplete及aggregation_supported=false。
派生要点evidence携带chunk_id、quote、document_id、parse_revision、span_basis和returned_chunk_id；
同源表格归一化时returned_chunk_id映射实际返回块，原始chunk_id保留可追溯性。
Context/MCP只透传完整支持的要点，并增加context_complete及related_evidence_group_ids。

profile.build_indexes需同时启用relations、communities；community配置provider、max_communities、
max_members、max_source_chars、level，边界详见retrieval_p4_implementation.md。
knowledge-index新增POST `/communities/build`（`{}`补建，`{"retry":true}`重试，不接收document_id）
与GET `/communities`（只返回当前有效派生报告，不透传来源快照和模型配置）；均需登录认证。
GET `/status`增加global_retrieval_ready和community_status。

## P3 Local/事件策略（2026-11-04）

以下 strategy 对象可用于 hybrid-search GET/POST、llm-context 和 MCP：

```json
{"family":"tiered","tier":"graph","graph_method":"local","fallback_policy":"error",
 "budget":{"candidate_k":20,"max_candidates":50,"max_hops":2,"max_fanout":5,"max_seeds":8}}
```

事件路线使用 `{"family":"event_graph","fallback_policy":"error"}`，可带同一 budget。
budget 默认值如上；candidate_k/max_candidates 为1～100，max_hops为1～2，max_fanout为1～10，
max_seeds为1～20。Top K仍由 match_count/top_k 控制，关联支持切片共同消耗预算。
索引未构建、禁用或过期时 error 失败，hybrid明确回退；部分覆盖可执行但标记不完整。
profile.build_indexes允许 entities、relations、events、communities；communities必须同时启用relations。
status增加 capability_coverage，并由有效manifest计算 graph_retrieval_ready/event_retrieval_ready。

meta.retrieval.association包含 provider、kind、coverage、budget、budget_truncated、
evidence_budget_truncated、coverage_incomplete、aggregation_supported=false和paths。
paths含 support_chunk_ids、hop、steps；relation step保留谓词/限定，共享实体标记shared_entity。
Context另加 context_complete和related_evidence_group_ids，保持evidence_groups.v1原字段语义。

## 融合检索 fusion（2026-11-11）

`{"family":"tiered","tier":"fusion"}` 在同一次请求内组合基础召回、关系扩展与事件关联，并在证据层做两级 RRF。默认 `rank_mode=rrf`、`event_policy=independent`，只显式开启，不进入默认策略。

```json
{"query_text":"甲校新增专业有哪些实践合作资源？","top_k":10,
 "strategy":{"family":"tiered","tier":"fusion",
  "fusion":{"rank_mode":"rrf","event_policy":"independent","include_global":false,
            "include_event_timeline":false,"event_reserved_slots":2},
  "budget":{"candidate_k":20,"relation_quota":30,"event_quota":20,
            "max_structure_candidates":50,"structure_timeout_ms":800}}}
```

- `rank_mode`：`rrf` 为两级 RRF（结构内部先融合，再与基础比较）；`feature` 只对已入池基础候选做图特征重排，图独有候选走保留位。
- `event_policy`：`independent`（事件独立排序 + 保留位，推荐）、`fused`（事件并入结构排名）、`off`（不执行事件通道）。
- `include_global` 需要 relations+communities 且社区报告就绪；摘要只进背景层，`citation_allowed=false`，不占用 S 编号。
- `include_event_timeline` 输出事件线，作为派生上下文，同样不可引用。
- fusion 不接受 `graph_method`，基础向量/关键词强制开启；relations/events 至少一个就绪，否则按 `fallback_policy=hybrid|error` 处理。
- 新增预算字段 `relation_quota`、`event_quota`、`max_structure_candidates`、`structure_timeout_ms`、`global_timeout_seconds`、`max_global_model_calls`、`max_global_reports`；无数据库迁移，配额存于 `retrieval_profile` JSON。
- `meta.retrieval.fusion` 返回 `mode/executed/channel_status/candidate_counts/event/structure_paths/weights/quota/elapsed_ms/coverage_incomplete`；旧 `association` 投影保留，融合时 `kind="fusion"`。
- 响应可选返回顶层 `background` 与 `event_lines`；`context_text` 在资料段之前渲染 B/E 段，引用编号只分配给证据 chunk。

## P2 派生索引维护接口（2026-11-04）

以下均需现有用户认证，路径前缀 `/api/v1/knowledge-bases/{kb_id}/knowledge-index`：

- POST `/build`：`{"document_id":"可选文档ID","retry":false,"limit":100}`；只入队。
  不指定文档时补偿 KB 内任务，limit 为单次新增和重试任务的合计上限（1～1000）。
  `retry:true`可同时重置当前输入的partial/failed任务，完整及运行中任务保持现有状态；
  批量返回`created_jobs`和`retried_jobs`。指定document_id时保留单文档重试及原返回结构。
  partial任务自动退避60秒重试，最多3次，知识库详情的“重试未完成索引”可显式重置次数。
- GET `/status`：返回派生索引的当前文档状态分布和覆盖数；P3计算 Local/事件能力覆盖。
  `counts` 额外返回当前有效 manifest / graph_revision 下的实体、关系、事件、社区与可用报告条数。
- GET `/jobs?limit=50`：返回当前 KB 最近任务（1～100），不返回来源快照和模型地址。
- GET `/entities?name=甲校&limit=50`：回查有效实体提及证据；name 精确匹配有来源的别名。

创建/更新知识库时在 retrieval_profile 中传 `"build_indexes":["entities"]` 开启能力，
空数组关闭。关闭后实体出口不再返回证据。开启时同事务为已有文档排队（每次最多新增100个），
worker 补偿扫描继续处理余下文档。没有自动改写检索 tier 或启动模型。
实体 API 返回 span_basis=admitted_projection，并附 source 通道/来源类型；模型候选不是确定关系。

## 双模式策略入口（2026-11-03，P0/P1）

POST `/api/v1/knowledge-bases/{kb_id}/hybrid-search` 和 `llm-context` 新增：

```json
{"query_text":"问题", "strategy":{"family":"tiered","tier":"semantic"}, "rerank_enabled":false}
```

`tier` 支持执行semantic/hybrid/graph/fusion/auto，graph_method支持local/global/drift；fusion不接受graph_method。
`family=event_graph` 不允许 tier/graph_method。未知策略字段和无效组合报校验错误。
未实现能力按 `fallback_policy=hybrid|error` 回退或失败；默认 hybrid，回退必带诊断。
GET hybrid-search 支持同名 strategy 参数，值为 JSON 编码的策略对象；也支持 rerank_enabled。
显式新策略与 use_vector/use_keyword/disable_* 或 retrieval_mode 冲突时拒绝请求。
legacy_v1 的旧 retrieval_mode 仍兼容接收但不改变路线；新 MCP 不再自动发送 hybrid。

知识库创建/更新接受 `retrieval_profile`：

```json
{"version":"new_profile_v2","family_scope":"a","strategy":{"family":"tiered","tier":"auto"},
 "build_indexes":["entities","relations","communities"]}
```

新建库必须显式提供 `family_scope`：`a` 构建实体、关系、社区；`b` 构建实体、事件。
`build_indexes` 由家族推导，客户端传入值会被覆盖；创建后家族不可更改，需要更换时删除并按新家族重建。
A 家族禁用事件通道，B 家族禁用关系与社区通道；跨家族意图降级 hybrid 并在 routing 中说明。
旧库迁移后为 `{"version":"legacy_v1"}`，继续采用全局与请求旧开关。
优先级为请求 strategy > KB profile > 全局旧配置；切回 legacy_v1 可恢复旧配置选择方式。
首阶段 profile 物理存储为 knowledge_bases.retrieval_profile JSON 列，需迁移 0016。

所有检索出口的 `meta.retrieval` 返回 contract、requested_strategy、executed_strategy、channels、
degraded、degradation_reason、elapsed_ms 和 result_count。运行时缺失向量或关键词通道会反映实际路线；
严格策略禁止通道降级。Context 旧 retrieval_meta 字段仍保留，Evidence Group v1 仅增可选 meta.retrieval。

> 状态：Current（完整实时契约以 FastAPI `/docs` 为准）
> 维护日期：2026-08-13

统一响应：

```json
{
  "success": true,
  "data": {},
  "message": ""
}
```

除 `/health` 和 `/api/v1/auth/login` 外，业务接口需要 `Authorization: Bearer <token>`。

### 上传文件内容去重

`POST /api/v1/documents/upload` 按原文件 SHA-256 在指定知识库内判重。
相同内容即使改名也拒绝；同名不同内容、不同知识库中的相同内容允许上传。
已有文档解析失败或被禁用时仍拒绝重复上传，可使用已有文档的重新解析接口。
重复上传沿用业务错误响应（HTTP 200，`success=false`）：

```json
{"success":false,"data":{"existing_document_id":"已有文档ID","file_name":"已有文件.pdf"},"message":"文件内容已存在于当前知识库：已有文件.pdf，请使用已有文档"}
```

普通重复请求不写入对象、不新建文档或解析任务；并发上传由数据库唯一索引兜底，
失败方清理本次创建的对象并返回相同错误契约。

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

- `POST /api/v1/documents/{id}/embedding` — 同步向量化（阻塞直到完成）
- `POST /api/v1/documents/{id}/embedding/stream` — **SSE 流式向量化**（实时推送每个切片进度）
- `GET /api/v1/documents/{id}/embedding/status`

SSE 流式端点返回 `text/event-stream`，每条事件格式：

```json
{"type":"progress", "embedded_chunks":5, "total_chunks":66, "skipped_chunks":0, "failed_chunks":0}
```

最终事件 `type: "complete"` 包含完整 `EmbeddingResult`；错误事件 `type: "error"` 包含 `message`。

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
  - 只读返回本地生成的 manifest、覆盖率报告、31 个对抗 case 的 gold/actual/diff 和产物路径；
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

### DCP-2.1 文档多轴画像

- `GET /api/v1/documents/{document_id}/processing-profile`
  - 需要登录，只读；`page_no` 为可选整数；
  - 返回 `profile_version / parse_revision / shadow_only`；
  - `summary` 包含布局、语义提示、文本来源、OCR 可信度分布和不确定页数；
  - `ocr_admission` 返回 reflow 前生产投影汇总；指定 `page_no` 时才返回该页原始 block 审计项，
    每项包含 `origin/admission/quality_score/reasons/bbox/raw_text`；
  - `block_origin` 返回 MinerU block 来源识别版本与计数；
  - `parent_policy` 返回 DCP-3.1 VisualItem 与 page-overview parent 执行汇总；
  - `resolved_policies` 返回解耦后的 boundary、parent、content-admission 策略；
  - 不带 `page_no` 时仅返回轻量 `available_pages`，`pages/legacy_pages` 为空；
  - 指定 `page_no` 后，`pages` 返回该页四轴画像、来源准入建议、连续分数/分档、不确定原因与
    evidence；
  - `legacy_pages` 返回同页 v1 画像用于对照；
  - 旧 artifact 未生成 v2 时返回 `available=false` 和原因，不触发重新解析。

## MCP 接入管理

管理 API（均需登录）：

- `GET /api/v1/admin/mcp-clients` — 分页列表（page/page_size），含计算字段 `status`（enabled/disabled/expired）与 `is_expired`；永不返回 secret。
- `POST /api/v1/admin/mcp-clients` — 创建。入参 `{name, description?, scopes[], expires_in_days?}`（null=永久）；响应一次性返回 `client_secret`。
- `GET /api/v1/admin/mcp-clients/{id}` — 详情，不含 secret。
- `PATCH /api/v1/admin/mcp-clients/{id}` — 编辑 name/description/scopes/expires_in_days。
- `POST /api/v1/admin/mcp-clients/{id}/reset-secret` — 重置，旧 secret 立即失效，一次性返回新 secret。
- `POST /api/v1/admin/mcp-clients/{id}/enable` / `/disable` — 启停。
- `DELETE /api/v1/admin/mcp-clients/{id}` — 删除，凭证立即失效。

内部校验（仅 service token，供 MCP auth middleware 调用）：

- `POST /api/v1/internal/mcp-access/verify` — 入参 `{client_id, timestamp, nonce, signature, method, path, body_sha256, tool_name?}`；返回 `{allowed, client_id, name, scopes}` 或 401/403。

scopes 取值：`know_rag_list_knowledge_bases`、`know_rag_search_context`。

## MCP 上下文返回（Evidence Group）

`POST /api/v1/knowledge-bases/{kb_id}/llm-context` 新增 `return_mode="evidence_groups"`，
MCP 的 `know_rag_search_context` 默认走该模式，返回：

```json
{
  "groups": [
    {
      "group_no": 1,
      "group_id": "<parent_chunk_id 或 chunk_id>",
      "parent_chunk_id": "...",
      "document_id": "...",
      "document_url": "http://<frontend>/documents/{id}/content",
      "score": 0.91,
      "direct_hit_count": 2,
      "eligible_child_count": 6,
      "hit_ratio": 0.333,
      "chunks": [
        { "item_no": "1-1", "chunk_id": "...", "direct_hit": true, "context_reason": "retrieval_hit" },
        { "item_no": "1-2", "chunk_id": "...", "direct_hit": false, "context_reason": "neighbor", "overlap_trimmed": true, "trimmed_chars": 240, "overlap_source_chunk_ids": ["..."] }
      ],
      "media": [
        { "item_no": "1-M1", "asset_id": "...", "chunk_id": "...", "primary_reason": "figure_binding", "relation_sources": ["figure_binding", "same_parent"] }
      ]
    }
  ],
  "chunks": [],
  "media": []
}
```

- `group_no`/`item_no` 是展示编号，真实 `group_id`/`chunk_id`/`asset_id` 始终保留。
- 相邻文本 chunk 存在 source span 重叠时（同 document、同 parent），展示层会做**重叠归一化**：
  后一个（低优先级）chunk 的 `content` 只保留未重叠部分，并附加 `overlap_trimmed=true`、
  `trimmed_chars`（裁掉的字符数）与 `overlap_source_chunk_ids`（覆盖该 span 的 chunk id）。
  未裁剪的 chunk 不带这三个字段。图片/表格/公式不参与文本去重；不同 parent 不跨组归一化。
- 文档归属在 **group 级**：`document_id` + `document_url`（一个 group 一个源文档，
  `document_url` 指向前端 `/documents/{id}/content` 内容页，域名由 `FRONTEND_BASE_URL`
  决定，`document_id` 为空时为 `""`）。chunk 与 media 条目仍带块级 `document_id`、
  `document_title`、`file_name` 用于归属标识，但不重复 `document_url`。
- 顶层 `chunks` 与 `media` 是两个**同构的扁平数组**：`chunks` 只含 chunk 条目（带
  `direct_hit`），`media` 只含图片条目（带 `primary_reason`/`asset_id`）；它们与
  `groups[]` 内容一一对应，供不需要分组结构的消费者使用。evidence_groups 模式不返回
  `context_text`。
- 图片 `primary_reason` 固定优先级 `direct_hit > figure_binding > same_parent`；同 asset
  全局去重，去重后 `relation_sources` 合并全部证据、`related_group_nos` 保留跨组归属。
- 图片 `content` 保留 `上下文：` 字段（nearby/OCR 正文），不会被当作包装标签剥除；
  无描述/无 OCR 的占位图片仍按低信息过滤，不出现在 `media`。
- 返回结构的机器可读契约由 `app/schemas/evidence_groups.py` 的
  `EvidenceGroupsResponse`（含 `EvidenceChunkPayload` / `EvidenceMediaPayload` /
  `EvidenceGroup` / `EvidenceGroupsMeta`）定义；`build_evidence_groups_payload` 返回前
  会 `model_validate` 校验，保证 schema 与实际输出一致。`meta` 携带
  `schema_version`（当前 `evidence_groups.v1`）。`EvidenceChunkPayload` 含
  `overlap_trimmed` / `trimmed_chars` / `overlap_source_chunk_ids` 三个归一化标记字段。
