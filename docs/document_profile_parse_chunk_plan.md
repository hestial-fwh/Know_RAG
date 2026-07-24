# 文档画像驱动的解析与差异化切分方案

> 状态：DCP-3 PASS，DCP-4 可开始
> 编写日期：2026-07-23  
> 适用范围：当前 `know_rag` 主分支  
> 本文性质：现状审查与后续实现方案，不代表已上线能力

## 1. 结论

Know_RAG 不应为书法、合同、论文、画册分别建立上传接口和完全独立的解析流水线。合理方案是：

1. 保持统一上传、统一 `ParsedDocument`、统一 Canonical 和统一索引出口。
2. 后端根据原始文件、页面和解析块生成可解释的结构画像。
3. 将“文档类型”作为用户提示，将“页面和块的结构证据”作为实际决策依据。
4. 解析策略按文档选择，视觉保护和正文角色按页面判断，切分策略按 section/block 选择。
5. 无法确定时保留原图和原文，不自动删除内容。

目标链路：

```text
统一上传
→ 原始文件预检
→ MinerU / RAGFlow / Native parser
→ 解析质量检查
→ 文档、页面、块三级画像
→ 页面主视觉补偿与 OCR 角色标记
→ Canonical Markdown + Asset Sidecar
→ ChunkPolicyResolver
→ 按 section/block 执行差异化切分
→ Canonical provenance + relation graph
→ embedding / BM25 / hybrid retrieval
```

这里的“差异化”不是让一份文档只能选择一种策略。一本画册可以同时包含目录文本页、作品视觉页和
附录表格页；最终策略必须能够在同一文档内逐页、逐 section 变化。

## 2. 当前已实现能力审查

### 2.1 上传与解析配置

当前已经实现统一上传接口，并在 `Document.metadata_json.parse_options` 中保存：

- `parser_engine`：`auto / mineru / ragflow / legacy`
- `mineru_profile`：`cpu / gpu`
- `enable_ocr`
- `enable_vlm`
- `enable_media_crop`

前端上传页能够提交这些字段。`parser_engine` 和 `mineru_profile` 已进入解析器选择逻辑，
OCR/VLM 开关也进入文档解析阶段。

当前缺口：

- `enable_media_crop` 已保存，但生产解析链路没有消费该配置，当前属于未闭环配置。
- 没有 `document_profile_hint`、页面视觉保护策略或 OCR 索引策略。
- 重新解析沿用上传时保存的解析配置，前端尚无文档级策略检查和覆盖入口。

### 2.2 Parser 路由

`parser_service.parse_local_document()` 当前提供：

- Markdown 原生解析。
- MinerU 主路径。
- MinerU 抛出异常时文档级回退 RAGFlow。
- 显式选择 RAGFlow。
- parser requested/actual/fallback 元数据。

当前缺口：

- MinerU 只要调用成功就被视为成功，没有解析结果质量门。
- 不会发现“PDF 每页存在主图，但 MinerU 只返回少量图片块”。
- fallback 只处理执行异常，不处理图片漏检、OCR 污染或页面覆盖不足。
- 解析器输出之间没有融合层；这避免了坐标污染，但也意味着主解析器漏掉的页面视觉内容无人补偿。

### 2.3 ParsedDocument、OCR/VLM 与 Canonical

当前已经实现：

- 文本、图片、表格等 `ParsedBlock`。
- OCR 和 VLM 图片增强。
- block reflow。
- 所有格式统一进入 Canonical Markdown。
- 图片通过 `asset://` marker 和 `CanonicalAsset` sidecar 保留。
- page、bbox、caption、footnote、OCR、VLM、邻近上下文及 provenance。
- 展示型 HTML 清理、页眉页脚噪声处理和表格 Markdown 规范化。

当前缺口：

- `ParsedBlock` 没有统一的质量角色和索引策略契约，例如
  `visual_ocr_candidate / auxiliary_only / page_primary_visual`。
- `CanonicalSourceNode` 尚不能完整透传任意块级质量证据和索引策略。
- 原始 PDF 页面主视觉缺失后，Canonical 层无法恢复不存在的 asset。

### 2.4 当前生产切分链路

生产入口 `chunk_service.rebuild_document_chunks()` 当前固定执行：

```text
ParsedDocument
→ build_canonical_document()
→ build_canonical_parent_child_plan()
→ WeKnora 两阶段切分
→ text/table/image child materialization
→ Chunk + canonical artifact 同事务替换
```

主要能力：

- 所有文件格式共享 Canonical 切分输入。
- `auto / heading / heuristic / recursive / legacy` 策略。
- 标题层级上下文。
- parent/child 可选物化。
- 代码围栏和超长表格保护。
- 图片 asset 独立物化。
- overlap 图片去重。
- 基于精确 overlap 证据的短尾修复。
- chunk provenance 和 parse revision。

需要特别注意：

- 数据库 `chunker_config` 只有一套全局配置。
- `strategy=auto` 目前只分析 Canonical 文本中的 Markdown 标题、章节标记和分隔符。
- `auto` 不知道文档是扫描合同、作品图册或表格密集文档，也不读取页面图片占比、OCR 质量等信息。
- 当前所有格式最终使用同一个 `child_size/overlap/parent_size`。
- 表格、图片和代码虽然有专项物化逻辑，但正文边界和尺寸策略仍不是画像驱动。
- `services/chunking/rebuild_service.py` 保留结构化 draft 构建能力，但生产持久化重新委托给
  `chunk_service.rebuild_document_chunks()`。后续功能必须接入 Canonical 生产入口，不能只修改
  辅助 draft 路径。

### 2.5 图文关系与检索

当前已实现：

- caption/footnote 局部关系。
- 正文显式图号引用。
- Canonical relation graph。
- relation materialization 和检索扩展开关。
- Phase 3 同页 asset group 和 shared context，默认关闭。
- pgvector、Elasticsearch BM25 和混合检索。
- embedding 与 BM25 共用部分内容清理/跳过约束。

当前缺口：

- 图关系只能处理已经进入 Canonical 的 asset，无法解决解析阶段漏图。
- 页面整图不能直接当作普通 figure group 成员，否则容易与页面内局部图片错误分组。
- 暂无针对低可信视觉 OCR 的统一降权或隔离策略。

## 3. 当前问题分类

| 问题 | 当前行为 | 正确责任层 |
|---|---|---|
| PDF 有整页作品图，MinerU 未返回图片 | 图片从后续链路永久缺失 | 解析质量检查、页面视觉补偿 |
| 书法内容被 OCR 成普通正文 | 正常进入 text chunk、embedding、BM25 | 块角色识别、索引策略 |
| 扫描合同也没有原生文本 | 容易与作品页使用相同粗粒度规则 | 页面画像、OCR 版式和连续性证据 |
| 全部文档共享切分尺寸 | 不同结构的边界质量不稳定 | ChunkPolicyResolver |
| 用户需要特殊文档入口 | 容易选错且不能处理混合文档 | 统一接口 + 可选 profile hint |
| 图片关系不完整 | 关系层无法处理不存在的 asset | 必须先补齐视觉资产 |

## 4. 目标画像模型

### 4.1 文档级画像

建议新增纯数据对象 `DocumentProfile`：

```python
@dataclass
class DocumentProfile:
    schema_version: int
    primary_kind: str
    secondary_kinds: list[str]
    page_count: int
    native_text_page_ratio: float
    visual_dominant_page_ratio: float
    scanned_page_ratio: float
    table_dense_page_ratio: float
    heading_density: float
    parser_quality_status: str
    evidence: list[dict]
```

`primary_kind` 只用于摘要和默认策略，不作为硬编码业务类型。建议枚举：

- `structured_text`
- `scanned_prose`
- `visual_collection`
- `table_dense`
- `presentation`
- `code_or_technical`
- `mixed`
- `unknown`

### 4.2 页面级画像

`PageProfile` 是核心决策对象：

```python
@dataclass
class PageProfile:
    page_no: int
    page_extent: tuple[float, float, float, float] | None
    native_text_chars: int
    dominant_image_ratio: float
    parser_text_area_ratio: float
    parser_image_area_ratio: float
    table_area_ratio: float
    ocr_layout: str
    visual_status: str
    page_kind: str
    evidence: list[dict]
```

页面类型建议包括：

- `native_text`
- `scanned_prose`
- `visual_dominant`
- `mixed_layout`
- `table_dense`
- `slide`
- `unknown`

证据必须记录来源和坐标空间。规则评分应称为 `heuristic_score`，不能称为概率 `confidence`。

### 4.3 块级角色

块级决定内容如何进入 Canonical、切分和索引：

- `body_text`
- `heading`
- `caption`
- `footnote`
- `table`
- `code_block`
- `page_primary_visual`
- `figure`
- `visual_ocr_candidate`
- `scanned_body_ocr`
- `page_noise`

建议统一携带：

```json
{
  "semantic_role": "visual_ocr_candidate",
  "index_policy": "shadow_auxiliary",
  "quality_reasons": ["overlaps_page_primary_visual", "no_native_text_match"],
  "policy_version": "document-profile-v1"
}
```

## 5. 差异化切分策略

### 5.1 决策粒度

切分配置按以下优先级解析：

```text
系统安全上限
> 文档级用户覆盖
> 页面/section 自动画像策略
> 知识库默认配置
> 当前全局 chunker_config
```

用户覆盖只能改变倾向和阈值，不能关闭数据安全约束。例如用户选择“文本优先”也不应删除已经发现的
页面主视觉。

### 5.2 推荐策略矩阵

以下数值是首轮实验默认值，不是最终固定参数，需要用 Phase 0 数据集校准。

| 结构画像 | 边界优先级 | 子块建议 | overlap | parent 策略 | 专项约束 |
|---|---|---:|---:|---|---|
| 结构化论文/手册 | 标题→段落→句子 | 350–500 字符 | 50–80 | 章节或 2500–5000 字符 | caption 不作标题；图表独立 child |
| 扫描合同/档案 | 条款→段落→页面 | 450–700 | 70–120 | 章/条款，3000–6000 | 页面图保留；连续正文 OCR 正常索引 |
| 书法/画册/古籍作品页 | 印刷标题/说明→页面 | 200–350 | 20–50 | 目录/作品说明可建 parent，纯视觉页不强建 | 主视觉独立 asset；作品 OCR 默认辅助 |
| 海报/宣传页 | 页面→文本区域 | 200–400 | 30–60 | 通常一页一个轻量 parent | 图中文字可能是核心语义，不按视觉 OCR 一律隔离 |
| 表格密集文档 | 表格→表头+行组 | 按行数和字符双限制 | 表格内不做普通 overlap | 表题/工作表为 parent | 不跨表；续块重复表头；保留行列语义 |
| PPT/演示文稿 | 幻灯片→标题/要点组 | 每页 200–500 | 低 overlap | 一页一个 parent | speaker notes、图片、要点分角色 |
| 代码/技术配置 | 标题→围栏/函数→行 | 500–900 | 40–100 | 模块/章节为 parent | 围栏原子保护；超长代码按行切，不清理 XML |
| 无结构长文本 | 段落→句子→递归分隔符 | 400–600 | 60–100 | 固定窗口 parent | 使用当前 recursive/legacy 作为可靠 fallback |
| 混合文档 | 每个 section 使用自身策略 | 按 section | 按 section | 文档父层统一、section 内差异化 | 禁止整篇只套一个 profile |

### 5.3 不同策略如何共存

不建议为每种画像复制一个 splitter。应引入 `ResolvedChunkPolicy`，复用现有底层切分器：

```python
@dataclass
class ResolvedChunkPolicy:
    boundary_strategy: str
    child_size: int
    child_overlap: int
    parent_size: int
    materialize_parent: bool
    preserve_page_boundary: bool
    table_mode: str
    image_ocr_mode: str
    reasons: list[str]
```

`ChunkPolicyResolver` 为每个 Canonical source section 生成策略：

- 普通正文继续调用现有 heading/heuristic/recursive。
- 表格继续调用表格专项切分。
- 代码继续使用围栏保护和行切分。
- 图片继续使用 asset materialization。
- 新增扫描正文的页面边界约束。
- 新增视觉页的说明文字和视觉 OCR 分离。

这样差异化发生在“策略参数和受保护结构”层，而不是形成多套不可维护的 chunk pipeline。

## 6. 页面主视觉保护

建议新增：

```text
backend/app/services/document_parser/page_visual_profile.py
backend/app/services/document_parser/pdf_page_renderer.py
backend/app/services/document_parser/page_visual_enricher.py
```

执行顺序：

```text
主解析器返回 ParsedDocument
→ 读取 PDF 页面尺寸、原生文本和嵌入图像布局
→ 与 parser block 覆盖率比较
→ 对确定漏失的页面补 page_primary_visual
→ OCR/VLM
→ reflow
→ Canonical
```

第一版只做安全补图：

- 页面存在高覆盖主图，parser 没有近似整页图片时才补。
- 只渲染选中的页面。
- 同页整图按 page、bbox 和 hash 去重。
- metadata 写入判定原因、页面 extent、坐标空间和渲染参数。
- `page_primary_visual` 设置 `exclude_from_asset_group=true`，避免参与普通多图 group。
- 不在这一阶段删除任何 MinerU 文本。

现有 `enable_media_crop` 可调整为 `preserve_page_visuals` 的兼容入口，但新字段命名应表达真实行为；
API 过渡期可同时接受两者。

## 7. OCR 角色和索引策略

### 7.1 不使用文档类型直接删除 OCR

以下规则都不够安全，不能单独触发隔离：

- 繁体字比例高。
- 单字较多。
- 语言模型认为“没有意义”。
- 页面是图片。
- 用户选择了“画册”。

扫描合同、海报和古籍正文都可能满足其中部分条件。

### 7.2 三阶段上线

1. `observe`：记录视觉 OCR 候选，不改变 Chunk。
2. `shadow_auxiliary`：保留并正常索引，但在评估报告中单独统计。
3. `auxiliary_only`：仅高精确率候选退出普通 embedding/BM25，原文仍保存在 artifact/asset metadata。

进入 `auxiliary_only` 至少需要多项独立证据：

- 与页面主视觉或作品区域高度重叠。
- 无对应 PDF 原生文本。
- OCR 行/字布局不像连续正文。
- 与页面外印刷标题和说明属于不同区域。
- 规则在标注集上达到隔离精确率门槛。

Canonical source node 和 text child 必须透传 `index_policy`；向量和 BM25 必须消费同一个最终准入函数，
避免只从向量移除但仍被关键词检索召回。

## 8. 前端和 API

### 8.1 保持统一上传接口

不新增“上传书法”“上传合同”等特殊接口。上传页默认：

```text
内容策略：自动判断（推荐）
保留页面主视觉：开启
```

高级提示可提供：

- `auto`
- `text_first`
- `visual_first`
- `scanned_document`
- `table_first`

字段应命名为 `document_profile_hint`，明确它只是提示。后端仍生成画像并记录自动判定结果。

### 8.2 切分配置页演进

当前全局配置保留为 fallback，并逐步增加：

- 知识库默认策略。
- 文档级覆盖策略。
- “自动画像策略”总开关。
- resolved policy 预览。

不建议直接给普通用户展示十几种底层 splitter 参数。高级页可以展示最终解析结果：

- 文档主画像及判定原因。
- 各页面类型分布。
- 使用了哪些 chunk policy。
- 页面主视觉补充数。
- 视觉 OCR 候选/隔离数。
- 每种策略产生的 chunk 数量和长度分布。

## 9. 持久化和兼容

首期不需要数据库迁移：

- 用户提示继续保存到 `Document.metadata_json.parse_options`。
- 文档/页面画像和 resolved policy 摘要保存到 canonical artifact 的 `stats_json`。
- 每个 chunk 的策略 ID、版本、角色和关键原因保存到 `metadata_json`。
- 完整媒体仍使用现有 MinIO 边界。

建议字段：

```json
{
  "document_profile_version": "v1",
  "document_primary_kind": "mixed",
  "chunk_policy_id": "scanned-prose-v1",
  "chunk_policy_reasons": ["page_kind=scanned_prose", "clause_structure=true"],
  "index_policy": "normal"
}
```

如果后续需要独立画像历史、人工校正或跨 revision 对比，再评估专用表；不应在算法仍处于 shadow
阶段时提前固化数据库模型。

## 10. 实施阶段

为避免与已有关系图 Phase 编号混淆，本方案使用 `DCP`（Document Classification & Policy）编号。

### DCP-0：现状基线和 replay

**当前状态（2026-07-23）：PASS。** 已实现独立的确定性生成器、gold、六层 replay、覆盖率报告和
生产切分入口测试。数据集包含 8 类文档、11 个关键页面场景，不依赖外部素材、网络或模型调用。
闭环结果如下：

- 11/11 页面完成 `source PDF facts → MinerU-shaped raw → ParsedBlock → page visual enrichment
  → Canonical artifact → Chunk` 追踪；
- 6 个页面 gold 要求保留页面主视觉，当前 parser/Canonical 仅覆盖 2 个；其余 4 个漏图作为
  DCP-1 前的确定性已知缺口固定；
- 6 个预期存在原生文本的页面均由 PDF 事实层检出；
- 2 个视觉 OCR block 要求非普通索引策略，当前实际为 0；
- 当前 chunk 中 `chunk_policy_id` 数量为 0，确认差异化切分尚未实现；
- DCP-0 初始基线确认 `enable_media_crop` 没有运行时消费者；DCP-1 已将其接入
  `parser_service`；
- 合成数据只允许工程覆盖结论，`accuracy_claim_allowed=false`。

实现入口：`backend/scripts/document_profile_dataset/` 和
`backend/scripts/generate_document_profile_dataset.py`；固定规格位于
`backend/tests/fixtures/document_profile_dcp0/generated_corpus_spec.json`。

- 将书法、扫描合同、论文、画册、海报、表格、PPT、代码文档纳入固定数据集。
- 保存 MinerU replay、PDF 页面事实和 gold 页面/块角色。
- 建立当前 parser 图片覆盖率、chunk 长度、边界和检索基线。
- 验证 `enable_media_crop` 当前未闭环的事实并固定回归测试。

退出条件：同一输入可重复生成相同画像证据和覆盖率报告。**已满足。**

### DCP-1：页面主视觉保护

**当前状态（2026-07-23）：PASS。** 已完成：

- `page_visual_profile.py` 使用 PDF 原生文本、嵌入图片 bbox/覆盖率和 parser image coverage
  生成确定性 `preserve / already_present / ambiguous / normal` 判定；
- `pdf_page_renderer.py` 只渲染被选中的一基页码，复用现有 MinIO 媒体存储；
- `page_visual_enricher.py` 注入 `page_primary_visual` block，写入 page extent、规则证据、
  `score_kind=heuristic_rule_score`、媒体哈希和 `exclude_from_asset_group`；
- parser 已有整页图片同样标记为 `page_primary_visual`；`block_reflow` 将视觉页 section
  限定为本页范围，优先采用顶部可靠短标题，降级页面中部误识别标题，无可靠标题时使用页级边界，
  不影响普通正文跨页继承；单个页面主视觉允许保留 context-only 页父块，避免树形接口产生孤立
  image child；
- `parser_service` 在 MinerU 或 RAGFlow 成功解析 PDF 后执行可选补偿；全局开关默认关闭，
  文档 `enable_media_crop=false` 可以单独关闭；
- 页面画像或渲染异常时 fail-open，不触发 parser fallback，不减少原有文本；
- Relation page layout graph 保留主视觉节点，但 asset group resolver 不将其作为普通组图成员。

DCP-0 固定语料闭环结果：gold 要求 6 页保留主视觉，原 parser 覆盖 2 页，DCP-1 注入 4 页，
最终覆盖 6/6，重复页面主视觉 0；普通论文小图页未触发整页渲染。真实选页渲染、混合文档、
Canonical asset、生产 parser 入口、开关和 fail-open 均有专项测试。

- 实现 PDF 页面画像、选页渲染、asset 注入和去重。
- 接入 `parse_local_document()` 成功结果之后。
- 默认 shadow 或由独立开关控制。
- 不改变 OCR 文本和索引。

退出条件：目标主视觉页召回达标，普通论文小图页不误生成整页 asset，文本 chunk 数不减少。
**合成工程语料已满足；真实 MinerU 准确率仍需独立校准，不允许据此宣称生产准确率。**

### DCP-2：画像与策略解析

**当前状态（2026-07-24）：PASS。** 已完成：

- `document_profiler.py` 基于 Canonical provenance、page visual facts、asset role、表格/代码/
  标题等结构证据，确定性生成 `DocumentProfile` 与逐页 `PageProfile`；
- 页面判定覆盖 `native_text / scanned_prose / visual_dominant / table_dense / slide /
  unknown`，文档摘要覆盖 `structured_text / scanned_prose / visual_collection / table_dense /
  presentation / code_or_technical / mixed / unknown`；
- `chunk_policy_resolver.py` 逐页生成 versioned `ResolvedChunkPolicy`，包含边界策略、尺寸、
  页面边界、表格模式、图片 OCR 模式和可解释 reasons；
- DCP-2 交付时策略全部以 `shadow_only=true` 写入 canonical artifact；DCP-3 仅将首批低耦合
  策略切换为 active，其余策略继续 shadow fallback；
- PDF 解析成功后无论主视觉补图开关是否开启，都会保存普通页和视觉候选页的全部只读 facts；
  补图开关只控制渲染和 asset 注入，下游生成画像时无需重新打开 PDF；
- 扫描正文、视觉 OCR 和海报策略由原生文本、主图覆盖率、parser 文本连续性及文本区域占比推导，
  DCP replay 会剥离 gold-only 角色后再验收，禁止把目标标签作为分类输入；
- `table_dense` 同时要求表格面积或表格文本占比达到阈值；标题密度按实际 heading 节点计数；
- replay 覆盖率报告新增画像、证据完整性和 shadow policy gold 对比。

DCP 固定工程语料结果：8/8 文档主画像、11/11 页面画像、11/11 resolved policy 与 gold 一致，
11/11 页面存在决策证据；混合文档逐页解析为 `native_text / scanned_prose /
visual_dominant`，相同输入重复 replay 的画像和策略完全一致。合成语料只证明工程闭环，不代表
真实文档分类准确率。

退出条件：画像 determinism、证据完整性和混合文档逐页分类通过。
**已满足。**

### DCP-3：差异化切分

**当前状态（2026-07-24）：PASS。** 已完成：

- Canonical normalize 和 provenance grounding 后生成权威页面范围；激活策略页独立运行现有
  WeKnora Parent/Child，连续 shadow 页面仍合并为原全局 fallback segment；
- 首批激活 `table-row-group-v1 / scanned-prose-v1 / visual-collection-v1 /
  poster-page-v1`；结构化正文、代码和 slide 推荐策略继续 shadow，不提前改变其边界；
- `DOCUMENT_ADAPTIVE_CHUNK_POLICY_ENABLED` 为独立默认关闭开关；关闭时所有策略只记录，
  DCP replay 显式开启以验收 active 路径；
- 扫描正文、视觉页和海报强制保持页面边界；混合文档可以在同一份 Canonical 中逐页切换；
- 表格复用现有结构保护和 row-group splitter，超长表格按行组拆分，每个续块重复表头；
- 对规范化后内容发生改写的长表格/代码节点，使用 Canonical 原始偏移映射并辅以确定性
  normalized node 定位恢复页界；混合文档中的改写页不会再丢失或并入相邻页；
- Parent 和 Child 均写入 `chunk_policy_id/version/reasons/page_no/boundary_strategy`、
  `chunk_policy_applied` 和 `active/shadow_fallback` 模式；`applied=true` 必须有实际执行的
  policy/page 证据，不能仅由 resolved policy 的 active 状态推导；artifact 写入
  `chunk_policy_execution_version=dcp3-v1` 和激活策略计数；
- 普通结构化文本对照测试确认启用 DCP-3 前后 content、start/end、parent 关系完全一致；
  三页混合长表格回归覆盖了 normalize 改写后的页界恢复。

DCP 固定工程语料结果：7/7 目标页面存在实际激活策略 Chunk，所有生成 Chunk 均携带策略 ID，
激活 Chunk 跨页数量为 0；扫描双页未混合正文，长表格续块全部重复表头。合成语料仍只证明
工程边界闭环，不代表真实文档分类或检索准确率。

退出条件：普通文本基线不退化；合同条款、表格行组和视觉页说明边界显著改善。
**合成工程语料已满足；真实文档边界质量仍需在 DCP-5 灰度前校准。**

### DCP-4：OCR shadow 与索引准入

**当前状态（2026-07-24）：进行中。** 已完成：

- `ResolvedChunkPolicy.image_ocr_mode` 已在 DCP-3 通过 `_apply_chunk_policy_metadata()` 透传
  至每个 chunk 的 `metadata_json.chunk_policy_image_ocr_mode`，无需额外 pipeline 改动；
- `content_cleaning.py` 新增 `visual_ocr_quarantine` 可信跳过原因和
  `is_visual_ocr_quarantined()` 纯函数判断（检查 `chunk_policy_image_ocr_mode == "shadow_auxiliary"`）；
- `embedding_content_builder.py`：`image` 和 `table` chunk 的嵌入内容中剥离 OCR 文本；
  `image_ocr_text` chunk 直接返回空内容；
- `embedding_service.py`：三个嵌入方法（`embed_chunks`、`embed_document`、`embed_knowledge_base`）
  在构建嵌入内容前检查隔离条件，设置 `skip_reason = "visual_ocr_quarantine"` 并跳过；
- `keyword_index_service.py`：`image_ocr_text` 已排除在 `INDEXABLE_CHUNK_TYPES` 外，
  ES 侧无需额外改动；
- `CanonicalSourceNode` 新增 `index_policy` 字段（默认 `""`），透传块级索引策略；
- 独立开关 `DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED`（默认 `false`），关闭时行为与当前完全一致；
- 仅 `visual-collection-v1` 页面的 chunk 被隔离；`scanned-prose-v1` 和 `poster-page-v1`
  的 OCR 文本保持正常索引——精确率优先。

退出条件：隔离集合优先保证高精确率，扫描合同正文和海报核心文字不得被误隔离。
**合成工程语料待验收；真实 OCR 隔离精确率仍需独立校准。**

### DCP-5：前端可观测和逐步启用

- 上传高级 profile hint。
- 文档详情展示自动画像、补图、OCR 和切分统计。
- 支持文档级重新解析覆盖。
- 按知识库灰度启用，所有阶段可独立关闭并退回当前 Canonical 全局策略。

## 11. 代码改动地图

| 位置 | 计划职责 |
|---|---|
| `document_parser/page_visual_profile.py` | PDF 页面视觉事实和页面画像 |
| `document_parser/pdf_page_renderer.py` | 统一的选页渲染 |
| `document_parser/page_visual_enricher.py` | 补充页面主视觉 asset |
| `document_parser/document_profiler.py` | 汇总文档、页面、块画像 |
| `chunker/chunk_policy_resolver.py` | 画像到 `ResolvedChunkPolicy` |
| `chunker/canonical_parent_child.py` | 生产路径按 section 应用 resolved policy |
| `document_parser/canonical_document.py` | 透传块角色、质量证据和索引策略 |
| `content_cleaning.py` | 最终统一索引准入 |
| `embedding_service.py` / `keyword_index_service.py` | 复用统一准入结果 |
| `document_service.py` | 编排、事件和统计 |
| `DocumentUploadView.vue` | 高级 hint，不新增上传接口 |
| `ChunkerConfigView.vue` | 全局 fallback、自动策略及 resolved policy 说明 |

不要把核心实现只放在 `chunking/rebuild_service.py`：当前生产入口最终仍由 `chunk_service` 的
Canonical 路径负责。

## 12. 测试和验收

### 12.1 固定样本

至少覆盖：

1. 整页书法作品加印刷标题。
2. 古籍图像页和可检索正文页混合。
3. 扫描合同及条款层级。
4. 图片上文字是核心语义的海报。
5. 普通学术 PDF 和小图。
6. 图册目录、作品页和附录表格。
7. 表头跨页表格。
8. PPT 标题、要点、speaker notes 和图片。
9. Markdown/HTML 技术文档及超长代码围栏。
10. MinerU 已返回整页图片的去重场景。

真实问题文档只作为本地私有 replay；可提交数据集使用自行生成、不含版权风险的模板和素材。

### 12.2 指标

- 页面主视觉召回率和普通页误补率。
- 每页 `page_primary_visual` 唯一性。
- 图片 URL 可用率。
- 文本覆盖率和文本丢失率。
- chunk 长度 P50/P95、超长率、非末尾短块率。
- 标题、条款、表格、代码边界完整率。
- OCR 隔离 precision；初期不以 recall 为主要目标。
- 不同 policy 的 chunk/embedding 数量和存储增量。
- 目标查询的 text/image Recall@K、MRR 或 nDCG。
- 同一 replay 的画像、policy 和 chunk ID/边界确定性。

### 12.3 必测生产入口

测试必须覆盖：

```text
document_service.parse_document
→ parser_service.parse_local_document
→ block_reflow
→ chunk_service.rebuild_document_chunks
→ canonical artifact
→ embedding/BM25 eligibility
```

只测试画像函数或辅助 builder 不足以证明功能已接入生产。

## 13. 开关与回滚

建议使用相互独立、默认关闭的开关：

- `DOCUMENT_PROFILE_ENABLED`
- `DOCUMENT_PAGE_VISUAL_PRESERVATION_ENABLED`
- `DOCUMENT_ADAPTIVE_CHUNK_POLICY_ENABLED`
- `DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED`

关闭全部新开关后，行为必须退回当前链路：

```text
现有 parser
→ Canonical Markdown
→ 全局 chunker_config
→ 当前 embedding/BM25
```

每个新产物必须带 algorithm/policy version。重新解析失败时沿用当前事务回滚语义，不能出现新
artifact 和旧 chunk 混合。

## 14. 推荐的下一步

优先实施 DCP-0 和 DCP-1，不直接开始 OCR 删除或复杂文档分类：

1. 先让 PDF 页面主视觉不再因 MinerU 漏图而丢失。
2. 使用 shadow 画像观察书法、合同、海报之间的可区分证据。
3. 再让差异化切分策略进入生产。
4. 最后启用高精确率 OCR 隔离。

这一顺序先解决不可恢复的信息丢失，再优化切分和索引噪声，风险最低，也能为后续规则提供真实数据。
