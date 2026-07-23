# 通用文档关系图可行性审查

> 状态：Draft  
> 审查日期：2026-07-22  
> 适用基线：`master` / `e4a5c11`  
> 本文只描述审查结论与目标设计，不表示 Planned 能力已经实现。
> 实现追踪：本文记录的是 Phase 0 前审查快照；其中 P4/P5 range、覆盖、scope、顺序、预算、
> fail-open 与开关问题已在 2026-07-22 的 Phase 1 修复。当前契约以 `architecture.md`、
> `image_chunk_binding_plan.md` 和 `document_relation_graph_phased_plan.md` 为准。

## 1. 结论摘要

当前架构适合继续改造。`ParsedDocument`、Canonical Markdown、asset sidecar、source provenance、
FigureLinker、Chunk 双侧摘要和 Canonical artifact 已经形成可复用骨架。无需推翻 FigureLinker；应将其
保留为 `explicit_reference` resolver，并在旁边增加纯逻辑关系模块。

进入图片组或隐式引用开发前，必须先修复现有 P4/P5 边界。当前主要风险不是“缺少更多规则”，而是：

- P4 没有要求 child 完整覆盖引用范围，也没有保证一个 occurrence 只落到一个最小正文 child；
- 图片侧 `image_binding` 是单对象，多条关系会互相覆盖字段；
- P5 实际已进入生产，但没有显式 knowledge base/document 隔离、关系顺序保证、异常回退或 feature flag；
- 当前页面坐标按内容块外包框推算，并非 PDF 真实页面尺寸；MinerU block metadata 也没有保留真实页宽高；
- 没有标注集时，不能承诺图片组、方向引用或指代消解准确率。

建议现在只进入 Phase 0（基线审计与固定样例），随后进入 Phase 1（修复现有 P4/P5）。首期通用关系范围
应限制为：统一边模型、同页图片组候选、公共上下文候选、明确的上/下/左/右规则、ambiguous 保留、
现有 VLM 描述候选重排，以及安全的检索扩展。跨页指代、通用“该图/上述图片”自动确定、页面截图 VLM
和人工审核工作台不应进入首期。

## 2. 审查基线与版本事实

### 2.1 Git 事实

审查开始时 `git status --short` 与 `git diff --stat` 均为空，没有需要避让或归属不明的未提交修改。
本轮只允许新增本文及同组方案文档。

| 能力 | Git 证据 | 当前判断 |
|---|---|---|
| P0 字段贯通 | `ca4e5dc image_bind_phase0` | 已提交 |
| P1 图片自身 caption/footnote 关系 | `cb8b7f7 phase 1 finished` | 已提交 |
| P2 显式 FigureLinker | `995f767 phase 2.5 finished` 同时引入 `figure_linker.py` | 已提交，但没有独立 P2 commit |
| P2.5 Canonical provenance artifact | `995f767 phase 2.5 finished` | 已提交 |
| P3 精确 child range | `53c4495 phase 3 finished` | 已提交；P4 选择逻辑仍未完整使用 range |
| P4 双侧 Chunk metadata | `a7e52c8 phase 4 finished` | 已提交，但存在本审查列出的边界缺陷 |
| P5 双向检索扩展 | `f9c35a7 phase 5 text_image_linked finished` | 已提交并由 `hybrid_search()` 无条件调用 |

### 2.2 文档与代码偏差

1. `docs/image_chunk_binding_plan.md:3` 仍声明“P5 规划中”，但
   `backend/app/services/retrieval_service.py:1440` 已在生产 `hybrid_search()` 中调用 P5。
2. `docs/image_chunk_binding_plan.md:342-350` 声明同知识库、同文档、每次扩展限额和失败回退；
   当前 `retrieval_service.py:1304-1385` 未满足这些约束。
3. `docs/architecture.md:30-34` 对 P3/P4 的措辞仍像后续职责，而 Git 与生产代码已经物化双侧关系。
4. `docs/backend_module_map.md` 对主流程的方向正确，但行数、Canonical/关系步骤和 P5 状态已经落后于代码。

## 3. 当前真实生产链路

以下以实际调用链为准。ImageRelationResolver 不是在 CanonicalDocument 完成后单独调用，而是在
`build_canonical_document()` 构造每个 `CanonicalAsset` 时调用。

| 步骤 | 生产入口与代码位置 | 输入 → 输出 | 实际调用 | 专项测试 | 当前职责边界 |
|---|---|---|---|---|---|
| 1. 解析入口 | `document_service.parse_document()`，`backend/app/services/document_service.py:420`；调用 `parser_service.parse_local_document()`，`parser_service.py:80` | `Document`/MinIO 文件 → `ParsedDocument` | 是，`document_service.py:485` | 指定测试未直接覆盖 dispatcher；`test_image_relation_resolver.py` 构造 ParsedDocument 后覆盖后半链路 | parser 选择、MinerU/RAGFlow fallback；不解析全文关系 |
| 2. OCR/VLM 与 block reflow | `enhance_image_blocks()`，`vision_service.py:505`；`reflow_parsed_document()`，`block_reflow.py:317` | `ParsedBlock[]` → 增强并重排/合并后的 `ParsedDocument` | 是，`document_service.py:569-570` | `test_canonical_pipeline_convergence.py` 覆盖部分 reflow 后约束；无指定 VLM 入口测试 | 图片自身语义增强、文本行重排；不建立跨 block 关系 |
| 3. Canonical 构建 | `build_canonical_document()`，`canonical_document.py:810`，由 `_rebuild_canonical_weknora_parent_child()` 调用 | `ParsedDocument` → Canonical Markdown、assets、source nodes | 是，`chunk_service.py:1069-1085` | `test_image_relation_resolver.py`、两个 Canonical integration/convergence 测试 | 唯一切分输入与 asset sidecar；不负责 ORM |
| 4. 图片自身关系 | `resolve_image_relation()`，`image_relation_resolver.py:306`，在 `canonical_document.py:924-948` 调用 | image block + 同页 caption/footnote candidates → `ImageRelation` | 是 | `test_image_relation_resolver.py` 有规则、空间、ambiguous、生产 ORM 覆盖 | 只处理图片自身 caption/footnote；不处理正文引用或图片组 |
| 5. Canonical normalize | `normalize_canonical_markdown()`，`canonical_parent_child.py:282`，在 plan builder 首先执行 | Canonical Markdown → normalized Markdown | 是，`canonical_parent_child.py:1472` | `test_figure_linker.py` 验证 linker 在 normalize 后；convergence 测试覆盖代码/表格 | 建立唯一字符坐标空间；不建立关系 |
| 6. provenance grounding | `ground_canonical_provenance()`，`canonical_document.py:769` | source nodes + normalized Markdown → 带 normalized range 的 nodes | 是，`canonical_parent_child.py:1473-1476` | `test_canonical_parent_child_integration.py::test_p25_*` | 保守定位，找不到时保留 `source_node_only`，不猜测重复文本 |
| 7. 显式 FigureLinker | `link_figure_references()`，`figure_linker.py:341` | normalized Markdown + reliable assets + provenance → `FigureLinkResult` | 是，`canonical_parent_child.py:1477-1481` | `test_figure_linker.py` | 仅明确图号引用；不映射 Chunk、不做隐式指代 |
| 8. parent/child split | `build_canonical_parent_child_plan()`，`canonical_parent_child.py:1467`；内部 `split_parent_child_result()` | normalized Markdown → parent/child drafts、精确 segment ranges | 是 | `test_canonical_parent_child_integration.py`、`test_canonical_pipeline_convergence.py` | 纯逻辑切分和 asset materialization；不创建 ORM |
| 9. Chunk ORM 构建 | `_rebuild_canonical_weknora_parent_child()`，`chunk_service.py:1060` | plan drafts → 内存 `Chunk[]` 和 artifact payload | 是，`rebuild_document_chunks()` 固定走 Canonical 路径 | `test_image_relation_resolver.py` 与 integration 测试直接调用 | ORM 适配、ID 分配；职责已偏大，不应继续加入关系推理 |
| 10. 双侧关系物化 | `_apply_figure_bindings()`，`chunk_service.py:2410` | `Chunk[]` + `FigureLinkResult` → text `figure_references`、image `image_binding` | 是，`chunk_service.py:1270` | integration 中只有单引用 happy path | 当前只物化 P4 摘要；存在 range、覆盖、去重缺陷 |
| 11. Canonical artifact 持久化 | `_replace_canonical_artifact()`，`chunk_service.py:2526`；ORM `DocumentCanonicalArtifact`，`models.py:179` | artifact payload → `document_canonical_artifacts` 当前 revision | 是，`chunk_service.py:2786-2795` 同一调用方事务 | integration 覆盖 payload、无 base64、session 不 commit | 当前 revision 账本；不是历史 ParsedBlock 仓库或人工覆盖表 |
| 12. embedding/BM25 | `embedding_service.embed_document()`，`embedding_service.py:393`；ES 索引由解析完成路径触发 | eligible Chunk → pgvector/ES 索引 | 是，但自动 embedding 受配置和任务路径影响；也可显式调用 | 本轮指定测试未覆盖 embedding；`test_retrieval_mapping.py` 只测结果映射 | 只消费 materialized chunk 内容；不推理关系 |
| 13. hybrid retrieval | `hybrid_search()`，`retrieval_service.py:1392` | query + KB → vector/BM25/RRF flat results | 是 | `test_retrieval_mapping.py` 只覆盖 `_row_to_result()` | 检索融合与上下文；文件已经过大 |
| 14. relation expansion | `_expand_figure_bindings()`，`retrieval_service.py:1304` | flat hits 中的双侧 metadata → 追加 linked Chunk | 是，`retrieval_service.py:1440` | 指定测试与仓库搜索均未发现专项 P5 测试 | 当前直接查 ORM 且无安全边界封装，应先修复并拆分 |

## 4. 当前实现问题逐项核查

建议阶段引用 `docs/document_relation_graph_phased_plan.md`。

| # | 是否存在 | 代码位置与触发条件 | 实际影响 | 建议阶段 |
|---|---|---|---|---|
| 1. overlap 让一个 reference 写入多个正文 child | 是 | `chunk_service.py:2459-2469` 遍历所有非 parent chunk；overlap 使多个 child 的 range 都包含同一 `ref_start` 时全部入选 | 一个 occurrence 被重复物化，图片反向引用与检索扩展重复 | Phase 1 |
| 2. 是否完整覆盖 `reference.start..end` | 否 | `chunk_service.py:2468` 仅检查 `start_at <= ref_start < end_at`，未检查 `ref_end <= end_at` | 横跨边界的引用可绑定到只含开头的 child；与 P3 规范不一致 | Phase 1 |
| 3. 不应参与的 Chunk 是否可能误选 | 部分存在 | candidate scan 仅排除 `parent_text`；之后 `text_by_id` 只接受 `text/table`（`chunk_service.py:2441-2451`） | image/equation/OCR candidate 会被静默跳过；table 可被当正文。结果目前较多是遗漏/隐式行为，但缺少显式 allowlist 和 source-role 校验 | Phase 1 |
| 4. 单个 `image_binding` 覆盖多关系 | 是 | `chunk_service.py:2503-2518` 每个 link 重写整个 `image_binding`，仅合并 `reference_chunk_ids` | 最后一个 link 的 figure_id/method/score 覆盖之前关系，无法表示一图多关系 | Phase 1 |
| 5. `figure_references` 去重粒度 | 不足 | `chunk_service.py:2483-2499` 仅按 `figure_id + image_chunk_id` 去重 | 同一正文 child 中两次独立引用会丢失 occurrence 的 start/end/evidence | Phase 1 |
| 6. retrieval expansion 显式 KB/doc 限制 | 否 | `retrieval_service.py:1360-1363` 只过滤 `id IN (...)` 和 enabled | 污染或篡改 metadata 可跨文档、跨知识库扩展；文档声明与代码不一致 | Phase 1，阻断后续 |
| 7. 扩展数量限制粒度 | 整批共享 | `collected_image_ids`/`collected_text_ids` 在整个 results 循环外创建，`retrieval_service.py:1321-1353` | 早期命中耗尽 3/5 配额，后续原始命中无扩展；与注释“per expansion”不一致 | Phase 1 |
| 8. SQL 顺序破坏关系顺序 | 是 | `Chunk.id.in_(all_linked_ids)` 无 `ORDER BY`，`retrieval_service.py:1360-1363` | DB 返回顺序不保证等于 metadata 中的关系顺序；可重复性和前端展示不稳定 | Phase 1 |
| 9. 扩展失败稳定回退 | 否 | `_expand_figure_bindings()` 及调用点没有 try/except | JSON 形状或 DB 查询异常可令整个 hybrid search 失败，不能回退原结果 | Phase 1 |
| 10. 关系开关与回滚路径 | 不存在 | config、`.env.example`、service 搜索均无 figure/relation flag；P4/P5 无条件执行 | 无法独立关闭解析、物化或检索扩展；只能回滚代码/镜像 | Phase 1 |
| 11. Canonical artifact 承载关系图/图片组 | 有条件适合 | `models.py:179-209` 是 document + current parse revision 的原子账本，但字段仍是 `figure_links_json` | 适合作为自动生成完整关系图的权威存储；当前字段命名/schema 不适合直接塞入长期通用模型，人工覆盖也不应随 reparse 覆盖 | Phase 2 |
| 12. page extent 是否真实页面尺寸 | 否 | `_page_extent()`，`canonical_document.py:297-309`，用同页有效 block bbox 的 min/max 推算 | 页边空白、缺块、裁剪图会改变归一化坐标；方向和组识别不稳定 | Phase 0 量化，Phase 3 修复 |
| 13. ParsedBlock/MinerU 是否已有真实页宽高 | 没有进入标准模型 | `types.py:10-31` 无 page extent；MinerU `_metadata_from_block()`，`mineru_adapter.py:982-1010` allowlist 不含 page width/height | 即使上游 payload 提供尺寸，当前标准化路径也不会可靠保留；不能把 derived extent 当真实页面坐标 | Phase 0/3 |
| 14. section/bbox/page/order 是否足够组识别 | 足够生成候选，不足以承诺确定绑定 | `CanonicalSourceNode`/`CanonicalAsset` 保留这些字段（`canonical_document.py:67-174`） | 同页规则、候选排序可做；缺真实页尺寸、列/reading-order graph、稳定 section 边界时必须保留 ambiguous | Phase 3 |
| 15. VLM description 是否进入 sidecar并可复用 | 是 | `CanonicalAsset.vlm_description` 与 `to_metadata()`，`canonical_document.py:121,159`；image child/embedding 使用 `canonical_parent_child.py:558-560,802` | 不调用新模型即可做候选文本相似度或规则特征重排；分数只能叫 heuristic/similarity score，未校准前不能叫 confidence | Phase 5 |

## 5. 能力可行性评级

| 能力 | 等级 | 可规则化部分 | 必须 ambiguous / 不可承诺部分 |
|---|---|---|---|
| 统一关系边模型 | 高 | dataclass、稳定 ID、证据、状态、版本、merge 规则 | schema 演进失败必须回退现有 figure links |
| 一张图片多关系 | 高 | edge list 天然支持多 source、多 relation type | 冲突 relation 不得覆盖，保留并标状态 |
| 同页图片组 | 中 | page、bbox、source order、caption 分区可生成 vertical/horizontal/multi-panel 候选 | 缺 bbox/真实页尺寸、复杂拼版时 ambiguous；无标注集不承诺准确率 |
| 页面公共上下文 | 中 | 顶部宽文本、同 section、覆盖多 asset x-range、距离阈值 | 独立 caption 与公共说明冲突时 ambiguous |
| 图版页识别 | 低 | 图片密度、正文比例、caption 分布可做规则候选 | 图册/论文拼版差异大；没有标注集不能承诺准确率，不进首期自动确定 |
| 上图/下图/左图/右图 | 中 | 同页 layout graph + directional cone + section 边界 | 多个等距候选、缺 bbox、跨栏时 ambiguous |
| “该图/上述图片”等指代 | 低 | 最近唯一显式 antecedent 可生成候选 | 语言指代依赖篇章语义；首期不得普遍自动绑定 |
| 跨页关系 | 低 | 明确图号仍由 FigureLinker 支持 | 隐式跨页方向/指代不可靠，不进首期 |
| 现有 VLM description 语义重排 | 高 | sidecar 已有文本；可对已生成候选做本地 embedding/词项相似度重排 | 只重排、不扩大候选；无标注集不设“准确率”承诺 |
| 页面截图 VLM 判断 | 低 | 技术上可行 | 当前无标准 page screenshot 资产，增加模型成本/隐私/可重复性风险，不进首期 |
| 检索阶段关系扩展 | 高 | 现有双向扩展骨架可复用 | 必须先完成 Phase 1 的隔离、顺序、限额、回退和 flag |
| 前端人工审核与覆盖 | 中 | artifact 图可展示，状态/候选/evidence 已有雏形 | 需要 override 持久化、并发与 reparse 继承策略；列为 Phase 6 |

规则化优先级：明确图号、同页强方向、明确“以下两图”数量、公共说明几何覆盖、section 边界。
必须保留 ambiguous：并列等分候选、缺 bbox、重复图号未消歧、跨 section、通用代词、多解析器坐标混用。

## 6. 目标架构

### 6.1 模块职责

建议新增纯逻辑包 `backend/app/services/document_relation/`：

| 模块 | 职责 | 明确禁止 |
|---|---|---|
| `models.py` | `CanonicalRelationEdge`、`CanonicalAssetGroup`、candidate/evidence dataclass；序列化与 schema version | SQLAlchemy、数据库 session、模型调用 |
| `page_layout_graph.py` | 按单一 parser coordinate space 构建 page node/edge、方向、重叠、距离与真实/推算 extent 标记 | 混合 MinerU/RAGFlow 坐标；猜测缺失 bbox |
| `asset_group_resolver.py` | 生成同页 group candidates，判断 vertical/horizontal/multi-panel/gallery/before_after | 全文引用、持久化 |
| `shared_context_resolver.py` | 公共说明对 asset/group 的候选与冲突判定 | 覆盖每张图自己的 caption |
| `implicit_reference_resolver.py` | 上/下/左/右、以下两图等有限语法；低证据保留 ambiguous | 通用代词强绑定、跨页自由推断 |
| `relation_graph_merger.py` | 合并 ImageRelation、FigureLinker、group/shared/implicit 结果；稳定排序、冲突状态与 ID | 丢弃候选证据、把 heuristic score 称 confidence |
| `materializer.py` | 纯逻辑地产生 Chunk relation summary/materialization instructions | ORM、查询、flush/commit |

保持现有边界：

- `figure_linker.py` 保留，适配为 `explicit_reference` resolver；不重写其解析规则。
- `image_relation_resolver.py` 继续只负责单图片 caption/footnote。
- `canonical_document.py` 只组装 Canonical IR 与调用关系 orchestrator，不继续堆放组规则。
- `chunk_service.py` 只调用 pure materializer 并把 instruction 写入 ORM；不继续增加推理逻辑。
- `retrieval_service.py` 调用独立 `relation_expansion_service`；自身只编排检索。
- SQLAlchemy 持久化适配器放在纯逻辑包之外，例如
  `backend/app/services/document_relation_persistence.py`；关系解析包不得 import ORM。
- RAGFlow vendor 不作为首期修改对象。MinerU 与 RAGFlow 各自建 page graph，绝不混合坐标。
- 任一 resolver 失败都返回空关系增量或 ambiguous；普通 text/table/image Chunk 仍生成并可检索。

### 6.2 数据流

```text
CanonicalDocument + grounded provenance
  ├─ ImageRelationResolver（现有，caption_of / footnote_of 输入）
  ├─ FigureLinker（现有，explicit_reference 输入）
  └─ document_relation orchestrator
       ├─ page layout graph
       ├─ asset groups
       ├─ shared contexts
       ├─ bounded implicit references
       └─ graph merger → CanonicalRelationGraph
              ├─ artifact persistence（完整 graph）
              └─ pure materializer → Chunk 摘要 instructions → ORM adapter

Hybrid retrieval → scoped relation expansion service → ordered expanded evidence
```

## 7. 统一数据模型

### 7.1 CanonicalRelationEdge

| 字段 | 类型/约束 | 说明 |
|---|---|---|
| `relation_id` | string，文档 revision 内稳定 | 由 type + source/target + occurrence fingerprint 生成 |
| `relation_type` | enum | 首期见下表 |
| `source_node_ids` | ordered string[] | 可多段正文/公共说明；不得用 Chunk ID 作为 Canonical 主键 |
| `target_asset_ids` | ordered string[] | 图片 asset targets |
| `target_group_ids` | ordered string[] | group targets |
| `status` | `bound/ambiguous/unmatched/rejected/overridden` | ambiguous 是一等状态 |
| `binding_method` | string | 例如 `figure_id_exact`、`directional_bbox_rule` |
| `binding_score` | number/null | 方法内部排序分 |
| `score_kind` | string | `heuristic_rule_score`、`cosine_similarity` 等；禁止 confidence/probability |
| `page_no` | int/null | 主要证据页 |
| `section_path` | string | Canonical section |
| `evidence` | object[] | occurrence range、bbox、cue、距离、parser coordinate space |
| `candidate_targets` | object[] | 未选/并列候选及各自分数 |
| `ambiguity_reasons` | string[] | 稳定 reason codes |
| `algorithm_version` | string | resolver/merger 版本 |

首期 `relation_type`：

- `caption_of`、`footnote_of`：来自 ImageRelationResolver；
- `explicit_reference`：来自 FigureLinker；
- `relative_reference`：有限上/下/左/右规则；
- `shared_context_of`：公共说明到 asset/group；
- `member_of_group`：asset 到 group。

示例：

```json
{
  "relation_id": "rel-explicit-node-17-image-3-0",
  "relation_type": "explicit_reference",
  "source_node_ids": ["node-000017-a1b2c3"],
  "target_asset_ids": ["image-0003-d4e5f6"],
  "target_group_ids": [],
  "status": "bound",
  "binding_method": "figure_id_page_exact",
  "binding_score": 0.82,
  "score_kind": "heuristic_rule_score",
  "page_no": 4,
  "section_path": "第二章 > 系统结构",
  "evidence": [{"start": 812, "end": 814, "cue": "如图1所示"}],
  "candidate_targets": [{"asset_id": "image-0003-d4e5f6", "rule_score": 0.82}],
  "ambiguity_reasons": [],
  "algorithm_version": "explicit-reference-v2"
}
```

### 7.2 CanonicalAssetGroup

| 字段 | 类型/约束 | 说明 |
|---|---|---|
| `group_id` | string，revision 内稳定 | 基于 ordered members + page/section fingerprint |
| `group_type` | enum | `plate_page/vertical_series/horizontal_series/multi_panel/gallery/before_after` |
| `member_asset_ids` | ordered string[] | 保持阅读/空间顺序 |
| `shared_context_node_ids` | ordered string[] | 可为空 |
| `page_start/page_end` | int/null | 首期通常相等；跨页组必须显式标记 |
| `section_path` | string | 防止跨 section 误组 |
| `grouping_method` | string | 几何/显式数量/版式规则 |
| `grouping_score` | number/null | 排序分 |
| `score_kind` | string | 禁止 confidence/probability |
| `status` | `bound/ambiguous/rejected` | candidate group 可 ambiguous |
| `evidence` | object[] | bbox、间距、alignment、source order、extent source |
| `ambiguity_reasons` | string[] | 缺 geometry、并列 partition 等 |

示例：

```json
{
  "group_id": "group-page-7-horizontal-01",
  "group_type": "horizontal_series",
  "member_asset_ids": ["image-left", "image-right"],
  "shared_context_node_ids": ["node-shared-heading"],
  "page_start": 7,
  "page_end": 7,
  "section_path": "作品展示",
  "grouping_method": "same_page_horizontal_alignment",
  "grouping_score": 0.76,
  "score_kind": "heuristic_rule_score",
  "status": "ambiguous",
  "evidence": [{"page_extent_source": "derived_content_extent"}],
  "ambiguity_reasons": ["page_extent_not_authoritative"]
}
```

### 7.3 持久化选择

自动生成的完整关系图应保存于 `document_canonical_artifacts`，而不是首期拆成完整独立边表：

- 它与 Canonical Markdown、assets、provenance 同属一个 parse revision；
- reparse/rechunk 可以在同一事务原子替换；
- candidate/evidence 是嵌套、版本化结构，JSON 比高度规范化关系表更适合早期演进。

Phase 2 建议新增明确命名的 `relation_graph_json`（additive migration），保留
`figure_links_json` 作为兼容投影，不能长期把通用 graph 隐藏在 figure 字段里。只有 Phase 6 人工覆盖
需要新增独立 override/audit 表，因为人工决定不得在自动 reparse 时被覆盖。若未来需要跨文档 SQL 分析，
可再增加可重建的 projection 表，但 projection 不是权威来源。

Chunk metadata 只物化检索所需摘要：

- `relation_summary_version`、`relation_ids`；
- ordered `outgoing_asset_chunk_ids` / `incoming_reference_chunk_ids`；
- `group_ids`、`shared_context_chunk_ids`；
- 每条摘要的 `relation_type/status/binding_method/binding_score/score_kind`；
- `canonical_parse_revision`。

不得复制完整 candidate lists、空间评分细节、所有 ambiguity evidence 或完整 group graph 到每个 Chunk。

## 8. 最终回答

1. **当前架构是否适合继续改造：**适合，Canonical/asset/provenance 是正确基础。
2. **是否需要推翻 FigureLinker：**不需要；保留为 explicit resolver，并修复下游 P4 物化。
3. **是否先修复 P4/P5：**必须。P5 隔离和失败回退属于数据边界问题。
4. **首期合理范围：**统一模型、P4/P5 修复、同页组/公共说明候选、有限方向引用、ambiguous、现有 VLM 重排。
5. **最大技术风险：**真实页面几何缺失与解析器版式质量导致组/方向候选不可校准。
6. **最大工程风险：**继续把规则塞入 `canonical_document.py`、`chunk_service.py`、`retrieval_service.py`，造成不可独立测试和回滚。
7. **是否建议现在进入编码：**仅建议进入 Phase 0；固定数据集与基线完成后才能进入 Phase 1。
8. **编码前必须准备：**18 个固定样例、四类真实页面（图册/书法/论文/产品说明书）、当前 P4/P5 baseline JSON、错误注入结果、人工 gold relation/status/evidence。

## 9. 仍未确认的问题

- 真实 MinerU 部署返回体是否含未被 allowlist 保留的 page width/height，需要用固定原始响应样本确认。
- 四类目标文档的 bbox 缺失率、坐标空间一致率和 caption role 准确率尚无量化基线。
- 人工 override 在 reparse 后按何种稳定 fingerprint 继承，需在 Phase 6 前产品决策。
- 关系扩展的最终预算应按每个原始 hit、每个 relation type 还是整次 query 分配，需在 Phase 1 选定并冻结契约。

## 10. 本轮测试执行记录

尝试执行用户指定的五个测试文件，但没有把未运行写成通过：

1. `docker compose exec -T backend python -m pytest ... -v`：失败，准确错误为
   `service "backend" is not running`。
2. `backend/.venv/Scripts/python.exe -m pytest ... -v`：失败，准确错误为
   `No module named pytest`。
3. 系统 `python` 解析到 `C:\Users\liuch\AppData\Local\Microsoft\WindowsApps\python.exe`，
   执行 `python -m pytest ...` 以退出码 1 结束且没有测试输出；系统没有 `py` launcher。

按任务约束，本轮没有启动服务、安装依赖、修改环境或调整测试。因此五个文件的实际结果均为
**未执行到测试收集阶段**，不是通过。
