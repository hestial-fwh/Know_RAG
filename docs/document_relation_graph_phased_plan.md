# 通用文档关系图分阶段实施计划

> 状态：Phase 2 PASS，Phase 3 可开始  
> 审查日期：2026-07-23  
> 依赖审查：`docs/document_relation_graph_feasibility.md`  
> Phase 0 已完成确定性生成语料与基线；Phase 1 已完成显式关系安全物化、scoped expansion、
> dual-read 和独立回滚开关。真实数据仅作为准确率校准集。

## 1. 总体约束

- 阶段顺序固定为 Phase 0 → 1 → 2 → 3 → 4 → 5；Phase 6 是后续产品阶段。
- 每阶段可单独开发、测试和通过 feature flag 回滚；FAIL 时不得进入依赖阶段。
- `document_relation/` 关系解析模块不得依赖 SQLAlchemy。
- FigureLinker 保留为 explicit resolver；ImageRelationResolver 只负责图片自身 caption/footnote。
- MinerU 与 RAGFlow 坐标分别处理，不混合 page graph；首期准确率只评估 MinerU 样本。
- 关系失败不得阻止普通 text/table/image Chunk 生成、embedding 或基础检索。
- 自动关系必须保存 `evidence`、`binding_method`、`binding_score`、`score_kind`、`status`；
  heuristic 分数不得命名为 confidence/probability。
- 每阶段验收编号唯一；固定样例定义见 `document_relation_graph_acceptance_tests.md`。

## 2. Feature flag 设计

建议分层开关，默认值按阶段逐步开启：

| Flag | 控制边界 | 关闭后的行为 |
|---|---|---|
| `DOCUMENT_RELATION_GRAPH_ENABLED` | 通用 graph orchestrator 总开关 | 仅运行现有 Canonical/Chunk 基础链路 |
| `DOCUMENT_RELATION_MATERIALIZATION_ENABLED` | graph → Chunk 摘要 | artifact 可保留，Chunk 不写通用摘要 |
| `DOCUMENT_RELATION_RETRIEVAL_EXPANSION_ENABLED` | 检索扩展 | 返回原始 hybrid results |
| `DOCUMENT_ASSET_GROUP_ENABLED` | 图片组/公共上下文 | 不生成 group/shared edges |
| `DOCUMENT_IMPLICIT_REFERENCE_ENABLED` | 方向隐式引用 | 只保留 caption/footnote/explicit relations |
| `DOCUMENT_RELATION_SEMANTIC_RERANK_ENABLED` | 现有 VLM 文本重排 | 保持纯规则候选排序 |

P4/P5 现有显式关系也应纳入 materialization/expansion 开关。flag 读取可以位于配置/编排层，纯逻辑
resolver 接收显式 options，不直接读取环境变量。

## 3. Phase 0：基线审计与数据集准备

**依赖阶段：**无。  
**目标：**冻结当前 P4/P5 行为、样本和错误率，不写生产实现。

**当前状态（2026-07-23）：PASS。** 默认生成四类各 40 页、共 160 页，19 个场景每类全覆盖，
raw → ParsedBlock → Canonical → Chunk metadata → retrieval 五层 replay 完整。真实样本改为上线准确率
校准门禁，不再阻塞 Phase 1。可复现结果见 `document_relation_phase0_baseline.md`。

- **修改模块：**仅测试 fixtures、离线评估工具和文档；固定原始 MinerU response、ParsedBlock、
  Canonical artifact、Chunk metadata、retrieval output 五层 golden snapshots。
- **明确不修改：**生产 parser/chunker/retrieval、ORM、迁移、vendor、前端。
- **输入 → 输出：**四类确定性渲染文档、19 个生成场景与 18 个固定契约样例 →
  gold relation/status/evidence、bbox/page coverage 报告、
  当前 P4/P5 baseline。
- **数据结构变化：**无生产结构变化；标注格式采用 Phase 2 proposed schema 的最小子集。
- **兼容策略：**不改变运行时。
- **Feature flag：**无新运行时 flag；记录现有“无关系 flag”为基线缺陷。
- **单元测试：**冻结 FigureLinker、ImageRelationResolver、range/overlap 与 expansion 的当前输出。
- **集成测试：**从 ParsedDocument 到 ORM draft；从 metadata 到 expansion 的只读 fixture 测试。
- **生产入口测试：**通过 `rebuild_document_chunks()` 和 `hybrid_search()` 的测试数据库路径记录 baseline；
  禁止只测未接 dispatcher 的 helper。
- **数据库迁移：**无。
- **回滚边界：**删除新增 fixtures/评估文档即可；生产无变化。
- **PASS：**
  - `REL-P0-001`：18 个固定样例全部具有唯一 case ID、输入、gold status、允许自动绑定标记和 evidence。
  - `REL-P0-002`：图册、书法、论文、产品说明书各至少 5 页确定性 PDF 完成 bbox/page/role 缺失率统计；
    默认规格提高到各 40 页，并保证 19 个生成场景每类无缺口。
  - `REL-P0-003`：MinerU-shaped raw response 与可选 live response 都有可复查路径；raw page width/height
    到 ParsedBlock 的字段损失可量化。
  - `REL-P0-004`：当前 P4/P5 的已知错误均有失败测试或明确的不可构造原因。
- **FAIL：**任一固定样例无 gold；任一文档类型缺场景；无法追踪 raw response 到 Chunk；默认 baseline
  依赖外部服务；固定 seed 不能复现相同 PDF/图片哈希。
- **阶段后文档更新：**本文 Phase 0 状态、acceptance test 数据来源、architecture 的已知限制。

## 4. Phase 1：修复现有 P4/P5 边界

**依赖阶段：**Phase 0 PASS。  
**目标：**在引入新关系类型前，使显式关系的物化与扩展安全、确定、可关闭、可回退。

**当前状态（2026-07-22）：PASS。** 原 7 个严格 `xfail` 已转为普通通过测试；两个开关默认关闭，
完整 160 页 Phase 0 replay 继续 PASS。实现没有修改 FigureLinker、parser、数据库 schema 或图片组规则。

- **修改模块：**从 `chunk_service._apply_figure_bindings()` 抽出纯 occurrence materializer；从
  `retrieval_service._expand_figure_bindings()` 抽出 scoped expansion service；配置层增加 flags。
- **明确不修改：**FigureLinker 识别规则、ImageRelationResolver、parser、vendor、数据库 schema、图片组逻辑。
- **输入 → 输出：**现有 `FigureLinkResult + ChunkDraft identity/ranges` → occurrence-level 双侧摘要；
  raw hybrid hits → ordered、scoped expanded hits。
- **数据结构变化：**Chunk metadata 兼容新增 `image_bindings[]` 与 occurrence identity；读取端继续兼容旧
  `image_binding` 单对象。不得复制 artifact 全证据。
- **兼容策略：**dual-read；新写 list，必要时保留旧字段的单关系投影；旧文档无需立即 reparse，扩展读取两种格式。
- **Feature flag：**materialization 与 retrieval expansion 两个独立 flag；默认先 shadow/off，验收后开启。
- **单元测试：**完整 range 覆盖、最小唯一 child、parent/image/OCR/equation 排除、多引用不覆盖、occurrence 去重、
  每 hit 限额、请求顺序恢复、污染 ID 拒绝、异常 fail-open。
- **集成测试：**P4 双侧 ORM metadata；P5 同 KB/doc 双向扩展、跨边界拒绝。
- **生产入口测试：**`rebuild_document_chunks()` 后查询真实 Chunk；`hybrid_search()` 开关 on/off 对比原始排序。
- **数据库迁移：**无。
- **回滚边界：**关闭两个 flag 即退化为普通 Chunk 与原始 hybrid results；旧字段读取仍保留。
- **PASS：**
  - `REL-P1-001`：一个 reference occurrence 只绑定完整覆盖其 `[start,end)` 的最小允许正文 child。
  - `REL-P1-002`：一图多引用的 method/score/occurrence 均不被覆盖，顺序稳定。
  - `REL-P1-003`：污染 metadata 无法扩展到其他 document 或 knowledge base。
  - `REL-P1-004`：扩展 DB/JSON 异常时响应与未扩展原结果逐项相同，并记录可观测降级信息。
  - `REL-P1-005`：限额语义冻结为“每个原始 hit、每 relation type”，并有 2+ hit 测试。
  - `REL-P1-006`：两个 flag 关闭时普通检索结果、排序和数量不变。
- **FAIL：**存在跨边界扩展；异常导致整个检索失败；相同 occurrence 仍映射多 child；关闭 flag 仍写/读关系。
- **阶段后文档更新：**architecture、api、security、image binding current 文档、backend module map。

## 5. Phase 2：统一关系模型与持久化结构

**依赖阶段：**Phase 1 PASS。  
**目标：**建立纯逻辑 `CanonicalRelationGraph`，把现有 caption/footnote/explicit 关系统一为多边模型。

**当前状态（2026-07-23）：PASS。** 已实现纯逻辑 edge/group/graph 模型、稳定 relation ID、
caption/footnote/explicit merger、legacy `figure_links_json` dual-read、compact Chunk summaries 和
独立 ORM persistence adapter。迁移 `0012_document_relation_graph.py` 仅新增
`relation_graph_json`；相关回归、160 页生成语料、140 个 retrieval replay case 和 8 个真实
跨文档污染 replay 全部通过。
`DOCUMENT_RELATION_GRAPH_ENABLED` 默认关闭，开启时先以 shadow artifact 方式运行；未实现任何
Phase 3 图片组或公共上下文规则。

- **修改模块：**新增 `document_relation/models.py`、`relation_graph_merger.py`、纯 `materializer.py`；
  新增独立 ORM persistence adapter；Canonical orchestrator 只接线。
- **明确不修改：**FigureLinker 识别算法、ImageRelationResolver 局部职责、RAGFlow vendor、隐式引用规则、前端审核。
- **输入 → 输出：**Canonical assets/provenance + ImageRelation/FigureLinkResult → versioned graph + Chunk summary instructions。
- **数据结构变化：**实现 feasibility 文档中的 Edge/Group schema；本阶段 group 可以为空；artifact schema version 升级。
- **兼容策略：**`figure_links_json` 继续写兼容投影；旧 artifact 读为 graph v1 adapter；旧 Chunk metadata dual-read。
- **Feature flag：**总 graph flag 与 materialization flag；shadow 模式生成 artifact 但不影响 Chunk/retrieval。
- **单元测试：**稳定 relation ID、多 source/target、状态优先级、ambiguous 保留、证据完整、序列化 round-trip、
  无 SQLAlchemy import 检查。
- **集成测试：**artifact 与 chunks 同事务；flush 失败回滚；fallback chunk 成功时旧 graph 不残留。
- **生产入口测试：**`rebuild_document_chunks()` shadow on/off 对比 Chunk 主体与数量完全一致。
- **数据库迁移：**一个 additive migration，为 `document_canonical_artifacts` 增加 `relation_graph_json`；
  downgrade 只删除新列，不触碰旧 `figure_links_json`。
- **回滚边界：**关闭 graph flag 并回退读取到 `figure_links_json`；migration downgrade 不丢旧显式关系。
- **PASS：**
  - `REL-P2-001`：现有 caption_of、footnote_of、explicit_reference 全部可无损转为 edge。
  - `REL-P2-002`：同一 asset 至少 3 条不同 relation 可共存并保持稳定顺序。
  - `REL-P2-003`：所有自动 edge 均有 method、score_kind、status、evidence、algorithm_version。
  - `REL-P2-004`：关系纯逻辑包 import graph 中不存在 SQLAlchemy/ORM。
  - `REL-P2-005`：事务错误后 artifact、Chunk 和旧关系仍为同一旧 revision。
- **FAIL：**兼容投影丢 occurrence；自动关系缺 evidence/status；reparse 出现 graph/chunk revision 不一致。
- **阶段后文档更新：**database、architecture、backend module map、API artifact schema。

## 6. Phase 3：图片组与公共上下文

**依赖阶段：**Phase 2 PASS，且 Phase 0 页面几何基线完成。  
**目标：**生成同页 group/shared-context 候选；不处理通用代词。

- **修改模块：**新增 `page_layout_graph.py`、`asset_group_resolver.py`、`shared_context_resolver.py`；
  MinerU adapter 仅在已验证原始字段存在时标准化真实 page extent。
- **明确不修改：**RAGFlow vendor、跨解析器坐标融合、FigureLinker、检索排序、页面截图 VLM、前端审核。
- **输入 → 输出：**单 parser page nodes/assets/真实或 derived extent → groups、member_of_group、shared_context_of edges。
- **数据结构变化：**启用 `CanonicalAssetGroup`；evidence 必须标 `page_extent_source=authoritative/derived/missing`。
- **兼容策略：**缺真实 extent 或 bbox 时生成 ambiguous candidate 或不生成，基础 Chunk 不变。
- **Feature flag：**`DOCUMENT_ASSET_GROUP_ENABLED`，默认 off/shadow。
- **单元测试：**横/竖系列、多 panel、共享顶部说明、独立 caption、跨 section 拒绝、缺 bbox、并列 partition。
- **集成测试：**group/edge 进入 artifact；Chunk 只保存 group/关系 ID 摘要。
- **生产入口测试：**四类文档各至少 5 页经 `rebuild_document_chunks()`，flag off 的 Chunk 主体一致。
- **数据库迁移：**无，复用 Phase 2 graph JSON。
- **回滚边界：**关闭 group flag；忽略 group/shared edges，不删除普通 assets。
- **PASS：**
  - `REL-P3-001`：固定同页双图公共说明样例生成一个 group 和一条 shared edge。
  - `REL-P3-002`：独立 caption 不被提升为公共说明。
  - `REL-P3-003`：跨 section 图片不自动成组。
  - `REL-P3-004`：缺 bbox/非权威 extent 的不唯一候选为 ambiguous，不得 bound。
  - `REL-P3-005`：四类样本分别报告 precision/recall；无预设最低值时不得宣称生产准确率。
- **FAIL：**跨 section 自动绑定；缺 geometry 仍 bound；group 失败阻止 image Chunk 生成。
- **阶段后文档更新：**architecture、database schema 示例、图片关系 Current 文档与评估报告。

## 7. Phase 4：隐式方向引用

**依赖阶段：**Phase 3 PASS。  
**目标：**只支持有限、可解释的“上图/下图/左图/右图/以下 N 图”同页规则。

- **修改模块：**新增 `implicit_reference_resolver.py`，复用 page layout graph；graph merger 增加 conflict policy。
- **明确不修改：**通用“该图/上述图片”自动确定、跨页隐式引用、外部模型、vendor。
- **输入 → 输出：**含方向 cue 的 source node + page graph/groups → relative_reference edge/candidates。
- **数据结构变化：**edge evidence 增加 cue span、direction cone、candidate geometry、count constraint。
- **兼容策略：**无唯一候选即 ambiguous；explicit relation 优先但不得删除冲突的 implicit candidate。
- **Feature flag：**`DOCUMENT_IMPLICIT_REFERENCE_ENABLED`，独立关闭。
- **单元测试：**上下左右、以下两图、等距候选、缺 bbox、跨 section、explicit 冲突。
- **集成测试：**方向 edge artifact + Chunk 摘要；关闭时无新增摘要。
- **生产入口测试：**固定真实版面经 parse/rebuild；检查同页/同 section 边界。
- **数据库迁移：**无。
- **回滚边界：**关闭 implicit flag，保留 explicit/group/shared。
- **PASS：**
  - `REL-P4-001`：四个方向各在唯一几何候选时 bound，evidence 可重算。
  - `REL-P4-002`：“以下两图”只在恰有唯一二图 group 时 bound。
  - `REL-P4-003`：等距、缺 bbox、跨 section 候选均 ambiguous/unmatched。
  - `REL-P4-004`：关闭 flag 后 artifact/Chunk 不含 relative_reference。
- **FAIL：**通用代词被强绑；方向跨页；implicit 覆盖 explicit evidence。
- **阶段后文档更新：**关系类型契约、规则说明、评估矩阵。

## 8. Phase 5：语义重排与低分处理

**依赖阶段：**Phase 4 PASS，且标注集足以离线评估。  
**目标：**只利用已有 caption/OCR/VLM/正文文本重排既有候选，不调用新模型、不扩大候选集合。

- **修改模块：**新增纯 semantic reranker；读取 `CanonicalAsset.vlm_description` 等 sidecar 文本；
  relation merger 增加 score provenance。
- **明确不修改：**页面截图 VLM、云端模型调用、candidate generation、数据库表、vendor。
- **输入 → 输出：**规则 candidates + 已有文本特征 → 稳定重排、bound/ambiguous 状态。
- **数据结构变化：**evidence 保存 feature versions；`score_kind=cosine_similarity` 或 composite heuristic，禁止 confidence。
- **兼容策略：**缺 VLM/embedding 时保持规则排序；模型/特征异常 fail-open 到原 candidates。
- **Feature flag：**`DOCUMENT_RELATION_SEMANTIC_RERANK_ENABLED`。
- **单元测试：**不扩大 candidates、稳定 tie、缺描述回退、异常回退、score_kind 命名。
- **集成测试：**artifact 同时保存规则分与重排分；Chunk 只保存最终摘要。
- **生产入口测试：**标注集离线 replay，不依赖实时外部服务。
- **数据库迁移：**无。
- **回滚边界：**关闭 rerank flag，恢复 Phase 4 规则顺序与状态。
- **PASS：**
  - `REL-P5-001`：所有 reranked target 都来自输入 candidate set。
  - `REL-P5-002`：缺失/异常 VLM 文本时输出与规则 baseline 相同。
  - `REL-P5-003`：冻结标注集上预先约定的 precision 不下降，ambiguous rate 不低于安全下限。
  - `REL-P5-004`：输出和 API 中不存在把 heuristic/similarity 命名为 confidence/probability 的字段。
- **FAIL：**重排引入新候选；异常阻断 chunk/retrieval；没有冻结阈值就宣称准确率提升。
- **阶段后文档更新：**算法版本、离线评估、模型/本地数据边界说明。

## 9. Phase 6：前端审核与人工覆盖（后续阶段）

**依赖阶段：**Phase 5 可选；至少 Phase 3 PASS 和产品审核流程确认。  
**目标：**展示 graph/evidence/candidates，允许人工接受、拒绝、改绑，并跨 reparse 审计。

- **修改模块：**只读 graph API、override/audit service、前端审核页面；独立 persistence adapter。
- **明确不修改：**自动 resolver 规则、vendor、原始文件、历史 artifact 内容。
- **输入 → 输出：**自动 graph + 用户决策 → override projection 与 effective graph。
- **数据结构变化：**新增独立 override/audit 表，保存 actor、reason、before/after、base revision、stable fingerprint。
- **兼容策略：**无 override 时完全等于自动 graph；旧 revision override 不自动套用，先标 stale/review_required。
- **Feature flag：**审核 UI 与 override apply 分开；默认只读。
- **单元测试：**状态机、权限、stale revision、冲突、审计不可变。
- **集成测试：**API/DB transaction、reparse 后 stale、effective graph materialization。
- **生产入口测试：**真实浏览器审核一次 accept/reject/rebind，验证检索只使用已生效版本。
- **数据库迁移：**新增 override/audit 表；独立 downgrade，不删除 canonical artifact。
- **回滚边界：**关闭 override apply，恢复自动 graph；保留审计记录。
- **PASS：**
  - `REL-P6-001`：每次人工修改有 actor/time/reason/base revision/before/after。
  - `REL-P6-002`：reparse 后旧 override 不会静默应用到不匹配节点。
  - `REL-P6-003`：只读/关闭模式不改变自动 graph 与检索。
  - `REL-P6-004`：权限不足无法修改关系。
- **FAIL：**人工决定被 reparse 覆盖；无审计修改；旧 revision override 静默污染新 graph。
- **阶段后文档更新：**API、database、security、frontend、architecture、运维回滚手册。

## 10. 阶段推进决策

Phase 0、Phase 1 与 Phase 2 已 PASS。下一编码阶段是 **Phase 3：图片组与公共上下文**；在
Phase 3 通过前仍不应直接开发隐式方向关系。
