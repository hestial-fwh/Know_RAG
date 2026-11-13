# 图谱增强检索修改方案：从现有四档到分层融合

> 状态：修改方案（Planned，未实施）
> 日期：2026-11-11
> 设计依据：分层融合方案（四层融合、两级 RRF / 图特征、事件独立政策、Global 背景）与当前代码现状
> 适用范围：`know_rag` 在线检索链路、检索契约、上下文出口、前端策略配置与测试
> 修改原则：只增不改默认、显式开启、分阶段放行、每阶段可独立回滚

---

## 0. 结论先行

当前代码已经具备全部零件：基础向量/BM25、Local 关系扩展、SAG 事件关联、Global 社区摘要、来源归一化、RRF、重排、证据链选择、上下文组装。缺的不是能力，而是**组合入口和各自的计分面**。

本方案不做一次性重写，而是按六个里程碑增量改造：

1. **M0 基线与护栏**：冻结现有行为，建立对照基线。
2. **M1 契约与策略**：新增 `tier="fusion"` 和 `FusionSettings`，先只接关系通道。
3. **M2 结构通道执行器**：关系/事件共用 reader 和 seeds；事件级排序；事件独立政策与保留位。
4. **M3 排序与选择**：两级 RRF、图特征重排、配额和完整链选择。
5. **M4 背景与事件线**：Global 只做背景；事件线作为派生上下文。
6. **M5 路由、前端与评测**：自动路由、负向路由、配置界面和分通道验收。

### 0.1 召回层融合形式的选择

| 形式 | 是否采用 | 原因 |
|---|---|---|
| 平铺 RRF | 不采用 | 票源失衡；事件通道会被关系和基础同时压住，SAG 优势消失 |
| 两级 RRF | 默认采用 | 结构先内部融合，再与基础比较；可解释、可配额 |
| 图特征重排 | 可选采用 | 不新增候选，保基础精度；需要可接收特征的重排器或外层加权 |
| 级联/路由 | 外层采用 | 单跳、专名、枚举、覆盖不足时不进图，控制成本和负收益 |

### 0.2 工作量与风险

预计 10~16 人日，集中在 5 个后端文件、3 个 schema/context 文件、3 个前端文件和测试。

最高风险不是算法，而是三件事：

- 事件通道被主排名淹没；
- Global 摘要污染证据池；
- 旧策略回归。

对应三道护栏：`event_policy`、`background` 物理隔离、默认 `hybrid` 且 fusion 只显式开启。

---

## 1. 现状快照

### 1.1 当前调用链

```text
API / MCP
  → retrieval_service.hybrid_search
  → orchestrator.execute_search
      → policy_resolver.resolve_policy（单 tier 选择）
      → _execute_chunk_search
          → vector_search + keyword_search
          → normalize_channels
          → merge_results（RRF，k=60）
          → [最多一个 association_kind] search_associations
          → normalize_channels + remap_path
          → rank_associations（base 与 association 等权 1:1）
          → rerank_results（完整融合池）
          → select_association_evidence（完整证据链占 top_k）
          → 图片绑定 + 父块上下文
```

Global / DRIFT 在 `orchestrator` 中短路执行，不经过 `_execute_chunk_search`：

- `graph_method=global` 直接 `execute_global`，不跑基础召回；
- `graph_method=drift` 先用社区要点规划子问题，再跑 Local。

### 1.2 现状与目标差距

| 模块 | 现状 | 目标 | 改动类型 |
|---|---|---|---|
| 策略契约 | `tier` 只有 `semantic/hybrid/graph/auto`；`event_graph` 是独立 family | 增加 `tier="fusion"` 和 `FusionSettings` | 扩展 |
| 策略解析 | `policy_resolver` 只输出一个 `_association_kind` | 输出 `_association_kinds`、`_fusion_settings`、`_global_background` | 修改 |
| 执行器准备 | 只有 graph/auto/event_graph 创建 `EvidenceReader` | fusion 也创建，且只建一次 | 修改 |
| 关系通道 | `search_associations(kind="relations")` | 拆成独立执行器，保留旧包装 | 重构 |
| 事件通道 | `search_associations(kind="events")`，chunk 级启发式排序 | 事件级排序 + 独立政策 + 保留位 | 修改 |
| 排序 | `rank_associations` 只融合 base + 单通道，等权 | 两级 RRF + 图特征模式 | 新增 |
| 选择 | `select_association_evidence` 只按完整链装 top_k | 增加配额、事件保留位、关系保留位 | 扩展 |
| Global | 独立执行路径，替代主结果 | 背景层 + 低权重支持 chunk | 拆分 |
| 上下文 | `evidence_groups.v1` 无 background/event_lines | 增加背景和事件线，引用隔离 | 扩展 |
| 路由 | 四档规则路由 | fusion 意图 + 负向路由 + 覆盖门槛 | 修改 |
| 诊断 | `meta.retrieval.association/global` | 增加 `meta.retrieval.fusion` | 新增 |
| 前端 | 策略类型与 KB 表单没有 fusion | 增加 fusion 选项和设置 | 修改 |
| 离线 | P2–P4 已产出 entities/relations/events/communities | 不改算法，只读 capabilities/coverage | 无改动 |

### 1.3 可复用资产

改造时优先复用，避免另起一套：

- `associations.EvidenceReader`：当前 manifest、来源、覆盖率、证据校验。
- `associations._seeds` / `_local` / `_events`：关系与事件扩展主体。
- `source_dedup.normalize_channels` / `remap_path`：多通道去重与路径映射。
- `associations.rank_associations` / `select_association_evidence`：旧单通道路径继续使用。
- `global_search.retrieve_global` / `current_reports`：Global map-reduce 与 revision 门禁。
- `global_search.present_graph_results`：Global/DRIFT 的最终展示出口。
- `retrieval_service.rerank_results`：现有重排。
- `retrieval_service` 的 `candidate_budget` 诊断结构。

### 1.4 不能动的边界

- `legacy_v1` 请求：行为完全不变。
- `family="event_graph"`：语义不变，仍保持 base + event 等权。
- `tier="graph"`：`local/global/drift` 行为不变。
- 切块、Embedding、BM25、离线抽取算法：不改。
- 数据库：不新增表、不新增列，不做迁移。
- 证据契约：chunk_id 仍是唯一通货；摘要和事件线不可引用。
- 外部调用：仍只允许本机 Ollama；不新增云调用。

---

## 2. 目标形态

### 2.1 请求流程

```text
query
  → 意图路由（是否进图、用哪种政策）
  → policy_resolver（通道、预算、rank_mode、event_policy）
  → 共享 EvidenceReader
  → 基础召回（vector + BM25）
  → 关系通道（Local）
  → 事件通道（SAG，事件级排序）
  → 可选背景（Global）
  → 归一化去重
  → 排序（rrf / feature）
  → 重排
  → 选择（完整链 + 配额 + 保留位）
  → 上下文（证据 / 背景 / 事件线分离）
```

### 2.2 通道角色

| 通道 | 角色 | 是否进主排名 | 是否可引用 |
|---|---|---|---|
| vector / keyword | 基础召回，图的入口 | 是 | 是 |
| relation | 关系邻域扩展 | 是（rrf）或仅特征/保留位（feature） | 是（chunk） |
| event | 事件—实体动态关联 | `independent` 时不进主排名，走保留位；`fused` 时进结构 | 是（chunk） |
| global | 主题综合 | 否 | 摘要否，支持 chunk 是 |
| event_lines | 事件组织 | 否 | 否 |

### 2.3 不融合清单

以下查询明确不走图，避免"为了用图而用图"：

- 单跳事实、定义、短 query；
- 专名、型号、编号；
- 枚举、总计、精确统计；
- 查询中没有可链接实体；
- 关系/事件索引覆盖低于阈值；
- 结构通道 deadline 不足。

### 2.4 四个融合层

| 层 | 融合什么 | 不融合什么 | 本文落点 |
|---|---|---|---|
| 索引层 | 文本命中 → 实体种子 → 图扩展 | 不把图当独立召回器 | M2 |
| 召回层 | 候选合并、图特征、配额 | 不把摘要当候选 | M3 |
| 上下文层 | 关系路径、事件线、背景摘要 | 摘要不占引用编号 | M4 |
| 生成层 | 背景摘要供 Agent 参考 | 后端不生成最终答案 | M4 |

---

## 3. 修改总览

| # | 模块 | 文件 | 改动 | 阶段 | 优先级 |
|---|---|---|---|---|---|
| 1 | 策略契约 | `backend/app/schemas/retrieval_strategy.py` | `FusionSettings`、`tier="fusion"`、预算字段、校验 | M1 | P0 |
| 2 | 策略解析 | `backend/app/services/retrieval/policy_resolver.py` | fusion 分支、多通道、背景开关 | M1 | P0 |
| 3 | 统一入口 | `backend/app/services/retrieval/orchestrator.py` | 共享 reader、capabilities、背景路径 | M1/M4 | P0 |
| 4 | 结构通道 | `backend/app/services/retrieval/associations.py` | 拆分关系/事件执行器；事件级排序；保留位候选 | M2 | P0 |
| 5 | 主检索 | `backend/app/services/retrieval_service.py` | 多通道执行顺序；rrf/feature；选择 | M2/M3 | P0 |
| 6 | 去重 | `backend/app/services/retrieval/source_dedup.py` | 确认 `retrieval_sources` 合并；路径 remap | M3 | P1 |
| 7 | Global | `backend/app/services/retrieval/global_search.py` | 抽出背景模式，不替代主结果 | M4 | P1 |
| 8 | 响应 schema | `backend/app/schemas/evidence_groups.py` | `background`、`event_lines` | M4 | P1 |
| 9 | 上下文 | `backend/app/services/context/knowledge_context_service.py` | 透传与引文过滤 | M4 | P1 |
| 10 | 上下文文本 | `backend/app/services/llm_context_builder.py` | 背景段/事件线段渲染 | M4 | P1 |
| 11 | 路由 | `backend/app/services/retrieval/auto_router.py` | fusion 意图 + 负向路由 | M5 | P2 |
| 12 | 前端类型 | `frontend/src/api/retrieval.ts` | 策略类型、诊断字段 | M5 | P2 |
| 13 | 前端映射 | `frontend/src/utils/retrievalStrategy.ts` | `fusion` 选择映射 | M5 | P2 |
| 14 | 前端表单 | `frontend/src/views/KnowledgeBaseFormView.vue` | fusion 选项与设置 | M5 | P2 |
| 15 | 后端测试 | `backend/tests/*` | 契约、执行、SAG、背景、预算 | 全程 | P0 |
| 16 | 文档 | `docs/api.md`、`docs/architecture.md` | 实施后更新契约与主链路 | M5 | P2 |

---

## 4. 分阶段修改

### M0 基线与护栏

**目标**：在改代码前冻结当前行为，避免"改完发现旧策略变了"。

**任务**

1. 运行现有测试，记录通过基线：
   - `pytest tests/ -q`
   - `ruff check app tests`
2. 选 20 条固定 query（单跳、专名、关系、事件、全局、枚举各若干），记录：
   - `meta.retrieval.channels`
   - `candidate_budget`
   - `association` / `global` 诊断
   - P50/P95 延迟
3. 在 `test_retrieval_strategy.py` 补充旧策略快照断言：
   - `tier=hybrid` 不产生 `_association_kinds`；
   - `family=event_graph` 仍只走 `events`；
   - `tier=graph/local` 仍只走 `relations`；
   - `tier=auto` 的路由结果结构不变。
4. 明确 `fusion` 只允许显式传入，M5 之前不接入 `auto_router`。

**验收**

- 现有测试全绿；
- 基线数据可复现；
- 旧策略诊断字段无变化。

**回滚**：本阶段只加测试，无运行时代码改动。

---

### M1 契约与策略

**目标**：让 `tier="fusion"` 合法、可解析、可执行"基础 + 关系"的最小闭环。

**文件与改动**

1. `schemas/retrieval_strategy.py`
   - 新增 `FusionSettings`（见 5.1）；
   - `TieredStrategy.tier` 增加 `"fusion"`；
   - `AssociationBudget` 增加 `relation_quota`、`event_quota`、`max_structure_candidates`、`structure_timeout_ms`、`global_timeout_seconds`、`max_global_model_calls`、`max_global_reports`；
   - 其中 `global_*` 是背景专用预算，不复用 DRIFT/Global 主路径的 `max_reports` / `max_model_calls` / `timeout_seconds`，避免背景拖长主链路；
   - 增加 fusion 校验：不允许 `graph_method`；基础通道必须开；配额与事件政策一致。
   - 注意：`AssociationBudget` 是 `extra="forbid"`，新增字段必须有默认值，旧 profile 才能继续校验。

2. `services/retrieval/policy_resolver.py`
   - 在 `resolve_policy` 增加 fusion 分支；
   - 输出：
     - `_association_kinds`：`["relations"]`（M1 只接关系；M2 加 `events`）；
     - `_fusion_settings`：`strategy.fusion.model_dump()`；
     - `_global_background`：仅当 `include_global` 且 `capabilities.global` 为真；
   - 两类图都未就绪时按 `fallback_policy` 处理：`hybrid` 降级并记录原因，`error` 直接报错；
   - 不设置 `_global_search`（旧 Global 路径保持独立）。

3. `services/retrieval/orchestrator.py`
   - 判断条件增加 `tier == "fusion"`；
   - 为 fusion 创建一次 `EvidenceReader`；
   - capabilities 至少计算 `relations/events/global`；
   - 将 reader 挂到 `plan.payload["_association_reader"]`。

4. `services/retrieval_service.py`
   - `_execute_chunk_search` 读取 `_association_kinds`；
   - M1 只执行 `relations`，`events` 记录为 `not_implemented`（M2 补齐）；
   - 保持旧 `_association_kind` 分支不动，event_graph 行为不变。

**测试**

- `test_retrieval_strategy.py`
  - `TieredStrategy(tier="fusion")` 通过；
  - `tier="fusion"` + `graph_method` 被拒绝；
  - `tier="fusion"` + `use_vector=False` 被拒绝；
  - 旧 profile 校验通过。
- `test_association_retrieval.py`
  - fusion + relations 返回路径；
  - reader 只构造一次（mock 计数）。

**验收**

- fusion 请求能返回基础 + 关系结果；
- 旧策略全部回归；
- 两类图都未就绪时按配置降级或报错；
- 无数据库迁移。

**回滚**：移除 fusion 分支即可；没有数据变更。

---

### M2 结构通道执行器与事件政策

**目标**：关系/事件共用 reader 和 seeds；事件按事件相关性排序；`event_policy` 生效；事件独有证据有保留位。

**文件与改动**

1. `services/retrieval/associations.py`
   - 新增 `search_relation_channel`、`search_event_channel`；
   - `_seeds` 只算一次，两个通道共用；
   - 保留 `search_associations` 作为旧单通道包装（event_graph 走它，行为不变）；
   - 新增 `rank_events`：
     - 计算 `event_relevance`；
     - 排序键 `(-event_relevance, -specificity_norm, min_hop, event_id)`；
     - `direct_hit` 只做平票裁决；
   - 新增事件候选的 `event_ids`、`event_relevance`、`association_paths` 字段；
   - 同一 chunk 多事件支持时取最大相关性，不重复计票。

2. `services/retrieval_service.py`
   - 读取 `_fusion_settings.event_policy`：
     - `independent`：事件候选单独保留，不并入 `rank_associations`；
     - `fused`：事件候选进入结构批次（M3 做两级 RRF；M2 先用简单合并验证）；
     - `off`：不执行事件通道；
   - 在 `rank_associations` 之外新增 fusion 分支，旧函数不动；
   - M2 先实现保留位选择的最小版本：事件独有候选按相关性装 2 个席位。

3. 诊断
   - 在 `meta.retrieval` 增加 `fusion.event`：
     - `policy`、`seeds`、`events_scored`、`direct_overlap_count`、`reserved_slots_used`、`top_event_relevance`；
   - 保留旧 `association` 投影。

**测试**

- 新增 `test_event_policy.py`
  - `independent/fused/off` 三种政策；
  - `direct_hit=false` 不再导致事件通道内部降序；
  - 事件保留位只给事件独有候选；
  - 同一 chunk 多事件只计一票；
  - 事件相关性高的独有证据能进入 top_k。
- `test_association_retrieval.py`
  - seeds 只算一次；
  - relation/event 预算不互相穿透；
  - 长路径放不下时不保留半截。

**验收**

- 事件独有证据在 `independent` 政策下可进入结果；
- 事件相关性排序与人工判断方向一致；
- reader 只构造一次；
- event_graph 旧路径不变。

**回滚**：`event_policy="off"` 或从 `_association_kinds` 移除 `events`。

---

### M3 排序、配额与选择

**目标**：关系/事件作为整体与基础比较；提供 `rrf` 和 `feature` 两种模式；配额与完整链选择。

**文件与改动**

1. `services/retrieval/associations.py`
   - 新增 `rank_structure_channels`：结构内部先融合；
   - 新增 `rank_fusion_channels`：顶层排序，负责 base 与 structure（或 event 保留位）的合并；
   - 新增 `apply_graph_features`：图作为重排特征；
   - 新增 `select_fusion_evidence`：完整链 + 配额 + 保留位；
   - 保留 `rank_associations` / `select_association_evidence` 供旧路径使用。

2. `services/retrieval_service.py`
   - fusion 执行顺序固定为：

     ```text
     base = merge_results(vector, keyword)
     structure = rank_structure_channels(relation, event)   # event_policy=fused
     evidence = rank_channels(base, structure)               # rrf
     或 evidence = apply_graph_features(base, relation, event) # feature
     evidence = rerank_results(evidence, full_pool)
     evidence = select_fusion_evidence(evidence, top_k, base_ids, quotas, reserved)
     ```

   - 重排必须在完整融合池上做，不能先截断；
   - `event_policy=independent` 时，事件不进主排名，只进保留位；
   - `rank_mode=feature` 时，图独有候选只走保留位；
   - 结构通道总 deadline 800ms，超时停止扩展。

3. `source_dedup.py`
   - 确认 `retrieval_sources` 合并、`source_chunk_ids` 合并、路径 remap 对四个通道都生效；
   - 不改现有表格去重逻辑。

**测试**

- 新增 `test_fusion_retrieval.py`
  - 两级 RRF 排序；
  - 配额不超限；
  - 同一 chunk 多通道只计一票；
  - 事件独立政策不进主排名；
  - 长路径不半截；
  - 单通道失败降级。
- 新增 `test_fusion_feature_rerank.py`
  - 图特征只影响已入池候选；
  - 图独有候选只走保留位；
  - 重排不可用时回退 rrf。
- `test_retrieval_candidate_budget.py`
  - 融合池 ≤ 基础 + 结构上限。

**验收**

- 结果可按通道解释；
- 无重复计票、无路径预算溢出；
- 事件通道有增量覆盖；
- P95 不超预算。

**回滚**：`rank_mode="rrf"`；`event_policy="independent"`；必要时把 `event_reserved_slots` 设为 0。

---

### M4 背景层与事件线

**目标**：Global 只做背景；支持 chunk 低权重并入；事件线作为派生上下文；引用完全隔离。

**文件与改动**

1. `services/retrieval/global_search.py`
   - 从 `retrieve_global` 中抽出 `retrieve_global_background`：
     - 复用 `current_reports`、map-reduce、引文校验；
     - 返回 `summaries`、`support_items`、`meta`；
     - 不调用 `present_graph_results`；
   - 保留 `retrieve_global` 作为旧 Global 路径包装，行为不变；
   - 背景模式失败/超时只返回 `status=failed|timeout`，不抛到主链路。

2. `services/retrieval/orchestrator.py`
   - `_global_background` 为真时，在基础/结构之后调用背景；
   - 不短路主检索；
   - 支持 chunk 只在未被基础/关系/事件覆盖时并入。

3. `schemas/evidence_groups.py`
   - 新增 `BackgroundSummary`、`EventLineItem`；
   - `EvidenceGroupsResponse` 增加 `background`、`event_lines`，默认空列表；
   - `extra="ignore"`，旧消费者不受影响。

4. `services/context/knowledge_context_service.py`
   - 透传 background/event_lines；
   - 逐条校验引文是否在返回 chunk 中逐字存在；
   - 定位失败则丢弃并写 `warnings`；
   - 背景/事件线不进入 `citation_map`，不占 S 编号。

5. `services/llm_context_builder.py`
   - `build_llm_context_payload` 在 `sections` 前插入背景段和事件线段；
   - 资料段编号逻辑不变；
   - `build_evidence_groups_payload` 把 background/event_lines 放到顶层。

**测试**

- `test_global_retrieval.py`
  - 背景摘要不出现在 `results/chunks/groups`；
  - 背景失败不影响证据；
  - 引文定位失败则整条丢弃。
- 新增上下文测试
  - `context_text` 中背景/事件线在资料段之前；
  - 背景/事件线不占 S 编号；
  - 事件线只包含支持 chunk 已返回的事件。

**验收**

- 摘要和事件线从不进入证据 RRF；
- 引文可逐字定位；
- Global 失败不影响证据；
- `include_global=false` 时行为与 M3 完全一致。

**回滚**：关闭 `include_global` / `include_event_timeline`；旧 Global 路径保留。

---

### M5 路由、前端与评测

**目标**：让 fusion 按意图自动启用，但保留显式关闭；前端可配置；评测能证明收益。

**文件与改动**

1. `services/retrieval/auto_router.py`
   - 关系/事件双意图且两者就绪 → `tier="fusion"`，`event_policy="independent"`；
   - 只有一类就绪 → 现有 `graph/local` 或 `event_graph`；
   - `overview` + 社区就绪 → `fusion` + `include_global=true`；
   - 枚举/统计 → `hybrid` + `coverage_incomplete`；
   - 覆盖不足、query 过短、无实体 → 保持 `hybrid`；
   - 路由结果继续记录 `intent/reason/capabilities/chosen_strategy`。

2. `frontend/src/api/retrieval.ts`
   - `RetrievalStrategy` 增加 `tier: "fusion"` 与 `fusion` 字段；
   - `RetrievalDiagnostics` 增加 `fusion` 诊断类型。

3. `frontend/src/utils/retrievalStrategy.ts`
   - `strategyFromSelection` 支持 `fusion`：
     - 默认 `rank_mode="rrf"`、`event_policy="independent"`；
     - 可选 `feature` 和 `include_global`。

4. `frontend/src/views/KnowledgeBaseFormView.vue`
   - 策略下拉增加“分级融合：关系 + 事件”；
   - 增加 `event_policy`、`rank_mode`、`include_global`、`include_event_timeline` 开关；
   - 只在选择 fusion 时显示这些设置。

5. 文档
   - 实施后更新 `docs/api.md` 的 tier 列表与请求示例；
   - 更新 `docs/architecture.md` 的检索主链路与诊断字段。

6. 评测
   - 复用 `test_retrieval_evaluation.py` 的固定题集框架；
   - 增加对比组：`hybrid`、`event_graph`、`graph.local`、`fusion-rrf independent`、`fusion-rrf fused`、`fusion-feature`、`fusion+global`；
   - 按查询类型分子集统计，不用总体平均掩盖单跳负向回归。

**测试与验收**

- 路由单测：各意图命中预期策略；
- 前端 `npm run check`、`npm run build`；
- 五组以上对比；
- 单跳子集负向回归 ≤1 个百分点；
- 关系/事件通道 unique contribution 达标；
- 事件时序/条件正确率有提升，或该通道不默认开启。

**回滚**：关闭 auto 路由中的 fusion；保留显式 `tier="fusion"`；前端可隐藏入口。

---

## 5. 关键实现细节

### 5.1 Schema 草案

```python
class FusionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rank_mode: Literal["rrf", "feature"] = "rrf"
    event_policy: Literal["independent", "fused", "off"] = "independent"
    include_global: bool = False
    include_event_timeline: bool = False
    event_reserved_slots: int = Field(default=2, ge=0, le=3)
    relation_reserved_slots: int = Field(default=2, ge=0, le=3)
    event_relevance_threshold: float = Field(default=0.5, ge=0, le=1)
    graph_feature_weight: float = Field(default=0.15, ge=0, le=1)
    event_feature_weight: float = Field(default=0.25, ge=0, le=1)


class TieredStrategy(BaseModel):
    family: Literal["tiered"] = "tiered"
    tier: Literal["semantic", "hybrid", "graph", "fusion", "auto"] = "hybrid"
    graph_method: Literal["local", "global", "drift"] | None = None
    fusion: FusionSettings = Field(default_factory=FusionSettings)
    fallback_policy: Literal["hybrid", "error"] = "hybrid"
    budget: AssociationBudget = Field(default_factory=AssociationBudget)
```

校验要点：

- `tier != "graph"` 且 `tier != "fusion"` 时不允许 `graph_method`；
- `tier == "fusion"` 时 `graph_method` 必须为空；
- `tier == "fusion"` 时基础通道强制开启；
- `event_policy != "off"` 时 `event_quota > 0`；
- `include_global` / `include_event_timeline` 只对 fusion/auto 生效；
- 旧字段和行为不变。

### 5.2 策略解析伪代码

```python
if isinstance(strategy, TieredStrategy) and strategy.tier == "fusion":
    kinds = [k for k in ("relations", "events") if (capabilities or {}).get(k)]
    if not kinds:
        if strategy.fallback_policy == "error":
            raise ValueError("fusion 需要 relations 或 events 索引")
        return RetrievalPlan(..., degraded=True, reason="关系/事件索引均未就绪，已回退混合检索")
    normalized["_association_kinds"] = kinds
    normalized["_association_budget"] = strategy.budget.model_dump()
    normalized["_fusion_settings"] = strategy.fusion.model_dump()
    if strategy.fusion.include_global and (capabilities or {}).get("global"):
        normalized["_global_background"] = True
    return RetrievalPlan(normalized, requested, executed)
```

### 5.3 执行顺序伪代码

```python
association_kinds = payload.get("_association_kinds") or []
fusion = payload.get("_fusion_settings") or {}

# 1. 基础召回
results = merge_results(vector_results, keyword_results, pool_options)

# 2. 结构通道
relation_candidates, relation_meta = search_relation_channel(...) if "relations" in association_kinds else ([], {})
event_candidates, event_meta = search_event_channel(...) if "events" in association_kinds and fusion["event_policy"] != "off" else ([], {})

# 3. 归一化
(results, relation_candidates, event_candidates), aliases = normalize_channels(
    results, relation_candidates, event_candidates
)

# 4. 排序
if fusion["rank_mode"] == "feature":
    results = apply_graph_features(results, relation_candidates, event_candidates, fusion)
else:
    # rank_fusion_channels 内部调用 rank_structure_channels，
    # 再按 event_policy 决定事件是进入结构还是只走保留位。
    results = rank_fusion_channels(
        base=results,
        relation=relation_candidates,
        event=event_candidates,
        event_policy=fusion["event_policy"],
        weights=...,
    )

# 5. 重排 + 选择
results = rerank_results(..., results, len(results))
results = select_fusion_evidence(
    results, final_top_k, base_ids, quotas, reserved
)
```

### 5.4 事件级计分

```text
event_relevance =
    0.40 * participant_match
  + 0.25 * term_overlap_norm
  + 0.20 * specificity_norm
  + 0.15 * time_condition_match
```

- `direct_hit` 不进入公式，只在完全同分时裁决；
- 同一 chunk 多事件取最大相关性；
- 事件保留位要求 `event_relevance >= threshold`；
- 权重待标定，不宣称概率含义。

### 5.5 选择算法

```text
select_fusion_evidence(ordered, top_k, base_ids, quotas, reserved):
  1. 先按完整证据链装填，保证多跳链不被截断；
  2. 再装事件保留位（仅事件独有、相关性过门槛）；
  3. feature 模式下装关系保留位；
  4. 用通用池填满剩余席位；
  5. 未用完的保留位归还通用池；
  6. 标记 evidence_budget_truncated / context_complete。
```

### 5.6 上下文渲染

```text
【背景摘要（派生内容，不可作为原文引用）】
B1. ……
B2. ……

【事件线（派生内容，不可作为原文引用）】
E1. 2025-03 | 甲校、乙实验室 | 签订实践基地协议（来源：S1）
E2. ……

【资料 S1】
……
```

- S 编号只给证据 chunk；
- B/E 不进入 `citation_map`；
- 任一条引文定位失败，整条丢弃并写 `warnings`。

### 5.7 诊断字段

```json
{
  "fusion": {
    "mode": {"rank_mode": "rrf", "event_policy": "independent"},
    "executed": ["vector", "keyword", "relation", "event"],
    "channel_status": {"relation": "ready", "event": "ready", "global": "skipped_not_requested"},
    "candidate_counts": {"vector": 20, "keyword": 20, "relation": 31, "event": 18, "event_only": 5},
    "event": {"reserved_slots_used": 2, "top_event_relevance": 0.83},
    "structure_paths": {"relation": 7, "event": 4},
    "weights": {"base": 1.0, "structure": 0.8, "relation": 1.0, "event": 1.0},
    "quota": {"relation": 30, "event": 20, "event_reserved_slots": 2},
    "elapsed_ms": {"base": 180, "relation": 150, "event": 70, "global": 0},
    "coverage_incomplete": false
  }
}
```

---

## 6. 测试矩阵

| 测试文件 | 类型 | 关键断言 | 阶段 |
|---|---|---|---|
| `test_retrieval_strategy.py` | 契约 | fusion 校验、冲突、旧值回归、旧 profile 校验 | M1 |
| `test_association_retrieval.py` | 执行 | seeds 只算一次、预算、路径、单通道包装不变 | M2 |
| `test_event_policy.py`（新增） | SAG | independent/fused/off、direct_hit 只做裁决、保留位 | M2 |
| `test_fusion_retrieval.py`（新增） | 融合 | 两级 RRF、配额、去重、降级、路径完整 | M3 |
| `test_fusion_feature_rerank.py`（新增） | 特征 | 图特征、保留位、rrf 回退 | M3 |
| `test_retrieval_candidate_budget.py` | 预算 | 融合池不超限、事件/关系配额 | M3 |
| `test_global_retrieval.py` | 背景 | 摘要不进证据、失败不影响证据、引文校验 | M4 |
| `test_retrieval_context_normalizer.py` | 上下文 | 背景/事件线不占 S 编号、裁剪后丢弃 | M4 |
| `test_retrieval_evaluation.py` | 评测 | 固定题集、分通道指标、单跳负向回归 | M5 |
| `frontend/src/utils/retrievalStrategy.test.ts` | 前端 | fusion 映射、旧值不变 | M5 |
| `npm run check` / `npm run build` | 前端 | 类型、Lint、构建 | M5 |

---

## 7. 验收与放行

### 7.1 每阶段放行条件

| 阶段 | 放行条件 |
|---|---|
| M1 | 旧策略全回归；fusion 关系通道可用；无迁移；未就绪可降级 |
| M2 | 事件独有证据可通过保留位进入；events/relations 共用 reader；无重复计票 |
| M3 | 两级 RRF / feature 可选；配额与完整链正确；P95 达标 |
| M4 | 摘要/事件线不进证据；引文可定位；Global 失败不影响证据 |
| M5 | 路由命中预期；前端可用；五组对比达标；单跳无负向回归 |

### 7.2 总体验收指标

- `Recall@K` 有提升，尤其是多跳和事件类子集；
- `nDCG@10` 不下降；
- 完整证据链覆盖率提升；
- 每通道 unique contribution 可解释；
- 事件专属贡献或时序/条件正确率有提升；
- P50/P95 在预算内；
- 旧版本/越界证据进入次数为 0；
- 单跳子集负向回归不超过 1 个百分点。

### 7.3 淘汰规则

- 关系通道 unique contribution < 5% → 不默认开启；
- 事件通道专属贡献 < 5% 且时序/条件正确率无提升 → 不默认开启；
- 端到端正确率提升 < 2 个百分点 → 保留显式入口，不做默认；
- P95 超预算且路由/超时无法控制 → 降级或关闭对应通道。

---

## 8. 风险与回滚

| 风险 | 触发信号 | 缓解 | 回滚 |
|---|---|---|---|
| 事件通道被主排名淹没 | 事件独有证据进不了结果 | `event_policy=independent` + 保留位 + 事件级计分 | 提高保留位或阈值 |
| 事件噪音大 | 单跳/专名查询被事件带偏 | 路由不进图；提高 `event_relevance_threshold` | `event_policy=off` |
| Global 摘要污染 | 摘要出现在 results/groups | 背景层物理隔离、`citation_allowed=false` | `include_global=false` |
| Global 幻觉 | 引文定位失败 | 逐字校验、失败整条丢弃 | 关闭背景 |
| 图特征未标定 | nDCG 下降 | 默认小权重；先 A/B | 回退 `rank_mode=rrf` |
| 旧策略回归 | `test_retrieval_strategy` 失败 | 旧函数保留，不重写 | 移除 fusion 分支 |
| 覆盖不足导致偏差 | 图通道只覆盖部分文档 | `coverage_incomplete`、负向路由 | 路由回 hybrid |
| 延迟超预算 | P95 超标 | 共享 reader、串行、800ms deadline | 关闭事件/Global |
| SQLAlchemy session 并发 | 连接/数据竞争 | 首期串行；并行时独立只读 session | 回退串行 |
| 前端类型漂移 | `npm run check` 失败 | 类型与后端 schema 同步 | 隐藏 fusion 入口 |

数据库无变更，代码回滚不涉及数据回滚。

---

## 9. 工作量与依赖

| 阶段 | 主要工作 | 预估 | 依赖 | 风险 |
|---|---|---|---|---|
| M0 | 基线、快照测试 | 0.5 天 | 无 | 低 |
| M1 | schema、policy、orchestrator、关系通道 | 1~2 天 | M0 | 低 |
| M2 | 结构执行器、事件级排序、保留位 | 2~3 天 | M1 | 中高 |
| M3 | 两级 RRF、feature、配额、选择 | 2~3 天 | M2 | 中 |
| M4 | Global 背景、事件线、上下文 | 2~3 天 | M3 | 中 |
| M5 | 路由、前端、评测 | 3~5 天 | M4 | 中 |

建议顺序：M0 → M1 → M2 → M3 → M4 → M5。M2 与 M4 可在 M1 后部分并行，但 M3 依赖 M2 的通道输出结构。

---

## 10. PR 检查清单

- [ ] 旧策略 `semantic/hybrid/graph/auto` 行为未变；
- [ ] `family="event_graph"` 语义未变；
- [ ] `legacy_v1` 请求未受影响；
- [ ] `tier="fusion"` 只显式开启，未进入默认或自动路由（M5 前）；
- [ ] 事件通道保留独立计分面，`direct_hit` 只做平票裁决；
- [ ] 事件保留位只给事件独有候选，且过相关性门槛；
- [ ] Global 摘要不进入 `results/chunks/groups`；
- [ ] 背景/事件线不占 S 编号，不进入 `citation_map`；
- [ ] `chunk_id` 仍是唯一通货，路径已 remap；
- [ ] 每通道每 chunk 只计一票；
- [ ] 结构超时和 Global 预算生效；
- [ ] 覆盖不足有 `coverage_incomplete`；
- [ ] 无数据库迁移；
- [ ] 无新增云调用或外部依赖；
- [ ] `pytest tests/ -q` 通过；
- [ ] `ruff check app tests` 通过；
- [ ] 前端 `npm run check`、`npm run build` 通过（涉及前端时）；
- [ ] `docs/api.md`、`docs/architecture.md` 已更新（实施后）；
- [ ] 五组对比与单跳负向回归已记录。

---

## 11. 附录

### 11.1 KB profile 配置示例

```json
{
  "version": "new_profile_v2",
  "build_indexes": ["entities", "relations", "events", "communities"],
  "strategy": {
    "family": "tiered",
    "tier": "fusion",
    "fusion": {
      "rank_mode": "rrf",
      "event_policy": "independent",
      "include_global": true,
      "include_event_timeline": true,
      "event_reserved_slots": 2
    },
    "budget": {
      "candidate_k": 20,
      "relation_quota": 30,
      "event_quota": 20,
      "max_structure_candidates": 50,
      "structure_timeout_ms": 800,
      "max_global_reports": 2,
      "max_global_model_calls": 2
    }
  }
}
```

注意：

- `communities` 依赖 `relations`，`RetrievalProfile` 现有校验已覆盖；
- 只开 `events` 的 KB 可以跑 fusion，但没有 Global 背景；
- 只开 `relations` 的 KB 可以跑 fusion，事件通道跳过。

### 11.2 最小请求

```json
{
  "query_text": "甲校新增专业有哪些实践合作资源？",
  "top_k": 10,
  "return_mode": "evidence_groups",
  "strategy": {
    "family": "tiered",
    "tier": "fusion",
    "fusion": {
      "rank_mode": "rrf",
      "event_policy": "independent",
      "event_reserved_slots": 2
    },
    "budget": {
      "candidate_k": 20,
      "relation_quota": 30,
      "event_quota": 20
    }
  }
}
```

### 11.3 不建议的做法

- 把 base、relation、event、global_support 四路平铺做 RRF；
- 把 Global 摘要当候选 chunk；
- 让事件通道默认 `W_evt=0.8` 且不设保留位；
- 把 `direct_hit` 放在事件排序的正向位置；
- 为 fusion 新增数据库列或重建离线索引；
- 默认开启 fusion 或让 auto 路由无条件选 fusion；
- 用总体平均指标掩盖单跳子集的负向回归。

---

## 12. 与现有文档的关系

- 本文同时承担目标形态与实施顺序：第 2 节定义目标形态，第 5 节定义契约与算法，第 7 节定义验收；
- 当前运行时行为以 `docs/architecture.md` 和代码为准；
- 实施完成后，再更新 `docs/api.md` 与 `docs/architecture.md` 的 Current 契约；
- 后续若拆分独立的目标设计文档，以本文第 2、5、7 节为基线，避免两份文档出现两套契约。