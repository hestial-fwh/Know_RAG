# 召回上下文重叠归一化方案（Retrieval Context Overlap Normalization）

> 状态：Current（实施中）
> 维护日期：2026-08-14
> 范围：仅召回结果 → Context/Group Assembly → Display/MCP 归一化这一层

## 1. 当前问题

索引/向量化阶段允许 Chunk 之间 overlap（`chunk_overlap` 以及 `source_child_overlap` /
`continuation_across_floating_blocks` 机制）。当同文档同 parent 下相邻的多个子块同时被召回时，
UI 调试页 / LLM Context / MCP `know_rag_search_context` 会把同一段 source 文本重复展示多次。

典型现象（用户截图）：

```text
Chunk #10:  [0027] [0028]
Chunk #11:  [0027] [0028] [0029]
```

两个 hit 都作为直接命中返回，`[0027] [0028]` 被看到两遍。

## 2. 实际知识库样例（DB 实查确认）

知识库 `e532eff1-bbca-4029-aa43-dcd2d6949817`（专利）等。文本子块 `chunk_type='text'` 共 3034 个，
**100% 具有 `source_position_is_exact=true`**，全部带 `start_at`/`end_at` 列。

代表性高重叠对（同一 parent，按 `start_at` 排序的相邻对）：

| document_id | chunk_a (index) | chunk_b (index) | start/end | overlap | ratio |
|---|---|---|---|---|---|
| f7ccec50-6553-… | 2453107a (71) | 402b0e61 (72) | [21193,21401) / [21197,21920) | 204 | 0.981 |
| 0433471b-7a45-… | bb8eb404 (57) | 86dad6b9 (58) | [16666,16883) / [16679,17402) | 204 | 0.940 |
| 366eab2a-aebe-… | 12200fd3 (24) | cff495bf (25) | [4529,4721) / [4541,5321) | 180 | 0.938 |
| f16798c7-aa09-… | db47147b (56) | d6d991e2 (57) | [12544,12843) / [12569,13256) | 274 | 0.916 |
| 495d34ff-d986-… | aebe3acd (67) | 5fbc58e7 (68) | [24300,24564) / [24326,24953) | 238 | 0.902 |
| 10b23cc5-92a3-… | dc33e6e1 (5) | ecbc0b61 (8) | [349,860) / [832,860) | 28 | 1.000（完全包含） |

真实内容示例（chunk 71 / 72，`f7ccec50`）：

```text
chunk 71 [21193,21401): "4 cells at the top of charge. A BMS will therefore typically monitor the individual cell voltage ... cell balanc…"
chunk 72 [21197,21920): "cells at the top of charge. A BMS will therefore typically monitor the individual cell voltage ... cell balancin…（后续 500+ 字）"
```

chunk 72 从 chunk 71 的第 4 个字符处开始，向前延展 519 字符——chunk 71 的内容几乎 100% 被 chunk 72 携带。
这是真实文本重复，不是展示层误读。

## 3. 根因

```text
Index overlap（chunk_overlap + source_child_overlap / continuation）
  → 相邻子块 source span 重叠
  → 相邻子块都作为 direct hit / neighbor 被召回
  → Display / Context 阶段把每个 hit 的完整 content 原样输出
  → 重复展示同一 source span
```

**不是数据库重复写入**：chunk_id / content_hash / start_at 各不相同；`len(content)` 与
`end_at - start_at` 高度一致（95% 相差 ≤3，99.8% 相差 ≤10），说明内容确为重叠区间而非重复行。

### 字段可用性结论（回答阶段一排查问题）

1. 重复是**切块时真实 overlap**，非数据库重复写入。
2. 两个 chunk 拥有可靠 canonical offset：`chunks.start_at` / `chunks.end_at` 列。
3. `source_position_is_exact` 可靠：文本子块 3034/3034 = 100% 为 `true`。
4. start/end 保存在 `chunks.start_at` / `chunks.end_at`。
5. `source_node_ids`（85% 文本块有）可辅助：重叠对共享 node（如 node-000091），但粒度粗于 offset。
6. 重叠对属于**同一 parent_chunk_id**（同 section）。
7. UI 展示的是 context builder 清洗后的 content（`_cloud_content`），其长度≈raw content，非原始 DB 直接透传但内容相同。
8. MCP 同样返回重复文本（`know_rag_search_context` 与 `/llm-context` 走同一 context assembly 层）。

## 4. 当前 Retrieval → Context → UI/MCP 调用链

```text
routes_retrieval.hybrid_search / llm-context
        │
        ▼
retrieval_service.hybrid_search
        ├─ vector_search + keyword_search → merge_results (RRF) → rerank
        ├─ _expand_figure_bindings
        ├─ attach_context / build_parent_groups   ← Group Assembly（results + parent_groups）
        │
        ▼
KnowledgeContextService.build_for_knowledge_base
        ├─ return_mode="evidence_groups" → llm_context_builder.build_evidence_groups_payload   ← MCP 默认
        ├─ return_mode="chunks_with_selective_context" → llm_context_builder.build_cloud_flat_chunks_payload ← /llm-context 默认
        └─ return_mode="legacy" → llm_context_builder.build_llm_context_payload（废弃保留）
```

- `results`（raw hits）：`_row_to_result` 输出，带 `start_at`/`end_at`/`content`/`chunk_type`/`chunk_index`/`score`。
- `parent_groups[].parent_children`（neighbor 候选）：`_row_child_summary` 输出，**当前缺 `start_at`/`end_at`**，只有 `content_preview`。
- `parent_groups[].matched_children`：`_compact_child_from_result` 输出，**当前缺 `start_at`/`end_at`**。

## 5. 不修改的模块（硬边界）

Chunk overlap 参数、Canonical Parent-Child 切分、Embedding、ES 索引、BM25、Vector Search、RRF、
Rerank、FigureLinker、Relation Graph、Media Parent Assignment、图片绑定逻辑——**均不修改**。
`hybrid-search` 的 `results`（raw retrieval hit）**保持原样**（不重写 content）。

## 6. Retrieval Context Normalization 设计

目标链路（只新增 ★ 一层）：

```text
Raw Retrieval Hits
  → Neighbor Expansion / Group Assembly
  → ★ Retrieval Context Normalization（overlap detection / trimming / adjacent span merge）
  → UI / MCP / LLM Context
```

归一化发生在 **context builder 输出最终 chunk 之前**，只影响 `build_cloud_flat_chunks_payload` 与
`build_evidence_groups_payload` 两个产出物；不改 raw hits、不改 DB、不改索引。

## 7. Source-span overlap 判断规则

- 只处理 `chunk_type`/`kind == "text"`（含 `image_ocr_text` 归 text）的 chunk。table/image/equation 不参与文本去重。
- 仅当 `start_at`/`end_at` 均非空且 `metadata_json.source_position_is_exact == true`（文本块恒真）时启用 offset 去重。
- 归一化作用域 = **同 `document_id` + 同 `parent_chunk_id`**（group 内）。standalone root（无 parent）不受影响。
- 跨 parent 不默认合并（Case 6）。

overlap 定义为 source span 交集：

```text
overlap_ratio = source_span_intersection / min(span_a, span_b)
```

## 8. Merge / Trim 策略

优先级（deterministic）：**direct hit 是 anchor，永不 trim；neighbor 次之**。同优先级按
`score desc`、再按 `start_at asc` 排序，依次“认领”自己的 source span。

对每个 text chunk（按优先级顺序）计算其 `[start,end)` 被已认领区间覆盖的部分：

- **无重叠** → 内容完整保留。
- **前缀重叠**（前面 chunk 盖住 `[start, x)`）→ 从 content 头部裁掉 `x - start` 字符。
- **后缀重叠**（后面更高优先级 chunk 盖住 `[y, end)`）→ 裁掉尾部 `end - y` 字符。
- **完全包含**（span 全被更高优先级 chunk 覆盖）→ content 置空，标记 fully deduplicated，
  保留 `chunk_id` / `direct_hit` / `overlap_source_chunk_ids` 作为 evidence（Case A 合并）。
- 内部孤立覆盖（更高优先级 chunk 完全嵌在中间，极罕见）→ 保守保留完整 content，不裁中间。

char↔offset 映射按 1:1（99.8% 文本块 `len(content)` 与 `end-start` 相差 ≤10），裁剪后不再向
句/段边界回吸，接受 ±2~3 字符边界误差；如需更精细可在后续版本加入段落边界吸附。

每次处理后，把该 chunk 的**完整 span** 并入“已认领区间”，保证后续低优先级 chunk 不重复展示已认领 source。

输出标记（附加到 chunk payload，schema 向后兼容）：

```json
{
  "overlap_trimmed": true,
  "trimmed_chars": 204,
  "overlap_source_chunk_ids": ["<chunk-a>"]
}
```

## 9. fallback 策略

当前文本块 `source_position_is_exact` 100% 为 true，offset 是首选且唯一实际路径。
对极少数无 exact offset 的文本块（本轮实测为 0），fallback 顺序（仅保守，不删除真实重复）：

```text
source_node_ids（共享 node → 标记同源，不硬裁）
→ 同 parent 相邻 chunk_index + 精确 prefix/suffix 字符串重叠
→ 保留原样（宁可不裁，不误删）
```

**禁止**：因“语义相似”删除；禁止删除文档中两个不同位置真实重复出现的文字（Case C，canonical offset 不同必须保留）。

## 10. 自动化测试方案

新模块 `backend/app/services/retrieval_context_normalizer.py`（纯函数，可单测）。

`backend/tests/test_retrieval_context_normalizer.py` 覆盖 12 个 case（见任务书 Case 1~12）：

1. 精确 offset `A[0,100]` `B[80,180]` → B 前 20% trim。
2. 90% overlap → 合并后文本不重复。
3. 无 overlap → 全保留。
4. 相同字符串但不同 source offset → 不去重。
5. 同 parent 相邻 chunk overlap → 正常 trim。
6. 不同 parent 同连续 source span → 不跨 parent 归一化（默认）。
7. `source_position_is_exact=false` → 保守 fallback（不硬裁）。
8. `source_node_ids` 证明同源 → 可 dedup（辅助信号）。
9. standalone root → 不受影响。
10. table/image/equation → 不参与文本去重。
11. 两个 raw hit 均保留 evidence ID（chunk_id 不丢）。
12. 最终 display/context 文本中重复 span 只出现一次。

## 11. 真实知识库验收方案

用诊断脚本（见 §四）扫描真实库，抽取 ≥5 组此前明显重复的对，记录 Before/After：

```text
Before:  chunk A content / chunk B content / overlap_ratio
After:   display A / display B / trimmed_chars / merged span
```

重点复现用户场景：`[0027][0028]` vs `[0027][0028][0029]` 不得再展示成两遍 `[0027][0028]`，
应呈现为 `[0027][0028][0029]` 或 `Item A: [0027][0028]` + `Item B: [0029]`。

## 12. 风险与回滚方式

- 风险 1：offset→char 1:1 假设在个别 chunk 有 ±3 字符偏差 → 边界可能多裁/少裁几个字符。
  缓解：文本块 99.8% 偏差 ≤10，影响可控；后续可加段落边界吸附。
- 风险 2：anchor（direct hit）被完全包含时 content 置空 → 若该 hit 是唯一证据会丢正文。
  缓解：完全包含只发生在同 parent 内有更高优先级 hit 已覆盖该 span 时，正文仍在输出中；
  保留 `chunk_id`/`direct_hit`/`overlap_source_chunk_ids` 保证 evidence 不丢。
- 风险 3：归一化只作用于 text，不触碰 table/image/equation，不影响图片绑定（媒体走独立 dedup）。
- 回滚：归一化为独立纯函数 + 两个 builder 的一处调用点，可开关；回滚只需移除调用点，
  不影响 raw hits / 索引 / DB。

---

## 未修改模块确认

Chunking（chunk_overlap / parent-child / WeKnora two-pass / DCP3.x）、Embedding、ES 索引、BM25、
Vector Search、RRF、Rerank、FigureLinker、Relation Graph、Media Parent Assignment、图片绑定——
均未修改。仅新增 Retrieval Context Normalization 一层 + 必要的 group assembly 字段透传
（`parent_children` / `matched_children` 补 `start_at`/`end_at`）。
