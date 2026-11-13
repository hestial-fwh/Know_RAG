# 融合检索设计：二路召回 + GraphRAG + SAG

> 状态：设计稿（Planned，未实施）
> 日期：2026-11-10
> 适用范围：`know_rag` 在线检索链路（召回 → 融合 → 重排 → 上下文组装）
> 关联文档：`dual_mode_retrieval_design.md`、`retrieval_p3_replay.md`、`retrieval_p4_implementation.md`、`retrieval_p5_implementation.md`
> 本文只描述目标设计与改造边界，不代表代码已实现，也不代表业务质量已验收。

---

## 0. 摘要

当前系统已经具备四类检索能力：

1. 向量召回（pgvector / 外部向量库）；
2. BM25 召回（Elasticsearch）；
3. GraphRAG Local：基于预建实体关系的邻域扩展；
4. SAG 式事件关联：基于事件—实体索引的查询时动态跳转；
5. GraphRAG Global：基于社区报告的 map-reduce 摘要。

问题不在于缺少组件，而在于这些能力被策略层**互斥选择**：`hybrid`、`graph.local`、`event_graph`、`graph.global` 一次只能走一条。本文提出新增 `tier="fusion"` 组合档，在同一个请求内同时执行基础召回、关系扩展和事件关联，并在证据层做两级融合；Global 摘要作为独立的背景层，不进入证据 RRF。

核心原则：

- **chunk_id 是唯一通货**：所有图通道必须映射回当前有效原文切片。
- **两级融合**：先在图通道内部融合 relations / events，再与基础通道融合，避免“条数多的通道”天然占优。
- **一个 EvidenceReader**：Local 和 SAG 共用同一份来源缓存、seeds 和预算，不重复扫描。
- **摘要是背景，不是证据**：Global 派生摘要不得混入证据池，也不得作为原文引用。
- **按通道路由**：fusion 不是新的默认值，只在对的意图、且索引就绪时启用。

---

## 1. 现状与差距

### 1.1 现有实现

| 能力 | 主要位置 | 当前行为 |
|---|---|---|
| 向量召回 | `services/retrieval_service.py::vector_search` | pgvector 余弦；外部库失败按配置回退 pgvector |
| BM25 | `services/retrieval_service.py::keyword_search` | ES `multi_match` + ngram；短查询启用 ngram |
| 基础融合 | `merge_results_rrf` / `merge_results_weighted` | 默认 RRF，`k=60`，权重 1:1 |
| 策略入口 | `services/retrieval/policy_resolver.py` | 请求 > KB profile > 默认；单 tier 选择 |
| 自动路由 | `services/retrieval/auto_router.py` | 规则选 `semantic/hybrid/graph/event_graph` 之一 |
| GraphRAG Local | `services/retrieval/associations.py::_local` | 实体种子 → 最多 2 跳关系 → 回查 chunk；逐跳校验版本/哈希/引文 |
| SAG 事件关联 | `services/retrieval/associations.py::_events` | 事件 + 共享实体动态展开；抑制高频泛称实体 |
| GraphRAG Global | `services/retrieval/global_search.py` | 社区报告 map-reduce → 派生要点 + 支持原文；独立策略 |
| 证据选择 | `associations.py::rank_associations` / `select_association_evidence` | 基础 + 单一关联通道 RRF；按完整证据链占用 Top K |
| 去重 | `services/retrieval/source_dedup.py` | 多通道来源归一化；精确表格按来源键去重 |
| 上下文 | `services/context/knowledge_context_service.py`、`llm_context_builder.py` | `evidence_groups.v1`、`context_text` 两种出口 |

### 1.2 差距

| # | 差距 | 影响 |
|---|---|---|
| 1 | `_execute_chunk_search` 只接受单个 `_association_kind` | 关系扩展和事件关联不能同时执行 |
| 2 | `policy_resolver` 单 tier 选择 | `graph.local` 与 `event_graph` 互斥，无法组合 |
| 3 | `rank_associations` 只融合 `hybrid + association` | 多图通道没有权重、配额和两级融合 |
| 4 | Global 是独立执行路径 | 无法作为 fusion 的背景层；它也不跑基础召回 |
| 5 | `EvidenceReader` 每个图通道各建一次 | 融合后可能重复扫描最多 1000 篇文档，延迟翻倍 |
| 6 | `evidence_groups.v1` 没有背景层 | Global 摘要要么整体替换结果，要么无处安放 |
| 7 | 缺少逐通道诊断 | 无法回答“哪条通道贡献了什么、为什么没执行” |

### 1.3 术语对齐

- **GraphRAG Local**：本项目指基于 `knowledge_relations` 的实体邻域扩展，属于自建参考实现，不宣称完整复现 Microsoft GraphRAG。
- **GraphRAG Global**：基于 `graph_communities` / `community_reports` 的社区摘要 map-reduce，产物是派生要点。
- **SAG**：本项目指参考 Zleap-AI/SAG 的事件—实体索引与查询时动态关联，即现有 `event_graph` 家族；不宣称逐行复现官方算法，也不宣称其论文指标。
- **fusion**：本文新增的组合检索档，同时执行基础通道、关系通道、事件通道，可选附带 Global 背景。

---

## 2. 目标与非目标

### 2.1 目标

1. 新增一个显式策略档 `tier="fusion"`，允许在同一请求内组合：
   - 向量 + BM25；
   - GraphRAG Local（relations）；
   - SAG 事件关联（events）；
   - 可选 GraphRAG Global 背景（community reports）。
2. 所有图候选映射回当前有效 chunk，做统一去重和两级 RRF。
3. 给每条通道独立的候选预算、权重、配额和诊断。
4. Global 摘要进入背景层，不进入证据 RRF，不作为原文引用。
5. 保持旧策略、旧请求、旧 MCP 调用行为不变。
6. 单通道失败可降级，不影响其他通道；严格模式仍可整体报错。

### 2.2 非目标

- 不引入 Neo4j 或其他图数据库；继续使用 PostgreSQL 关系表/事件表。
- 不重写切块、向量化、BM25 索引和现有上下文主链路。
- 不把 fusion 设为默认策略；默认仍是 `hybrid`。
- 不实现后端答案生成；生成仍由外部 Agent 负责。
- 不声称 SAG 官方算法复现或 GraphRAG 全量能力复现。
- 不在首期做跨请求的图缓存；只做单请求内共享。

---

## 3. 总体架构

```text
                         ┌──────────────────────────────┐
                         │  查询理解 / 意图路由          │
                         │  实体别名、query 类型、预算   │
                         └──────────────┬───────────────┘
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        │                               │                               │
        ▼                               ▼                               ▼
┌───────────────┐               ┌───────────────┐               ┌────────────────┐
│ 基础通道       │               │ 结构通道       │               │ 背景通道（可选）│
│ vector + BM25 │               │ relations      │               │ community       │
│               │               │ + events       │               │ reports         │
└───────┬───────┘               └───────┬───────┘               └───────┬────────┘
        │                               │                               │
        │  chunk 候选                    │  chunk 候选 + 路径             │  派生摘要
        └───────────────┬───────────────┘                               │
                        ▼                                               │
              ┌───────────────────────┐                                 │
              │ 来源归一化 / 去重      │                                 │
              │ chunk_id 为通货        │                                 │
              └──────────┬────────────┘                                 │
                         ▼                                              │
              ┌───────────────────────┐                                 │
              │ 两级 RRF + 通道配额    │                                 │
              └──────────┬────────────┘                                 │
                         ▼                                              │
              ┌───────────────────────┐                                 │
              │ 重排 + 证据链选择      │                                 │
              └──────────┬────────────┘                                 │
                         ▼                                              ▼
              ┌───────────────────────┐                     ┌────────────────────┐
              │ 证据层 chunks          │                     │ 背景层 summaries    │
              │ 可引用、可回查         │                     │ derived、不可引用   │
              └──────────┬────────────┘                     └─────────┬──────────┘
                         └──────────────────┬──────────────────────────┘
                                            ▼
                              ┌──────────────────────────┐
                              │ evidence_groups / context │
                              └──────────────────────────┘
```

---

## 4. 设计决策

### 4.1 用 `tier="fusion"`，不新增家族

现有 `TieredStrategy` 已经有 `semantic / hybrid / graph / auto` 四档，`policy_resolver`、`orchestrator`、回退逻辑都围绕 tier 展开。新增一个 tier 比重建 `family` 的改动面小，也不会改变 `event_graph` 家族的现有行为。

```python
tier: Literal["semantic", "hybrid", "graph", "fusion", "auto"] = "hybrid"
```

校验规则：

- `tier="fusion"` 时 `graph_method` 必须为空。
- `tier="fusion"` 时基础通道强制开启，不接受与之冲突的 `use_vector=False` / `use_keyword=False`。
- `tier="fusion"` 至少需要 `relations` 或 `events` 之一就绪；两者都未就绪时按 `fallback_policy` 处理。
- 旧值 `semantic / hybrid / graph / auto` 行为不变。
- `event_graph` 家族保留，不迁移、不改语义。

### 4.2 一个 `EvidenceReader`，两个图执行器

`EvidenceReader.__init__` 会扫描最多 1000 篇文档，建立 manifest、source、coverage 缓存。融合模式必须只建一次，`relations` 和 `events` 共用：

- 同一批 seeds；
- 同一份来源快照；
- 同一套版本/哈希/引文校验；
- 同一份总预算。

执行器层面保留两个独立函数，输出按通道打标签，便于配额和诊断。

### 4.3 两级融合，不做四路平铺 RRF

平铺 RRF 的问题是：一个通道返回 50 条，另一个返回 10 条，前者的总票数天然更高。两级融合把“结构”当作一个整体先内部消化，再与基础通道比较：

```text
第一级：relations + events → structure 排名
第二级：base + structure  → 最终证据排名
```

### 4.4 Global 摘要与证据物理隔离

社区摘要是模型生成的派生文本，没有可逐字回查的原文区间。它只能出现在 `background`，必须带 `derived=true`、`citation_allowed=false`。Global 的支持 chunk 是真实原文，可以作为低权重通道并入证据池，但摘要本身不能。

### 4.5 按通道路由，不默认全开

一次 fusion 的代价是两次图扩展，外加可选的社区模型调用。单跳事实、定义、短 query 不需要这些。路由规则先判断意图和能力，再决定是否进入 fusion；不确定时保持 hybrid。

---

## 5. 契约设计

### 5.1 策略 schema

在 `app/schemas/retrieval_strategy.py` 扩展：

```python
class AssociationBudget(BaseModel):
    # 现有字段保持不变
    candidate_k: int = Field(default=20, ge=1, le=100)
    max_candidates: int = Field(default=50, ge=1, le=100)
    max_hops: int = Field(default=2, ge=1, le=2)
    max_fanout: int = Field(default=5, ge=1, le=10)
    max_seeds: int = Field(default=8, ge=1, le=20)

    # fusion 新增：按通道配额
    relation_quota: int = Field(default=30, ge=1, le=100)
    event_quota: int = Field(default=20, ge=1, le=100)

    # fusion 新增：时间与模型预算
    structure_timeout_ms: int = Field(default=800, ge=50, le=5000)
    global_timeout_seconds: int = Field(default=45, ge=1, le=180)
    max_global_model_calls: int = Field(default=2, ge=0, le=5)
    max_global_reports: int = Field(default=2, ge=0, le=12)


class TieredStrategy(BaseModel):
    tier: Literal["semantic", "hybrid", "graph", "fusion", "auto"] = "hybrid"
    graph_method: Literal["local", "global", "drift"] | None = None
    include_global: bool = False
    fallback_policy: Literal["hybrid", "error"] = "hybrid"
    budget: AssociationBudget = Field(default_factory=AssociationBudget)
```

校验：

```python
@model_validator(mode="after")
def validate_fusion(self):
    if self.tier != "graph" and self.tier != "fusion" and self.graph_method is not None:
        raise ValueError("只有 graph/fusion 级别允许 graph_method")
    if self.tier == "fusion" and self.graph_method is not None:
        raise ValueError("fusion 不接受 graph_method")
    if self.include_global and self.tier not in {"fusion", "auto"}:
        raise ValueError("include_global 只对 fusion/auto 生效")
    return self
```

### 5.2 请求示例

```json
{
  "query_text": "甲校新增专业有哪些实践合作资源？",
  "top_k": 10,
  "return_mode": "evidence_groups",
  "strategy": {
    "family": "tiered",
    "tier": "fusion",
    "include_global": false,
    "fallback_policy": "hybrid",
    "budget": {
      "candidate_k": 20,
      "max_candidates": 50,
      "max_hops": 2,
      "max_fanout": 5,
      "relation_quota": 30,
      "event_quota": 20,
      "structure_timeout_ms": 800
    }
  }
}
```

### 5.3 诊断输出

在 `meta.retrieval` 下新增 `fusion`，同时保留现有 `association` 投影以保证旧消费者可用：

```json
{
  "fusion": {
    "requested": ["vector", "keyword", "relation", "event", "global_background"],
    "executed": ["vector", "keyword", "relation"],
    "channel_status": {
      "vector": "ready",
      "keyword": "ready",
      "relation": "ready",
      "event": "unavailable",
      "global": "skipped_not_requested"
    },
    "candidate_counts": {
      "vector": 20,
      "keyword": 20,
      "relation": 31,
      "event": 0,
      "global_support": 0
    },
    "structure_paths": {"relation": 7, "event": 0},
    "rrf": {
      "base": 1.0,
      "structure": 0.8,
      "relation": 1.0,
      "event": 0.8
    },
    "quota": {"relation": 30, "event": 20, "reserved_slots": 2},
    "elapsed_ms": {"base": 180, "structure": 220, "global": 0},
    "coverage_incomplete": false
  },
  "association": {
    "executed": true,
    "kind": "fusion",
    "paths": []
  }
}
```

`association.kind` 在融合模式下为 `"fusion"`；`association.paths` 合并两类路径，保持旧前端/上下文代码可读。

### 5.4 兼容性

- 旧请求不传 `strategy`：行为完全不变。
- 旧 `family="event_graph"`：行为不变。
- 旧 `tier="graph"`：行为不变。
- 新增字段全部有默认值，已存储的 KB profile 可继续通过 Pydantic 校验。
- 不新增数据库列；配额和时间预算放在 `retrieval_profile` JSON 中。
- `retrieval_mode` 别名不映射到 fusion；fusion 必须显式传 `strategy`。

---

## 6. 执行流程

### 6.1 策略解析

`policy_resolver.resolve_policy` 增加 fusion 分支：

```python
if isinstance(strategy, TieredStrategy) and strategy.tier == "fusion":
    kinds = [k for k in ("relations", "events") if (capabilities or {}).get(k)]
    if not kinds:
        if strategy.fallback_policy == "error":
            raise ValueError("fusion 需要 relations 或 events 索引")
        return RetrievalPlan(normalized, requested, hybrid_executed, degraded=True,
                             reason="关系/事件索引均未就绪，已回退混合检索")
    normalized["_association_kinds"] = kinds
    normalized["_association_budget"] = strategy.budget.model_dump()
    if strategy.include_global and (capabilities or {}).get("global"):
        normalized["_global_background"] = True
    return RetrievalPlan(normalized, requested, executed)
```

`orchestrator.execute_search`：

- tier 为 `fusion` 时也创建 `EvidenceReader`；
- capabilities 同时计算 `relations`、`events`、`global`、`entity_matches`；
- 不设置 `_global_search`（那是独立替换结果的路径），只设置 `_global_background`；
- 单通道能力缺失时记录 `channel_status`，不整体回退。

### 6.2 基础召回

保持现有顺序和预算：

1. `vector_search`：`recall_k = candidate_k`；
2. `keyword_search`：`recall_k = candidate_k`；
3. `normalize_channels` 做来源归一化；
4. 每路重新排 1..N；
5. `merge_results` 生成基础排名。

### 6.3 结构通道

新增统一入口，seeds 只算一次：

```python
def search_structure_channels(reader, query, direct, kinds, budget, include_content):
    seeds = _seeds(reader, query, direct, budget["max_seeds"])
    batches, metas = {}, {}

    if "relations" in kinds:
        rel_budget = _slice_budget(budget, "relations")
        paths, truncated = _local(reader, seeds, rel_budget)
        batches["relations"], metas["relations"] = _materialize(
            paths, kind="relations", include_content=include_content,
            budget=rel_budget, truncated=truncated,
        )

    if "events" in kinds:
        evt_budget = _slice_budget(budget, "events")
        paths, truncated = _events(reader, seeds, query, direct, evt_budget)
        batches["events"], metas["events"] = _materialize(
            paths, kind="events", include_content=include_content,
            budget=evt_budget, truncated=truncated,
        )

    return batches, metas
```

`_materialize` 的职责：

- 把路径支持的 chunk 转成统一结果结构；
- 给每条候选打 `retrieval_sources: ["relation"]` 或 `["event"]`；
- 保存 `association_paths`；
- 同一 chunk 被多条路径支持时，只保留最佳路径参与排序，其余路径保留为解释。

### 6.4 背景通道

仅当 `_global_background` 为真时执行：

```python
summaries, support, global_meta = await retrieve_global_background(
    db, kb_id, query,
    budget={
        "max_reports": budget["max_global_reports"],
        "max_model_calls": budget["max_global_model_calls"],
        "timeout_seconds": budget["global_timeout_seconds"],
    },
    top_k=options.top_k,
)
```

- 失败或超时：`global_meta.status="failed|timeout"`，证据链路继续；
- 摘要只写入 `meta.retrieval.global.derived_summaries` 和上下文 `background`；
- 支持 chunk 作为 `global_support` 低权重通道，仅在未被基础/结构通道覆盖时加入；
- 执行前后仍复用现有 `current_reports` 的 revision 校验。

### 6.5 融合与选择

```text
base = hybrid(vector, keyword)
structure = rrf(relations, events)
evidence = rrf(base, structure)
evidence = rerank(evidence)
evidence = select_fusion_evidence(evidence, top_k, quotas)
```

顺序要点：

1. 先做通道内排序，再跨通道 RRF；
2. 重排在完整融合池上做，不在截断后做；
3. 证据链选择在重排后做，确保长路径不被提前截断；
4. 最后再做图片绑定扩展和父块上下文。

### 6.6 上下文组装

- `evidence_groups`：新增可选 `background` 数组；
- `context_text`：在资料段之前增加背景段；
- `chunks` / `chunks_with_selective_context`：背景摘要作为 `derived_context` 附属字段，不混入 `chunks`；
- 引用编号只分配给证据 chunk，背景摘要不占用 S1、S2。

---

## 7. 融合算法

### 7.1 归一化与去重

复用 `source_dedup.normalize_channels`：

- 按 `source_key` 归一化，精确表格按来源键去重；
- 合并 `source_chunk_ids`；
- 合并 `association_paths`；
- 用 `remap_path` 把路径中的 chunk_id 映射到代表 ID。

基础通道、关系通道、事件通道、`global_support` 一次性归一化，避免同一原文被多个 ID 重复计票。

### 7.2 通道内排序

当前 `knowledge_relations` 没有边权字段，`EventEntity` 也没有权重，因此不使用“校准概率”，只使用可解释的启发式：

关系通道排序键：

```text
(min_hop, -exact_alias_hit, -direct_hit, -path_count, chunk_id)
```

事件通道排序键：

```text
(min_hop, -shared_entity_specificity, -direct_hit, -term_overlap, chunk_id)
```

其中：

- `exact_alias_hit`：query 中逐字命中的实体别名；
- `direct_hit`：该 chunk 是否已在基础候选中；
- `shared_entity_specificity`：共享实体的辨识度，高频泛称实体降权；
- `path_count`：支持同一 chunk 的独立路径数，用于排序但不重复计分。

这些是待标定的启发式，不是概率分数；上线前需在标注集上校准。

### 7.3 两级 RRF

第一级：

```text
S_struct(d) = W_rel / (k + rank_rel(d)) + W_evt / (k + rank_evt(d))
```

第二级：

```text
S_final(d) = W_base / (k + rank_base(d)) + W_struct / (k + rank_struct(d))
```

初始值：

```text
k = 60
W_base = 1.0
W_struct = 0.8
W_rel = 1.0
W_evt = 0.8
```

约束：

- 每条子通道对同一个 chunk 只计一票；
- 同一通道内多条路径只保留最佳路径参与计分；
- 缺失通道贡献为 0，不补零、不归一化；
- 分数只用于排序，不对外宣称概率含义。

### 7.4 配额

建议初始配额：

| 项目 | 默认 | 说明 |
|---|---|---|
| `candidate_k` | 20 | 向量、BM25 每路候选 |
| `relation_quota` | 30 | 关系通道候选上限 |
| `event_quota` | 20 | 事件通道候选上限 |
| `max_candidates` | 50 | 结构通道总上限 |
| 融合池 | ≤ 90 | 基础 40 + 结构 50 |
| 最终 `top_k` | 8~12 | 证据链可能占用多个 chunk |
| 结构保留位 | 20%~30% | 仅当结构候选归一化分不低于基础通道 60 分位时生效 |

配额的作用是防止“某条通道返回条数多”直接决定最终结果，而不是强行塞入低质证据。保留位需要质量门槛。

### 7.5 路径选择与预算

扩展 `select_association_evidence`：

- 按 kind 分配证据链预算：关系链约 60%，事件链约 40%；
- 优先选择“能带来新 chunk 且完整可容纳”的路径；
- 路径放不下时，不保留半截链；
- 种子块如果本来就是基础命中，可以保留，但不伪造完整路径；
- 输出标记 `evidence_budget_truncated`；
- 路径中的每个 chunk 必须仍在最终返回集合内，否则该路径标记 `context_complete=false`。

---

## 8. 背景层设计

### 8.1 数据结构

`EvidenceGroupsResponse` 增加可选字段：

```python
class BackgroundSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary_id: str
    text: str
    report_ids: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    derived: bool = True
    citation_allowed: bool = False


class EvidenceGroupsResponse(BaseModel):
    # 现有字段不变
    background: list[BackgroundSummary] = Field(default_factory=list)
```

### 8.2 渲染规则

`context_text` 模式：

```text
【背景摘要（派生内容，不可作为原文引用）】
B1. ……
B2. ……

【资料 S1】
……
```

`evidence_groups` 模式：

```json
{
  "background": [
    {
      "summary_id": "community:12:0",
      "text": "……",
      "report_ids": ["community:12"],
      "evidence_chunk_ids": ["c1", "c2"],
      "derived": true,
      "citation_allowed": false
    }
  ]
}
```

### 8.3 证据约束

- 摘要中的每条支持引文必须在本次返回的证据 chunk 中逐字可定位；
- 任一条定位失败，整条摘要丢弃，并写入 `warnings`；
- 背景摘要不占用证据组编号，不进入 `citation_map`；
- 外部 Agent 若要把背景摘要写进答案，必须明确标注“背景/归纳”，不能伪装成原文引用。

---

## 9. 路由策略

### 9.1 意图到通道

| 查询特征 | 通道 | 说明 |
|---|---|---|
| 单跳事实、定义、短 query | vector + keyword | 不进入图 |
| 专名、型号、编号 | vector + keyword（BM25 权重可提高） | 不进入图 |
| 关系、比较、因果、多跳 | + relation | 需要 `relations` 就绪 |
| 事件、参与者、时间、合作 | + event | 需要 `events` 就绪 |
| 关系 + 事件混合 | fusion（两类都开） | 需要两者都就绪 |
| 全局主题、共性、趋势 | fusion + global background | 需要 `communities` 就绪 |
| “所有/总计/多少” | hybrid + `coverage_incomplete` | 图不解决全量统计 |
| 探索、追问 | 现有 DRIFT 或 fusion + global background | 受模型/时间预算约束 |

### 9.2 自动路由

`auto_router.route_auto` 增加：

- `capabilities.relations` 与 `capabilities.events` 同时为真，且命中关系/事件意图 → `tier="fusion"`；
- 只有一类就绪 → 选对应的 `graph.local` 或 `event_graph`；
- `overview` 且社区就绪 → `tier="fusion"` + `include_global=true`；
- 其他情况保持现有规则；
- 路由结果记录 `intent`、`reason`、`capabilities`、`chosen_strategy`。

### 9.3 成本守卫

- query 长度低于阈值且不含关系/事件线索时，不进入 fusion；
- 单请求结构通道总 deadline 默认 800ms，超时停止扩展；
- Global 默认最多 2 次模型调用、2 个社区报告；
- 高 QPS 场景优先 `graph.local`（预建索引查询便宜），低 QPS 高价值场景才开 event/global。

---

## 10. 预算、并发与降级

### 10.1 预算

| 阶段 | 默认预算 |
|---|---|
| 向量 / BM25 | 各 `candidate_k=20` |
| 关系扩展 | 最多 2 跳，扇出 5，候选 30，seeds 8 |
| 事件扩展 | 最多 2 跳，扇出 5，候选 20，seeds 8 |
| 结构通道总计 | 候选 ≤ 50 |
| 融合池 | ≤ 90 |
| 结构超时 | 800ms |
| Global | 2 报告、2 模型调用、45s |
| 最终证据 | `top_k` 8~12（可配） |

所有预算在同一次请求内共享，不因多通道重复放大。

### 10.2 并发

- 首期串行执行：当前是同步 SQLAlchemy session，避免在 `asyncio.to_thread` 中共用同一 session。
- 如果实测延迟不达标，再评估并行：
  - 每个结构通道使用独立只读 session；
  - 主 session 只做基础召回和最终融合；
  - 所有 session 共用同一 KB/版本快照语义。
- 不建议为了并行而引入新的连接池复杂度；先测串行 P95。

### 10.3 降级矩阵

| 情况 | 行为 | 诊断 |
|---|---|---|
| relations 失败 | 保留 base + event | `channel_status.relation=failed` |
| events 失败 | 保留 base + relation | `channel_status.event=failed` |
| 两者都失败 | 回退 hybrid | `degraded=true` |
| global 超时/失败 | 背景为空，证据不变 | `channel_status.global=timeout` |
| 基础向量不可用 | 按现有逻辑降级关键词 | 现有 `degradation_reason` |
| 基础关键词不可用 | 按现有逻辑降级向量 | 现有 `degradation_reason` |
| `fallback_policy=error` | 请求的通道不可用即报错 | 保持严格语义 |

逐通道失败不应导致整个请求失败，除非调用方显式选择严格模式。

---

## 11. 代码改造清单

| 文件 | 改动 | 优先级 |
|---|---|---|
| `backend/app/schemas/retrieval_strategy.py` | `tier` 增加 `fusion`；新增配额/时间/Global 字段；fusion 校验 | P0 |
| `backend/app/services/retrieval/policy_resolver.py` | fusion 分支；逐通道能力检查；输出 `_association_kinds`、`_global_background` | P0 |
| `backend/app/services/retrieval/orchestrator.py` | fusion 创建共享 reader；透传多通道；诊断 | P0 |
| `backend/app/services/retrieval/associations.py` | 新增 `search_structure_channels`；seeds 复用；通道标签；两级融合；路径配额 | P0 |
| `backend/app/services/retrieval_service.py` | `_execute_chunk_search` 接多通道；融合/重排/选择顺序 | P0 |
| `backend/app/services/retrieval/source_dedup.py` | 复用多通道归一化；确认 `retrieval_sources` 合并 | P1 |
| `backend/app/services/retrieval/global_search.py` | 抽出 `retrieve_global_background`；不替换主结果 | P1 |
| `backend/app/schemas/evidence_groups.py` | 增加可选 `background` | P1 |
| `backend/app/services/context/knowledge_context_service.py` | 透传 background；保持 evidence 编号独立 | P1 |
| `backend/app/services/llm_context_builder.py` | `context_text` 增加背景段；其他模式挂 `derived_context` | P1 |
| `backend/app/services/retrieval/auto_router.py` | fusion 意图路由 | P2 |
| `backend/tests/test_retrieval_strategy.py` | fusion 校验、冲突、旧值回归 | P0 |
| `backend/tests/test_association_retrieval.py` | 多 kind、共享 seeds、预算、路径 | P0 |
| `backend/tests/test_fusion_retrieval.py`（新增） | 融合排序、配额、去重、降级、背景隔离 | P0 |
| `backend/tests/test_global_retrieval.py` | 背景摘要不进证据 | P1 |
| `backend/tests/test_retrieval_candidate_budget.py` | 总预算不超限 | P0 |
| `docs/api.md`、`docs/architecture.md` | 契约与主链路更新（实施后） | P2 |
| 前端 `RetrievalConfigView.vue` 等 | fusion 选项与通道状态（实施后） | P2 |

### 11.1 关键函数签名建议

```python
# associations.py
def search_structure_channels(
    reader: EvidenceReader,
    query: str,
    direct: list[dict],
    kinds: list[str],
    budget: dict,
    include_content: bool,
) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    ...

def rank_structure_channels(
    batches: dict[str, list[dict]],
    rrf_k: int,
    weights: dict[str, float],
) -> list[dict]:
    ...

def select_fusion_evidence(
    ordered: list[dict],
    top_k: int,
    base_ids: set[str],
    quotas: dict[str, int],
) -> list[dict]:
    ...
```

```python
# retrieval_service.py
association_kinds = payload.get("_association_kinds") or []
if association_kinds:
    batches, structure_meta = search_structure_channels(
        payload["_association_reader"], options.query, results,
        association_kinds, payload["_association_budget"], options.include_content,
    )
    normalized, aliases = normalize_channels(results, *batches.values())
    ...
```

---

## 12. 实施阶段

### P6.1 组合骨架

- schema 增加 `fusion` 与预算字段；
- policy 输出 `_association_kinds`；
- orchestrator 共享 reader；
- `_execute_chunk_search` 串行执行 relations + events；
- 不做 Global，不做两级融合调优；
- 默认关闭，按 KB 显式开启。

进入 P6.2 的条件：

- 旧策略全部回归通过；
- fusion 能同时返回 relation/event 通道；
- 预算不超限；
- 单通道失败可降级。

### P6.2 融合质量

- 两级 RRF；
- 通道配额和结构保留位；
- 路径去重与完整链选择；
- 诊断字段；
- 结构通道超时。

进入 P6.3 的条件：

- 固定语料上，融合结果可按通道解释；
- 无重复计票、无路径预算溢出；
- 离线对比显示结构通道有增量覆盖。

### P6.3 背景层

- Global background 调用；
- `background` schema；
- 上下文渲染与引用隔离；
- 支持 chunk 低权重并入。

进入 P6.4 的条件：

- 摘要从不进入证据 RRF；
- 摘要引文可逐字定位；
- Global 失败不影响证据。

### P6.4 路由、前端与评测

- auto_router 增加 fusion；
- 前端增加 fusion 选项和通道状态；
- 100 题标注集评测；
- 根据结果调权重/配额/超时。

---

## 13. 测试计划

### 13.1 单元与契约

- `TieredStrategy(tier="fusion")` 通过校验；
- `tier="fusion"` + `graph_method="local"` 被拒绝；
- `include_global=true` + `tier="hybrid"` 被拒绝；
- 旧 `semantic/hybrid/graph/auto` 和 `event_graph` 行为不变；
- 已存储 KB profile 在新增字段后仍能校验。

### 13.2 执行与预算

- `EvidenceReader` 在 fusion 中只构造一次（mock 计数）；
- relation/event 共用 seeds；
- relation 候选不超过 `relation_quota`；
- event 候选不超过 `event_quota`；
- 结构总候选不超过 `max_candidates`；
- 融合池不超过基础 + 结构上限；
- 同一 chunk 被多个通道命中时只计一票；
- 长路径放不下时不保留半截链。

### 13.3 降级与隔离

- relation 失败 → event 仍返回；
- event 失败 → relation 仍返回；
- 两者失败 → hybrid，`degraded=true`；
- Global 超时 → 证据结果不变；
- Global 摘要不出现在 `results` / `chunks` / `groups`；
- `fallback_policy=error` 时按约定报错。

### 13.4 回归

- `pytest tests/ -q` 全量通过；
- `ruff check app tests` 通过；
- MCP `search_context` 旧参数路径通过；
- `evidence_groups.v1` 旧字段不缺失、不重定义；
- 前端 `npm run check` 与 `npm run build` 通过（涉及前端时）。

---

## 14. 评测与验收

### 14.1 对比组

至少五组，固定同一语料、切片版本、Embedding、重排模型和回答模型：

1. `hybrid`（基线）；
2. `hybrid + relation`；
3. `hybrid + event`；
4. `hybrid + relation + event`（fusion）；
5. `fusion + global background`。

### 14.2 指标

- 原文证据 `Recall@K`、`nDCG@10`；
- 多跳问题完整证据链覆盖率；
- 引用支持率、原文定位成功率；
- 无依据关系率、无答案误答率；
- 每通道 unique contribution（新增且被最终答案引用的证据占比）；
- P50/P95 延迟、模型调用数、单 query 成本；
- 旧版本/越界证据进入结果的次数（要求 0）。

### 14.3 淘汰标准

- 结构通道 unique contribution 低于 5%；
- 端到端正确率提升低于 2 个百分点；
- P95 延迟超出预算且无法通过路由/超时控制；
- 路径完整性或引用定位出现系统性失败。

满足任一条，就不默认开启对应通道；若只在特定子集有收益，保留按需路由。

---

## 15. 风险与非目标

| 风险 | 影响 | 缓解 |
|---|---|---|
| `EvidenceReader` 扫描 1000 篇文档 | fusion 延迟高 | 单请求共享；必要时增加组合索引；先测串行 |
| 同一 SQLAlchemy session 并发 | 数据竞争/连接错误 | 首期串行；并行时使用独立只读 session |
| 路径重复计票 | 图通道分数虚高 | 每通道每 chunk 只计一票；只取最佳路径计分 |
| 长路径吃满 Top K | 证据多样性下降 | 按 kind 分配链预算；放不下不保留半截 |
| Global 摘要幻觉 | 被当成原文引用 | 背景层隔离、`citation_allowed=false`、逐字定位 |
| 索引覆盖不全 | 图通道只覆盖部分文档 | `coverage_incomplete`、逐通道状态、不冒充全库 |
| SAG 表述过度 | 对外承诺无法验证 | 明确“参考实现”，不宣称官方算法复现 |
| 默认全开导致成本上升 | QPS 下降 | fusion 非默认；自动路由加成本守卫 |

非目标重申：

- 不新建图数据库；
- 不重新切块；
- 不改变旧策略默认行为；
- 不把摘要混入证据；
- 不让后端承担答案生成。

---

## 16. 附录：最小请求与响应片段

### 16.1 只开关系通道

```json
{
  "query_text": "甲校和乙实验室是什么合作关系？",
  "strategy": {
    "family": "tiered",
    "tier": "fusion",
    "budget": {"relation_quota": 30, "event_quota": 0}
  }
}
```

### 16.2 关系 + 事件 + 背景

```json
{
  "query_text": "甲校新增专业有哪些实践合作资源？",
  "strategy": {
    "family": "tiered",
    "tier": "fusion",
    "include_global": true,
    "budget": {
      "relation_quota": 30,
      "event_quota": 20,
      "max_global_reports": 2,
      "max_global_model_calls": 2
    }
  }
}
```

### 16.3 响应片段

```json
{
  "success": true,
  "return_mode": "evidence_groups",
  "groups": [],
  "chunks": [],
  "background": [
    {
      "summary_id": "community:12:0",
      "text": "该知识库中多份材料提到……",
      "report_ids": ["community:12"],
      "evidence_chunk_ids": ["c1", "c2"],
      "derived": true,
      "citation_allowed": false
    }
  ],
  "meta": {
    "retrieval": {
      "executed_strategy": {"family": "tiered", "tier": "fusion"},
      "fusion": {
        "executed": ["vector", "keyword", "relation", "event", "global_background"],
        "channel_status": {
          "relation": "ready",
          "event": "ready",
          "global": "ready"
        },
        "coverage_incomplete": false
      }
    }
  }
}
```