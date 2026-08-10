# 日报 2026-08-06

## 主题：检索上下文构建绑定感知改造（Phase 1-2）实施与验证

---

## 一、Phase 1: `relation_chunks` 数据落盘

### 1.1 迁移脚本修复

- **文件**：[0014_backfill_relation_chunks.py](../backend/app/db/migrations/versions/0014_backfill_relation_chunks.py)
- **问题**：`metadata_json` 是 PostgreSQL `json` 类型，不支持 `jsonb` 的 `?` 操作符，迁移执行报错 `operator does not exist: json ? unknown`
- **修复**：WHERE 子句中添加 `::jsonb` 类型转换
- **结果**：迁移成功执行，**281 个 chunk** 从 `metadata_json` 回填了 `relation_chunks`（分布：58个1个关联，60个2个，最高13个关联）

### 1.2 物化器写入

- **文件**：[materializer.py](../backend/app/services/document_relation/materializer.py)
- `materialize_figure_bindings()` 和 `materialize_relation_summaries()` 末尾，将 `figure_references`、`image_bindings`、`outgoing_asset_chunk_ids`、`incoming_reference_chunk_ids` 合并去重写入 `chunk.relation_chunks`

---

## 二、Phase 2A/2B: 上下文构建器绑定感知

### 2.1 预拉取关联 chunk（Phase 2A）

- **文件**：[retrieval_service.py](../backend/app/services/retrieval_service.py)
- `_attach_binding_related_chunks()` 方法：在 `build_parent_groups` 中收集命中 chunk 的 `relation_chunks`，批量查询 DB，作为 `binding_related_chunks` 附加到每个 parent_group
- 受 `DOCUMENT_RELATION_CONTEXT_BUILDING_ENABLED` 开关控制（默认 `true`）

### 2.2 上下文扩展重写（Phase 2B）

- **文件**：[llm_context_builder.py](../backend/app/services/llm_context_builder.py)
- `_cloud_build_window_for_parent()` 重构：
  - **新增 binding 优先逻辑**：读取 `parent_group["binding_related_chunks"]`，通过 `figure_references[].image_chunk_ids` 和 `outgoing_asset_chunk_ids[]` 精确拉取绑定图片/表格
  - **规则兜底**：原有 `figure_or_table_reference` / `forward_reference` / `nearby_multi_hits` 规则保留，仅在 binding 未覆盖时生效
  - **上下文标注**：binding 来源标记 `strategy: "binding_aware"`，规则来源标记 `strategy: "rule_based"`

---

## 三、Bug 修复

### 3.1 `sibling_lookup` 缺少跨父绑定 chunk

- **文件**：[llm_context_builder.py](../backend/app/services/llm_context_builder.py#L1658-L1663)
- **问题**：`build_cloud_flat_chunks_payload()` 构建 `sibling_lookup` 时只注册了 `parent_children` + `matched_children`，跨父的 `binding_related_chunks` 在 emit 阶段找不到
- **修复**：将每个 group 的 `binding_related_chunks` 也注册到 `sibling_lookup`
- **影响**：修复前，binding 图片虽写入 `media_chunk_ids` 但 emit 时因找不到 chunk 对象而被跳过

### 3.2 绑定图片同时是命中结果的去重

- 当 binding 图片的 VLM 描述也匹配查询时，该图片既是搜索结果 hit 又是 binding 扩展的 media
- emit 阶段先处理 hit 再处理 media，已 emit 的会自动跳过（`already in emitted set`），行为正确

---

## 四、端到端验证

### 4.1 TEST1 知识库检索测试

- **查询**："王羲之书法"
- **结果**：Top 10 全部来自书法文献 `diskfcl87_fcl87_05-页面.pdf`，内容高度相关（王羲之草书寒切帖卷、十七帖等）
- **混合检索**：RRF 融合正常（向量 + ES BM25）
- **注意**：该书法文献无绑定数据（页面级图片目录，非技术文档），这是预期行为

### 4.2 专利文档绑定验证

- **文档**：`db278_20260428104424_nrqnd095db.pdf`
- **156 个 image chunk** 有 1-13 个 `relation_chunks`
- Phase 2A `binding_related_chunks` 正确预拉取并分发到 parent_group
- Phase 2B `context_policy` 正确标注 `strategy: "binding_aware"`

---

## 五、数据质量问题

### 5.1 重复文档导入

- **文件**：`diskfcl87_fcl87_05-页面.pdf`（19MB，hash `559def28...`）
- **问题**：同一文件在 TEST1 知识库中被导入了两次：
  - `366eab2a` — 2026-07-29
  - `b4b05a5b` — 2026-07-30
- **影响**：该文档所有 chunk 双份存在，导致检索结果出现"重复块"
- **建议**：删除重复文档 `b4b05a5b`，并在导入流程增加文件哈希去重检查

---

## 六、变更文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `backend/app/db/migrations/versions/0014_backfill_relation_chunks.py` | **新增** | 存量 relation_chunks 回填迁移 |
| `backend/app/services/document_relation/materializer.py` | 修改 | Phase 1: relation_chunks 写入 |
| `backend/app/services/retrieval_service.py` | 修改 | Phase 2A: binding_related_chunks 预拉取 |
| `backend/app/services/llm_context_builder.py` | 修改 | Phase 2B: binding 优先扩展 + sibling_lookup 修复 |
| `backend/app/core/config.py` | 修改 | DOCUMENT_RELATION_CONTEXT_BUILDING_ENABLED 开关 |
| `docs/retrieval_context_building_plan.md` | **新增** | 完整方案文档 |
| `docs/image_chunk_binding_plan.md` | 修改 | 更新绑定计划文档 |

---

## 七、待办

- [x] 删除 TEST1 中重复导入的文档 `b4b05a5b`
- [ ] 导入流程增加文件哈希去重检查
- [ ] 提交代码并推送
- [ ] Phase 3（图片上下文展示优化）评估
- [ ] 修复复合图号绑定阻断问题（已有 plan，影响 35 张复合图片）

---

## 日报 2026-08-07

### 主题一：文档向量化实时进度条

**背景**：文档向量化为同步阻塞操作，前端点击"生成向量"后无进度反馈，等待中用户无法得知处理进度。

**后端改动**：
- [routes_documents.py](../backend/app/api/routes_documents.py)：新增 `POST /api/v1/documents/{id}/embedding/stream` SSE 端点，通过 `asyncio.Queue` + `StreamingResponse` 实时推送每个切片的 embedding 进度事件
- [embedding_service.py](../backend/app/services/embedding_service.py)：`progress_callback` 事件增加 `"type": "progress"` 字段，前端据此区分进度/完成/错误事件

**前端改动**：
- [documents.ts](../frontend/src/api/documents.ts)：新增 `embedDocumentStream()` 函数，基于 `fetch` + `ReadableStream` 解析 SSE 帧，返回 `AbortController` 支持中途取消
- [DocumentDetailView.vue](../frontend/src/views/DocumentDetailView.vue)：新增实时进度条（百分比 + 已处理/总数 + 跳过/失败数 + 取消按钮），完成后自动刷新向量化状态

**文档维护**：
- [api.md](./api.md)：补充 SSE 流式端点说明
- [frontend.md](./frontend.md)：补充实时进度条特性
- [index.md](./index.md)：更新维护日期

**验证**：
- SSE 端点端到端测试通过（66 chunk 逐一推送进度）
- 前端 `vue-tsc --noEmit` + ESLint + Vitest（9 tests）全部通过
- 前后端容器重建后正常运行

### 主题二：修复英文专利页被误判为图片

**背景**：`_page_kind` 函数的 `visual_dominant` 门控只检查嵌入式图片面积是否 ≥65%，不检查页面文字量。英文专利页（大图+成段技术英文）被误判为 `visual_dominant`，导致 OCR 文字隔离、切片策略退化。

**根因**：[document_profiler.py:158-171](../backend/app/services/document_parser/document_profiler.py#L158-L171) — `dominant_image_ratio >= 0.65` 单属性门控，缺少 `continuous_prose and native_text_chars > 0` 的文字页逃逸分支。

**修复**：[document_profiler.py](../backend/app/services/document_parser/document_profiler.py)：在 `scanned_prose` 分支后插入 `continuous_prose and native_text_chars > 0` 逃逸分支，推翻 `visual_dominant` 决策，让页面正确归类为 `native_text`。

**测试**：新建 [test_document_profiler.py](../backend/tests/test_document_profiler.py)，9 个测试用例覆盖书法、扫描件、英文专利、幻灯、表格、纯图、未知等所有页面类型。

**验证**：
- 新增 9 个测试全部通过
- 现有 32 个页面相关测试无回归
- 全量测试通过

---

## 主题三：文档处理模式双路径实施（2026-08-07）

### 背景

`_page_kind` 的 `dominant_image_ratio >= 0.65` 门控对专利/技术文档产生大量误报。之前的修复方案是在同一个函数里加入 3 个逃逸分支对抗 pdfplumber 信号，逻辑复杂且治标不治本。

### 方案

让用户在上传时选择文档类型，拆成两条独立路径：

| 模式 | 默认 | `visual_dominant` 触发条件 | 适用场景 |
|------|:---:|------|------|
| `standard` | ✅ | `primary_visual_count`（MinerU 显式标记） | 专利、论文、报告 |
| `visual_art` | | `primary_visual_count` + `dominant_image_ratio >= 0.65` + 逃逸分支 | 画册、书法 |

核心思路：**标准模式只信任 MinerU 信号，不咨询 pdfplumber**。从源头消除误报。

### 实施

**后端 4 文件**：
- [document_profiler.py](../backend/app/services/document_parser/document_profiler.py)：`_page_kind()` 和 `profile_canonical_document()` 新增 `processing_mode` 参数；标准模式分支只检查 `primary_visual_count`
- [chunk_service.py](../backend/app/services/chunk_service.py)：从 `document.metadata_json.parse_options.processing_mode` 读取模式 → 传入 profiler
- [document_service.py](../backend/app/services/document_service.py)：`upload_document()` 接受并持久化 `processing_mode`
- [routes_documents.py](../backend/app/api/routes_documents.py)：上传端点新增 `processing_mode` Form 参数（默认 `"standard"`）

**前端 2 文件**：
- [documents.ts](../frontend/src/api/documents.ts)：`uploadDocument()` payload 和 FormData 新增 `processing_mode`
- [DocumentUploadView.vue](../frontend/src/views/DocumentUploadView.vue)：新增"文档类型"下拉选择器（📄 标准文档 / 🎨 画册书法）

**存量兼容**：已解析文档无 `processing_mode` 字段 → 默认 `"visual_art"`（保守兼容，保持现有行为）。

### 测试

- 新增 `TestStandardModeClassification`（11 个测试）覆盖标准模式全部场景
- 现有 `TestPageKindClassification`（12 个测试）覆盖画册模式无回归
- 全量：876 passed / 3 既存失败（与本次无关）

### 文档

- [document_processing_mode_plan.md](./document_processing_mode_plan.md)：完整设计文档（状态：Current）
- [index.md](./index.md)：更新索引和日期

---

## 日报 2026-08-10

### 主题：标准模式 enricher 隔离 — 双模式真正解耦

**背景**：主题三（2026-08-07）实现了 `_page_kind()` 的双路径分支，但上游
`page_visual_enricher` 对所有 PDF 无差别执行——pdfplumber 分析后给图片打
`page_primary_visual` 标签。专利文档即使选了 standard 模式，enricher 的标签
仍然通过 `primary_visual_count` 间接泄漏进 profiler，导致 `visual_dominant`
误判。

**根因**：`_maybe_preserve_pdf_page_visuals()` 不检查 `processing_mode`，
enricher 在 standard 和 visual_art 模式下行为完全一致。

**修复**：[parser_service.py](../backend/app/services/parser_service.py)
`_maybe_preserve_pdf_page_visuals()`：
- `processing_mode == "standard"` → 直接返回 `parsed`，零 pdfplumber 调用
- `processing_mode == "visual_art"` → 完整 enricher 流程

**效果**：
- 新解析的 standard 文档 `primary_visual_count` 始终为 0
- `_page_kind()` 的 `canonical_text_chars >= 300` 守卫保留（保护存量文档）
- standard 模式解析速度提升（跳过 pdfplumber + 页面渲染）

**测试**：全量 876 passed / 3 既存失败 / 0 新增回归

**变更文件**：
| 文件 | 变更 |
|------|------|
| `backend/app/services/parser_service.py` | standard 模式跳过 enricher |
| `backend/app/services/document_parser/document_profiler.py` | 更新注释 |
| `docs/document_processing_mode_plan.md` | 补充 enricher 隔离记录 |
