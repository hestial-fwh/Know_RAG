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

- [ ] 删除 TEST1 中重复导入的文档 `b4b05a5b`
- [ ] 导入流程增加文件哈希去重检查
- [ ] 提交代码并推送
- [ ] Phase 3（图片上下文展示优化）评估
- [ ] 修复复合图号绑定阻断问题（已有 plan，影响 35 张复合图片）
