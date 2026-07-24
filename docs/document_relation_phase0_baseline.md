# 文档关系 Phase 0 基线

> 状态：工程基线 PASS；真实准确率校准集独立保留  
> 基线日期：2026-07-22  
> 固定契约：`backend/tests/fixtures/document_relation_phase0/dataset.json`  
> 生成规格：`backend/tests/fixtures/document_relation_phase0/generated_corpus_spec.json`

## 当前结论

- `REL-P0-001`：18 个唯一顶层 case 均有五层结构、gold status、自动绑定许可和 evidence。
- `REL-P0-002`：生成器默认产生图册、书法、论文、产品说明书各 40 页，共 160 页；每类完整覆盖
  19 个场景，不依赖临时收集的业务文档。新增 `图1‑图2` 双目标范围引用，并对每个
  `contaminated_scope` occurrence 注入真实外部 document row。
- `REL-P0-003`：MinerU-shaped raw response 显式携带 page width/height，并通过当前 adapter replay。
  Phase 3 已关闭原 allowlist 缺口；只有两个字段同时有效时才保留 authoritative extent。
- `REL-P0-004`：7 个 P4/P5 缺陷曾以严格 `xfail` 固定；Phase 1 已将原断言全部转为普通通过
  测试，gold 未为迎合实现而改写。
- 生成语料已通过 `rebuild_document_chunks()` 和 `hybrid_search()` 生产入口基线测试。

因此 Phase 0 的**工程规则与可复现性门禁已经 PASS，可以进入 Phase 1**。合成数据不能用于发布
真实 precision/recall；少量真实验证集仍是上线校准门禁，但不再阻塞日常阶段开发。

覆盖门禁不只检查场景名称或 layer 键：它同时要求 raw/ParsedBlock 数量一致、Canonical
provenance 与 chunk 非空、gold 中的 source/target/candidate ID 均可回溯到布局，并要求所有已经
物化的 direct binding 都完成一次按声明顺序的 retrieval replay。空 replay、断裂 gold ID 或漏跑
任一 direct binding 都会令 `engineering_baseline_passed=false`。

## 默认生成语料

运行：

```bash
cd backend
python -m scripts.generate_document_relation_dataset
```

默认输出到忽略目录 `backend/.generated/document_relation_phase0/`，包括：

```text
corpus_manifest.json
assets/<document-type>/*.png
documents/*.pdf
layouts/*.json
raw/*.json
gold/*.json
snapshots/*.json
reports/coverage.json
```

图片、PDF、布局、raw response 和 gold 都由固定 seed 确定性生成，不下载字体、图片或外部文档。
只有显式传入 `--mineru-url` 时才调用 MinerU，并把原始响应保存到 `raw_live/`。

## 2026-07-22 完整报告

- 页面：160，四类各 40 页。
- 场景：19，每类场景缺口均为 0。
- 布局 block：572；bbox 564/572（98.60%），page/role/section 均为 572/572。
- gold relation：196；bound 124、ambiguous 32、rejected 24、unmatched 16。
- 五层追踪：4/4 文档完整，无 trace failure。
- gold/layout 对齐：196 个关系均可回溯，无断裂 ID；Phase 1 replay：140 个 direct binding，
  无漏跑或顺序差异；8 个跨文档污染 occurrence 全部被 scope guard 拒绝。
- 当前已实现范围的显式引用 gold 对比：68 个 occurrence 的 status 与 bound target 全部一致；
  其中 `图1‑图2` 被拆为两个有序 `explicit_range` occurrence；
  方向等尚未实现类型不计入这一门禁，也不宣称通过。
- 当前 FigureLinker baseline：bound 92、ambiguous 16；方向关系尚未实现，不计算虚假准确率。
- ParsedBlock authoritative page extent：524/572（91.61%）；缺失样例继续验证 derived/missing guard。
- Phase 3 group/shared synthetic exact-match：四类各 gold 4、predicted 4、TP 4；precision/recall
  均为 1.0。该数字只描述确定性生成语料，`phase3_accuracy_gate_applied=false`，不得作为生产准确率。

## 测试基线

```bash
docker compose build backend
docker compose run --rm --no-deps backend \
  python -m pytest \
  tests/test_document_relation_dataset_generator.py \
  tests/test_document_relation_phase0_dataset.py \
  tests/test_document_relation_phase0_entrypoints.py \
  tests/test_document_relation_phase0_known_failures.py -v
```

生成器测试验证 PDF 可读取、PNG/布局/raw/gold 完整、固定 seed 哈希一致、五层 replay 和覆盖率报告；
跨页目标必须落到下一物理页，overlap 引用必须被两个真实 child range 完整覆盖。
原 7 个严格 `xfail` 已在 Phase 1 转正；Phase 0 数据生成与 gold 门禁继续作为回归基线。

## 真实验证集边界

真实样本不再是 Phase 0 工程开发依赖。若要声明生产准确率，每类仍应保留至少 5–10 页隔离的真实
MinerU 验证集；它不能参与调阈值或修改 gold。`document_relation_phase0.py --strict-real` 继续作为
可选真实校准门禁，而非 Phase 1 的启动条件。
