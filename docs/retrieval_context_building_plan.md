# 检索上下文构建：绑定感知改造方案

> 状态：In Progress（Phase 1-2 已实施，Phase 3-4 待定）
> 维护日期：2026-08-10
> 前置依赖：[image_chunk_binding_plan.md](image_chunk_binding_plan.md) (P0-P5 已完成，Final Media Parent Assignment 已实施)
> 关联文档：[pipeline_audit.md](pipeline_audit.md)、[document_relation_graph_phased_plan.md](document_relation_graph_phased_plan.md)

## 1. 问题陈述

P0-P4 图文绑定系统已在解析阶段产出了精确的、带置信度的 chunk 间关系数据（`figure_references[]`、`image_bindings[]`、`outgoing_asset_chunk_ids[]`、`incoming_reference_chunk_ids[]`），写入 `metadata_json`。但检索到 LLM 的上下文构建链路完全没有消费这些数据。

当下"如何把命中块周围的内容组装给大模型"这一环节，靠的是**同 parent 内按 chunk_index 排序 + 内容文本正则猜谜**，与绑定系统产出的确定性关系数据完全脱节。

## 2. 现状分析

### 2.1 数据生产（已就绪）

P4 物化阶段 (`materialize_figure_bindings` + `materialize_relation_summaries`) 写入 `metadata_json`：

| 存储位置 | 字段 | 含义 | 示例值 |
|---------|------|------|--------|
| 正文 chunk `metadata_json` | `figure_references[]` | 该正文引用了哪些图片 | `[{figure_id:"figure:3", image_chunk_ids:["c01","c02"], binding_score:0.9}]` |
| 图片 chunk `metadata_json` | `image_bindings[]` | 哪些正文引用了该图 | `[{figure_id:"figure:3", reference_chunk_ids:["t01"], binding_score:0.9}]` |
| 正文 chunk `metadata_json` | `outgoing_asset_chunk_ids[]` | 该正文关联的图片 chunk ID（来自 relation graph） | `["c01","c02"]` |
| 图片 chunk `metadata_json` | `incoming_reference_chunk_ids[]` | 引用该图的正文 chunk ID | `["t01","t03"]` |

**`relation_chunks` DB 列（JSON, default `[]`）始终为空** — 物化器从未写入此列。

### 2.2 数据消费（断点）

唯一消费 binding 数据的代码路径是 `expand_figure_bindings()`（[document_relation_expansion_service.py:103](../backend/app/services/document_relation_expansion_service.py#L103)），由 feature flag `DOCUMENT_RELATION_RETRIEVAL_EXPANSION_ENABLED` 控制且**默认 `false`**。该方法读取 `figure_references[].image_chunk_ids` 和 `image_bindings[].reference_chunk_ids`，从 DB 批量拉取目标 chunk，追加到检索结果列表。

上下文构建器 `build_cloud_flat_chunks_payload()` 和 `build_llm_context_payload()` **不读取任何 binding 元数据**。

### 2.3 上下文构建器当前策略（纯猜谜）

`_cloud_needs_expansion()`（[llm_context_builder.py:1250](../backend/app/services/llm_context_builder.py#L1250)）的触发规则：

| 规则 | 触发条件 | 扩展行为 | 缺陷 |
|------|---------|---------|------|
| `figure_or_table_reference` | 正文含 "图X"/"表X"/"Fig.X" | 从同 parent siblings 遍历找 image/table chunk | 只能同 parent 内找，跨 parent 的图找不到；已经知道精确 chunk ID 却不用 |
| `forward_reference` | 含 "上述/如下/as shown" | 补前一个 text chunk | 不读 `relation_chunks` |
| `nearby_multi_hits` | 同 parent 内两个命中 index gap ≤ 2 | 补中间 text chunk | 不读 `relation_chunks` |
| `short_but_valid` | 内容 < 200 字符 | 补最近的有 token 重叠的 text | 不读 `relation_chunks` |
| `neighbor_query_overlap` | query token 出现在相邻 chunk | 补该 chunk | 最弱信号，可能引入噪音 |

`_cloud_build_window_for_parent()`（[line 1310](../backend/app/services/llm_context_builder.py#L1310)）的扩展优先级：

```
1. figure/table ref → 同 parent siblings 中找 media chunk  ← 猜
2. nearby multi-hit → 补命中间 text chunks                ← 猜
3. forward reference → 补前一个 text chunk                  ← 猜
4. short / overlap → 补最近有效 text chunk                  ← 猜
```

**核心问题：当正文精确写"如图3所示"，系统通过 P2 FigureLinker 已经知道图3是哪个图片 chunk，但在上下文构建时却忽略这个信息，改为在同 parent 的 siblings 列表中盲找。**

### 2.4 三个断点总结

```
解析管线（P0-P4）          检索管线               上下文构建
═══════════════          ══════════             ══════════

精确绑定 ✓              扩展关闭 ✗             不读元数据 ✗
figure_references        flag=false             _cloud_needs_expansion
image_bindings           不读 binding           纯位置猜谜
outgoing_asset_chunk_ids 不扩展结果             不跨 parent
```

## 3. 改造方案

### Phase 1: `relation_chunks` 落盘

**目标**：物化完成后 `relation_chunks` 包含确定性的直接关联 chunk ID 列表。

**文件**：[`backend/app/services/document_relation/materializer.py`](../backend/app/services/document_relation/materializer.py)

**改动**：`materialize_figure_bindings()` 和 `materialize_relation_summaries()` 末尾，对参与 chunk 计算并写入 `relation_chunks`：

```python
# 对每个 chunk，合并所有确定性关联：
relation_chunks = list(dict.fromkeys([
    # 来自 figure_references 的图片
    *[img_id for ref in metadata.get("figure_references", [])
       for img_id in (ref.get("image_chunk_ids") or [])],
    # 来自 relation graph 的出边
    *metadata.get("outgoing_asset_chunk_ids", []),
    # 来自 image_bindings 的正文
    *[ref_id for binding in metadata.get("image_bindings", [])
       for ref_id in (binding.get("reference_chunk_ids") or [])],
    # 来自 relation graph 的入边
    *metadata.get("incoming_reference_chunk_ids", []),
]))

chunk.relation_chunks = [cid for cid in relation_chunks if cid != str(chunk.id)]
```

`indirect_relation_chunks` 暂不填充，预留给后续同页/同节间接关系。

**迁移**：新增 Alembic 迁移脚本，从存量 chunk 的 `metadata_json` 回填 `relation_chunks`：

```sql
-- 对已有 binding 数据的存量 chunk，从 metadata_json 提取并回填
UPDATE chunks
SET relation_chunks = ...
WHERE metadata_json ? 'figure_references'
   OR metadata_json ? 'image_bindings'
   OR metadata_json ? 'outgoing_asset_chunk_ids';
```

**影响**：
- 新解析文档：物化阶段自动写入
- 存量文档：需执行迁移脚本（或等待 reparse）
- 无 binding 数据的文档：`relation_chunks` 保持 `[]`

### Phase 2: 上下文构建器消费 binding 数据

#### Phase 2A: `build_parent_groups` 预拉取关联 chunk

**文件**：[`backend/app/services/retrieval_service.py`](../backend/app/services/retrieval_service.py)

**改动**：`build_parent_groups()`（[line 1325](../backend/app/services/retrieval_service.py#L1325)）在构建每个 parent_group 时，收集所有命中 child 的 `relation_chunks`，批量查询 DB，附加到 group：

```python
# build_parent_groups 内，对每个 group：
def _collect_binding_related(
    db: Session,
    matched_children: list[dict],
    parent_children_ids: set[str],
) -> list[dict]:
    """拉取命中 chunk 的 relation_chunks 中、不在 parent_children 内的 chunk。"""
    related_ids: list[str] = []
    for child in matched_children:
        meta = child.get("metadata_json") or {}
        if isinstance(meta, dict):
            # 优先用 relation_chunks（Phase 1 落盘后）
            rc = meta.get("relation_chunks") or child.get("relation_chunks") or []
            related_ids.extend(rc)
    related_ids = list(dict.fromkeys(related_ids))  # 去重保序
    new_ids = [cid for cid in related_ids if cid not in parent_children_ids]
    if not new_ids:
        return []
    return _fetch_chunks_by_ids(db, new_ids)  # 新增辅助函数
```

**关键设计决策**：

- **拉取时机**：在 `build_parent_groups` 中而非 `attach_context` 中，因为 parent_groups 是上下文构建器的直接输入
- **去重逻辑**：如果关联 chunk 已在 `parent_children` 中（同 parent sibling），不重复拉取
- **DB 查询**：批量 `SELECT ... WHERE id IN (...)`，一次查询覆盖所有 group
- **Fail-open**：查询失败不阻断上下文构建，降级为纯规则模式

#### Phase 2B: 重写上下文扩展逻辑（binding 优先 + 规则兜底）

**文件**：[`backend/app/services/llm_context_builder.py`](../backend/app/services/llm_context_builder.py)

**改动**：`_cloud_build_window_for_parent()`（[line 1310](../backend/app/services/llm_context_builder.py#L1310)）重写扩展逻辑。

**新优先级**：

```
1. binding 精确命中 → 从 binding_related_chunks 拉图片/表格/公式 → 加入 media
2. binding 精确命中 → 从 binding_related_chunks 拉关联正文 → 加入 before/after
3. figure_or_table_reference 规则命中但 binding 无数据 → 同 parent siblings（旧逻辑兜底）
4. forward_reference / short / nearby_multi_hits → 保留现有规则
```

**具体实现**：

```python
def _cloud_build_window_for_parent(...):
    # ... 现有 preamble 不变 ...

    # 从 parent_group 读取 Phase 2A 预拉取的 binding 数据
    binding_related = parent_group.get("binding_related_chunks") or []
    binding_by_id = {_cloud_chunk_id(c): c for c in binding_related if _cloud_chunk_id(c)}

    # === 新增：binding 优先扩展 ===
    hit_meta = {}
    for h in hit_chunks:
        meta = h.get("metadata_json") or {}
        if isinstance(meta, dict):
            hit_meta[_cloud_chunk_id(h)] = meta

    # 1) 从 figure_references 精确拉图片
    for h in hit_chunks:
        meta = hit_meta.get(_cloud_chunk_id(h), {})
        for ref in meta.get("figure_references", []):
            for img_id in (ref.get("image_chunk_ids") or []):
                c = binding_by_id.get(img_id)
                if c and can_add(c, media_ok=True):
                    add_media(c)

    # 2) 从 outgoing_asset_chunk_ids 补遗
    for h in hit_chunks:
        meta = hit_meta.get(_cloud_chunk_id(h), {})
        for asset_id in meta.get("outgoing_asset_chunk_ids", []):
            c = binding_by_id.get(asset_id)
            if c and can_add(c, media_ok=True):
                add_media(c)

    # === 以下保留现有规则（3-4），但在 media 阶段跳过已有 binding 的 ===
    # figure_or_table_reference 规则：只处理 binding 未覆盖的
    if "figure_or_table_reference" in rules:
        for c in ordered:
            if _cloud_kind(c) in ("image", "table") and can_add(c):
                # 只有不在 binding media 中的才加入
                if _cloud_chunk_id(c) not in included:
                    add_media(c)
    # ... 其余规则不变 ...
```

**字符预算分配**：

| 优先级 | 来源 | 类型 | 计入 |
|--------|------|------|------|
| 0 | binding `figure_references` | image/table/equation | media（优先，不占 text 配额） |
| 1 | binding `outgoing_asset_chunk_ids` | image/table | media |
| 2 | binding `incoming_reference_chunk_ids` | text | before/after |
| 3 | 规则 `figure_or_table_reference` | image/table（同 parent 兜底） | media |
| 4 | 规则 `forward_reference` | text（前一个 sibling） | before |
| 5 | 规则 `nearby_multi_hits` | text（命中之间） | between |
| 6 | 规则 `short_but_valid` / `neighbor_query_overlap` | text（最近有效） | before/after |

**上下文标注**：binding 来源的 chunk 在 `context_policy` 中标注 `strategy: "binding_aware"`，与纯规则扩展的 `strategy: "rule_based"` 区分，便于调试和后续调优。

#### Phase 2C: 检索扩展默认开启

**文件**：[`backend/app/core/config.py`](../backend/app/core/config.py)

**改动**：`DOCUMENT_RELATION_RETRIEVAL_EXPANSION_ENABLED` 默认值 `False` → `True`。

这是最小的单行改动。开启后 `expand_figure_bindings()` 默认运行，将关联图片/正文追加到检索结果。在 Phase 2A/2B 完成后，该扩展的价值从"影响 LLM 上下文"降为"影响前端检索结果展示"——上下文构建器已有自己的 binding 消费路径。两项并行不冲突：扩展追加的是检索结果列表项，上下文构建器追加的是上下文窗口内的 chunk。

### Phase 3: 图片上下文展示优化（可选）

**目标**：有 binding 关系的图片内容紧挨引用正文展示，而非放到末尾装饰区。

**文件**：[`backend/app/services/llm_context_builder.py`](../backend/app/services/llm_context_builder.py)

**改动**：legacy builder `build_llm_context_payload()`（[line 623](../backend/app/services/llm_context_builder.py#L623)）当前的"相关图表/表格/公式"区（line 763-765）将所有非命中 child 统一放到末尾。改为：

- 有 `figure_references` 匹配的图片 → 紧挨对应正文展示（`role: "figure_binding"`）
- 无绑定的图片/表格 → 保留现有"相关图表"区

**注意**：此 Phase 优先级低，因为 legacy builder 已不是默认路径（Cloud Flat 是默认路径）。

### Phase 4: 图片 URL 策略（可选）

**目标**：为多模态模型接入做准备。

当前 `include_image_urls` 默认 `false` — 图片 chunk 只送文字描述（VLM description/caption/OCR），不送 URL。这对纯文本 LLM 合理，但多模态模型需要 URL。

**改动**：不改变默认值。确保 binding 图片的文字描述优先级高于普通 caption（VLM > description > figure_caption > OCR），使得即使不开 URL，LLM 也能获得最丰富的图片语义。

## 4. `relation_chunks` 拉取机制详述

### 4.1 数据写入（Phase 1）

```
rebuild_document_chunks()
  └─ _rebuild_canonical_weknora_parent_child()
       ├─ FigureLinker.link_figure_references()  → 产出 FigureLinkResult
       ├─ parent/child split                     → 产出 Chunk 列表
       ├─ _apply_figure_bindings()               → materialize_figure_bindings()
       │     └─ 写入 metadata_json.figure_references / image_bindings
       │     └─ [新增] 写入 chunk.relation_chunks
       └─ materialize_relation_summaries()       → 写入 outgoing/incoming + relation_summaries
             └─ [新增] 合并写入 chunk.relation_chunks
```

### 4.2 数据读取（Phase 2）

```
hybrid_search()
  ├─ vector + keyword → RRF merge
  ├─ [Phase 2C] expand_figure_bindings() → 检索结果层面追加关联 chunk
  ├─ attach_context()          → 加载同 parent siblings（不改动）
  ├─ build_parent_groups()     → [Phase 2A 改动] 收集 relation_chunks, 批量查询 DB
  │     └─ 每个 group 新增字段:
  │         binding_related_chunks: [{id, content, chunk_type, metadata_json, ...}, ...]
  └─ → KnowledgeContextService.build_for_knowledge_base()
       └─ build_cloud_flat_chunks_payload()
            └─ _cloud_build_window_for_parent()  → [Phase 2B 改动] binding 优先
                  ├─ 1. 读 parent_group["binding_related_chunks"]
                  ├─ 2. 匹配 figure_references[].image_chunk_ids → 精确拉图片
                  ├─ 3. 匹配 outgoing_asset_chunk_ids → 补遗图片
                  ├─ 4. figure_or_table_reference 规则 → 兜底同 parent 图片
                  └─ 5-6. 其余规则兜底
```

### 4.3 查询模式

`relation_chunks` 的拉取采用**批量预取 + 内存匹配**模式，而非逐 hit 查询 DB：

```python
# build_parent_groups 阶段一次性收集所有需要的 ID
all_relation_ids = set()
for group in groups:
    for child in group["matched_children"]:
        rc = child.get("relation_chunks") or []
        all_relation_ids.update(rc)

# 单次批量查询
rows = db.query(Chunk).filter(
    Chunk.id.in_(list(all_relation_ids)),
    Chunk.is_enabled == True,
).all()

# 按 group 分发
rows_by_id = {row.id: row for row in rows}
for group in groups:
    group["binding_related_chunks"] = [
        _row_to_summary(rows_by_id[cid])
        for cid in group_relation_ids
        if cid in rows_by_id
    ]
```

**为什么不用 JOIN**：parent_groups 的数量通常在 3-10 之间，每个 group 的 relation_chunks 在 0-20 之间，总 ID 数远小于一次 DB round-trip 的阈值。批量 `WHERE id IN (...)` 最直接。

## 5. 实施优先级

| 优先级 | Phase | 改动范围 | 工作量 | 影响 |
|--------|-------|---------|--------|------|
| **P0** | Phase 1: relation_chunks 落盘 + 迁移 | materializer.py + migration | 小 | 数据基础，无行为变化 |
| **P1** | Phase 2A: build_parent_groups 预拉取 | retrieval_service.py | 中 | 支撑 Phase 2B |
| **P1** | Phase 2B: 上下文构建器 binding 优先 | llm_context_builder.py | 中 | 核心改进，结束猜谜 |
| **P2** | Phase 2C: 扩展默认开启 | config.py | 极小（1行） | 前端展示联动 |
| **P3** | Phase 3: 图片紧挨引用展示 | llm_context_builder.py（legacy） | 小 | UX 优化 |
| **P4** | Phase 4: URL 策略 | llm_context_builder.py | 极小 | 多模态准备 |

## 6. 开关与回滚

- Phase 1 无行为变化，纯数据落盘，无需开关
- Phase 2A/2B 引入新的上下文扩展逻辑，**建议新增 feature flag** `DOCUMENT_RELATION_CONTEXT_BUILDING_ENABLED`（默认 `true`），关闭时退化为纯规则模式
- Phase 2C 将现有 flag 默认值改为 `true`，可直接 revert
- 所有 binding 消费路径 fail-open：DB 查询失败、metadata 缺失、relation_chunks 为空时，自动降级为现有规则

## 7. 测试要点

| Phase | 测试内容 |
|-------|---------|
| Phase 1 | 新解析文档 `relation_chunks` 非空；迁移脚本存量回填正确；幂等性（重复执行不产生重复 ID） |
| Phase 2A | `binding_related_chunks` 正确分发到各 parent_group；跨文档隔离；去重逻辑 |
| Phase 2B | binding 图片优先于规则图片；`strategy` 字段正确标注；关闭 flag 后退化为纯规则 |
| Phase 2C | 扩展开启后检索结果数量变化；前后端兼容 |
| 全链路 | `rebuild_document_chunks()` → `hybrid_search()` → `build_cloud_flat_chunks_payload()` 端到端；已知测试文档的上下文输出对比 |
