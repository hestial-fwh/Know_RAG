# 通用文档关系图标注与验收样例

> 状态：Phase 2 统一关系模型与持久化已通过  
> 审查日期：2026-07-23  
> 依赖计划：`docs/document_relation_graph_phased_plan.md`  
> 18 个顶层 case 已写入 `backend/tests/fixtures/document_relation_phase0/dataset.json`；
> P4/P5 原已知失败在 Phase 1 全部转为普通通过测试。四类确定性 PDF 默认各生成 40 页；
> 真实 MinerU 样本只用于独立准确率校准。

## 1. 标注格式

每个样例保存以下层级输入和 gold：

```text
raw parser fixture（如可得）
→ ParsedBlock[]
→ normalized Canonical Markdown + provenance + assets
→ expected CanonicalRelationEdge[] / CanonicalAssetGroup[]
→ expected Chunk relation summaries
→ expected retrieval expansion
```

每条 gold relation 至少包含：

- `relation_type`、ordered source/target IDs；
- `status=bound|ambiguous|unmatched|rejected`；
- `auto_bind_allowed`；
- `binding_method`、`binding_score`、`score_kind`；
- cue occurrence `[start,end)`、page、section、source order；
- source/target bbox 与 coordinate space（存在时）；
- candidates 和 `ambiguity_reasons`；
- algorithm/schema version。

测试层级：**U** 为纯逻辑单元测试，**I** 为 artifact/ORM/检索集成测试，**P** 为真实生产入口
`rebuild_document_chunks()` 或 `hybrid_search()` 测试。任何 P 验收不能由只调用 helper 的 U 替代。

## 2. 固定样例

表中 block 采用简写：`T(text,page,bbox,section,order)`、`I(caption,page,bbox,section,order)`。
所有 bbox 均为同一 parser coordinate space；特别说明缺失时为 `null`。

| Case ID / 依赖阶段 | 输入 block（最小固定内容） | 预期 relation / group | 预期 status；允许自动绑定 | 必须保留的 evidence | 层级 |
|---|---|---|---|---|---|
| `REL-CASE-001` / P1 | `T("如图1所示，系统启动",p1,[20,20,400,60],S,0)`；`I("图1 系统结构",p1,[20,100,400,300],S,1)` | 一个 occurrence-level `explicit_reference`，source T → target I | `bound`；是 | figure token/cue range、figure_id、page/section match、candidate rule score、完整 child coverage | U/I/P |
| `REL-CASE-002` / P1-P2 | `T("如图1所示…",p1,...,S,0)`；`T("再次参见图1…",p2,...,S,1)`；同一 `I("图1",p1,...,S,2)` | 两条独立 explicit edges；图片侧保留两个 incoming occurrence，不是一个被覆盖的对象 | 两条 `bound`；是 | 两个 source node/occurrence range、各自 method/score、稳定 relation_id | U/I/P |
| `REL-CASE-003` / P1 | 一个包含“如图1所示”的 source range，经 overlap 产生两个正文 child；一个图1 asset | 只把 occurrence 物化到完整覆盖 `[start,end)` 且范围最小的 child | `bound`；是；另一个 child 无摘要 | 两个 child ranges、选择/排除理由、source child seq | U/I/P |
| `REL-CASE-004` / P3 | `T("以下作品展示同一主题",p1,[0,0,900,70],S,0)`；左右两个无独立 caption 的 I | 一个二图 group；一条 `shared_context_of` 指向 group；两个 `member_of_group` | authoritative extent 且唯一 partition 时 `bound`；derived extent 时可 `ambiguous` | page extent source、top text 横向覆盖、两图 bbox/间距、section/order | U/I/P |
| `REL-CASE-005` / P3 | 两个 I，各自紧邻独立 `T("图1…",...)`、`T("图2…",...)` caption，无公共顶部说明 | 两条 `caption_of`；可有 group candidate，但不得产生 shared context | captions `bound`；shared `unmatched/rejected` | caption ownership scores、独立 bbox、拒绝公共说明原因 | U/I/P |
| `REL-CASE-006` / P4 | 同页上下两个 I；中间/下方 `T("上图展示原方案，下图展示优化方案",...)` | 两条 `relative_reference`，分别 target upper/lower asset | geometry 唯一时 `bound`；是 | cue span、direction、candidate centers/cones、page/section、distance | U/I/P |
| `REL-CASE-007` / P4 | 同页左右两个 I；`T("左图为输入，右图为输出",...)` | 两条 relative edges，分别 target left/right | geometry 唯一时 `bound`；是 | x ordering、horizontal alignment、cue-to-target mapping、extent source | U/I/P |
| `REL-CASE-008` / P3-P4 | `T("以下两图为处理前后结果",p1,top,S,0)`；紧随两个 I | before_after 或最保守二图 group；source → group 的 relative/shared edge | 唯一二图候选时 `bound`；是 | 明确数量“2”、source order window、member ordering、group type 选择依据 | U/I/P |
| `REL-CASE-009` / P3-P4 | 同页三张等距 I；`T("该图展示结果",...)`，无图号/方向/唯一 antecedent | 保留三个 candidates，不产生确定 target | `ambiguous`；否 | 全 candidate list、等分/缺 cue reason、page/section、各 bbox | U/I/P |
| `REL-CASE-010` / P3-P4 | `T("下图展示结果",p1,S-A)`；最近 I 位于 `S-B`，S-A 内无图 | 不跨 section 自动绑定 | `unmatched` 或 `ambiguous`；否 | section mismatch、candidate 被拒原因、heading/provenance boundary | U/I/P |
| `REL-CASE-011` / P3 | p1 末尾标题/公共说明，图片在 p2；没有明确图号或 continuation metadata | 不自动生成 shared/group relation；可保存跨页候选 | `ambiguous`；否 | page delta、source order、section、缺少明确跨页证据 | U/I/P |
| `REL-CASE-012` / P3-P4 | 同页文本和两图，但任一目标 `bbox=null` | 不做方向确定或唯一组 partition | `ambiguous`；否 | `missing_target_geometry`、已知 page/order/section、未选 candidates | U/I/P |
| `REL-CASE-013` / P1-P2 | 两张图片都 caption“图1”；正文“如图1所示”；或单图 caption“图7、图8” | repeated figure candidates 或 P1 ambiguous asset；不得按最近距离强绑 | `ambiguous`；否 | 原始 captions、normalized figure IDs、page/section comparisons、candidate scores | U/I/P |
| `REL-CASE-014A-D` / P0/P3 | 四套 fixture：图册页（多图少文）、书法作品页（单/多作品+题签）、论文页（正文+编号图）、产品说明书（callout+步骤图） | gold 由人工逐页标注；不得套用统一“多图即 gallery”规则 | 每条独立标 `bound/ambiguous/unmatched`；只有 gold 允许才自动 | 原始 parser response、真实/derived extent、角色、bbox、page、section、source order、asset text | U/I/P，参数化四类 |
| `REL-CASE-015` / P1-P5 | 使用 CASE-001/004/006，依次关闭总 graph、materialization、expansion、group、implicit flag | 关闭能力不产生对应 artifact/Chunk/expanded result；普通 chunks/results 保留 | `unmatched/not_evaluated` 或无 relation；禁止自动 | flag snapshot、原始 Chunk IDs/排序/数量、降级原因 | U/I/P |
| `REL-CASE-016` / P1 | hybrid 原始命中含 CASE-001 正文 chunk，其 metadata 指向同 doc image chunk | 在原始命中后按契约追加图片；不改变原始 hit score/order | expansion `bound/applied`；允许 | source hit ID、relation ID、target ID、KB/doc equality、budget、expansion order | U/I/P |
| `REL-CASE-017` / P1/P3 | hybrid 原始命中图片；图片有两个正文 reference 和一个 shared context/group | 追加 ordered 正文 references、公共说明和 group 摘要；按 relation-type budget 截断 | 有效关系 `applied`；允许 | 每条 relation ID/type/order、scope validation、budget decision、dedup reason | U/I/P |
| `REL-CASE-018` / P1 | doc A 命中 chunk 的 metadata 被污染，包含 doc B/KB B 的 chunk ID | 不追加污染 target；原始 doc A result 正常返回 | expansion target `rejected_scope_mismatch`；禁止；检索整体成功 | requested ID、resolved target doc/KB、source doc/KB、拒绝 reason、降级 audit | U/I/P |

## 3. Phase 1 必须新增的边界测试矩阵

| Acceptance ID | 断言 |
|---|---|
| `REL-AT-P1-001` | child 必须满足 `child.start <= ref.start` 且 `child.end >= ref.end`。 |
| `REL-AT-P1-002` | 多个完整覆盖 child 时选择 range 最小者；仍并列时按文档顺序稳定选择一个。 |
| `REL-AT-P1-003` | 只允许明确配置的 prose chunk types；`parent_text/image/image_ocr_text/equation` 不得成为 reference carrier。 |
| `REL-AT-P1-004` | occurrence 去重键包含 relation/occurrence identity，不以 `figure_id + image_id` 吞掉重复提及。 |
| `REL-AT-P1-005` | image incoming bindings 是 ordered list，多条 method/score/score_kind 不覆盖。 |
| `REL-AT-P1-006` | expansion 查询同时限制 target ID、source `knowledge_base_id` 和 `document_id`。 |
| `REL-AT-P1-007` | SQL/ORM 返回任意顺序时，最终结果恢复 metadata 请求顺序。 |
| `REL-AT-P1-008` | 第二个原始 hit 拥有独立 3-image/5-text budget，不被第一个 hit 消耗。 |
| `REL-AT-P1-009` | malformed metadata、missing target、DB exception 均 fail-open，原始 results 不变。 |
| `REL-AT-P1-010` | expansion flag off 时 `_expand` 不查询关系 target，原始排序和分数逐项相同。 |

### Phase 2 统一关系图验收

| Acceptance ID | 断言 |
|---|---|
| `REL-AT-P2-001` | caption_of、footnote_of、explicit_reference 均无损进入 versioned edge。 |
| `REL-AT-P2-002` | 同一 asset 的三种关系可共存，relation ID 与排序在相同输入下稳定。 |
| `REL-AT-P2-003` | 自动 edge 必须具有 method、score_kind、status、evidence 和 algorithm_version。 |
| `REL-AT-P2-004` | `document_relation/` 纯逻辑包不得导入 SQLAlchemy/ORM。 |
| `REL-AT-P2-005` | graph shadow on/off 的 Chunk 数量、类型、正文和范围完全一致。 |
| `REL-AT-P2-006` | 新 graph 完成 round-trip；空新列从 legacy `figure_links_json` dual-read。 |
| `REL-AT-P2-007` | Chunk 只保存 compact summaries，不复制 evidence/candidate graph。 |

## 4. 标注判定规则

1. **自动 bound 的最低条件**：目标唯一，证据字段完整，未跨 parser coordinate space，未跨禁止的
   page/section 边界，resolver 明确允许自动绑定。
2. **ambiguous 优先于猜测**：两个候选低于既定 margin、extent 非权威且影响排序、bbox 缺失、通用代词
   无唯一 antecedent 时必须 ambiguous。
3. **unmatched 与 rejected 区分**：没有候选为 unmatched；有候选但违反 scope/section/parser boundary 为 rejected。
4. **分数命名**：规则组合使用 `heuristic_rule_score`；文本向量相似度使用 `cosine_similarity`；都不能叫 confidence。
5. **标注集不足**：在各文档类型至少 5 页只是工程基线，不足以承诺生产准确率。Phase 3/5 上线阈值必须在
   数据集扩充后另行冻结，不能在实现完成后反向选择阈值。

## 5. 回归与退化验收

- `REL-AT-REG-001`：关系 resolver 抛异常时，普通 text/table/image Chunk 数量与 flag-off baseline 相同。
- `REL-AT-REG-002`：artifact/Chunk flush 失败时，旧 revision、旧 chunks、旧 embeddings 保持一致。
- `REL-AT-REG-003`：关系关闭后 hybrid 原始 hit 的 chunk ID、score、rank、match_type 不变。
- `REL-AT-REG-004`：MinerU 与 RAGFlow fixtures 分别处理；测试禁止把两者 bbox 放入同一 page graph。
- `REL-AT-REG-005`：Canonical asset sidecar 和 graph artifact 不保存 base64；媒体仍由现有存储边界负责。
- `REL-AT-REG-006`：候选全证据只在 artifact；Chunk metadata 不出现完整 candidate graph 副本。

## 6. 数据集与报告输出

当前数据源和复现命令见 `document_relation_phase0_baseline.md`。手写简写 case 只用于冻结规则契约；
四类工程覆盖率来自实际渲染的确定性 PDF。报告必须分别标记 `deterministic_synthetic` 与
`real_validation`，禁止用生成语料声明真实准确率。

Phase 0 每次 replay 输出：

- case/document/page 数量及 parser 版本；
- bbox、page_no、authoritative page extent、section_path、VLM description 的字段覆盖率；
- 每 relation/group type 的 bound/ambiguous/unmatched/rejected 数量；
- occurrence-level precision/recall、group exact-match、unsafe false-positive 数；
- flag-off compatibility diff；
- 失败样例 ID，不只给汇总百分比。

跨页样例的候选图片必须位于下一物理 PDF 页；overlap 样例必须在实际 chunk range 中产生至少
两个完整覆盖同一引用 occurrence 的 child。retrieval output 必须逐个 replay 当前物化的所有
direct binding。报告还校验 gold ID 到 layout block 的完整映射，不能以空 layer 或场景标签代替
真实覆盖。

没有人工 gold 或没有报告原始分母时，不得把测试结果描述为准确率通过。

## 7. 当前测试缺口映射

| 当前测试 | 已覆盖 | 未覆盖 |
|---|---|---|
| `test_figure_linker.py` | 图号语法、代码排除、page/section 消歧、ambiguous | Chunk overlap occurrence、一图多正文物化、图片组/隐式引用 |
| `test_image_relation_resolver.py` | caption/footnote、bbox 候选、共享 caption 歧义、生产 ORM image metadata | 真实页面 extent、图片组、全文 shared context |
| `test_canonical_parent_child_integration.py` | artifact、range 基本形状、单关系 P4 happy path | full range containment、唯一 carrier、多关系不覆盖、P5 |
| `test_canonical_pipeline_convergence.py` | Canonical 清洗、结构与 parent 收敛 | relation graph/expansion |
| `test_retrieval_mapping.py` | flat result 字段映射 | `_expand_figure_bindings()` 的 scope/order/budget/fallback |
| `test_document_relation_dataset_generator.py` | 四类 PDF/PNG、19 个生成场景、固定哈希、五层 replay、`图1‑图2` 范围引用、真实跨 document 污染 replay | 真实 MinerU 精度校准 |
| `test_document_relation_phase0_entrypoints.py` | 生成语料接入 rebuild/hybrid 生产入口 | 外部服务端到端性能与容量 |
| `test_document_relation_phase2.py` | 统一三类 edge、稳定 ID、round-trip、legacy dual-read、状态合并、shadow 与 compact materialization | Phase 3 group/shared resolver |

因此，现有测试通过只能证明当前显式 happy path 没有回归，不能作为通用关系层或 P5 安全边界已经完成的证据。

## 8. Phase 1 前端验证方案

本阶段没有修改前端源码；图文关系物化发生在重新解析时，关系扩展发生在后端混合检索响应中。
因此仅重新构建前端不会启用或刷新这项能力。测试环境先在 `.env` 设置：

```dotenv
DOCUMENT_RELATION_MATERIALIZATION_ENABLED=true
DOCUMENT_RELATION_RETRIEVAL_EXPANSION_ENABLED=true
```

然后重新构建并强制重建 backend；对验证文档重新执行解析和向量化，使新关系元数据进入 chunks。
前端只需正常启动；若要排除旧静态资源，再单独重建并强制重建 frontend，随后浏览器硬刷新。

在 `/debug/search` 选择测试知识库，关闭“按父块聚合命中子块”以直接观察 `results`。同时打开浏览器
DevTools 的 Network，检查 `POST /api/v1/knowledge-bases/{kb_id}/hybrid-search` 原始响应：

| 场景 | 页面验收 | Network 响应验收 |
|---|---|---|
| 两个 flag 均关闭 | 结果数量、顺序、分数与旧基线一致 | 不出现 `retrieval_expansion=true` |
| 仅物化开启 | 重新解析后的切片详情可看到 `figure_references`、`image_bindings[]` 及兼容字段 `image_binding` | 检索仍不追加关系目标 |
| 文本命中图片 | 原始文本结果在前，关联图片随后显示且可预览 | 图片项有 `retrieval_expansion=true`、`expansion_source=figure_binding`、正确的 `expansion_source_id` |
| 图片命中文本 | 原始图片结果在前，关联正文按 binding 顺序追加 | 追加 ID 顺序与 `reference_chunk_ids` 一致 |
| 去重和配额 | 页面不出现同一切片两次 | 每个原始 hit 最多追加 3 张图片、5 个文本；已直接命中、缺失或跨作用域目标不消耗配额 |
| 文档/知识库隔离 | “命中文档”中不出现污染文档 | 被篡改为其他 document/KB 的目标 ID 不进入 `results` |
| DB 扩展失败 | 原始结果仍正常显示 | `success=true`，原始结果不变，顶层 `message` 包含降级说明；当前前端客户端不展示该顶层消息，必须在 Network 检查 |

建议保存两组证据：页面截图（结果顺序、图片预览、“命中文档”去重）和 Network 响应 JSON
（chunk ID、作用域、扩展标记、来源 ID、顶层降级消息）。回滚时关闭两个 flag 并重建 backend；本阶段
无数据库迁移，不需要执行 migration rollback。
