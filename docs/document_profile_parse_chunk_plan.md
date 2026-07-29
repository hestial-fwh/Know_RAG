# 多模态文档融合：画像、解析、切分与索引分阶段方案

> 状态：DCP-0～DCP-3、DCP-4R、DCP-0.1 PASS；DCP-4 初版保留为 FAIL 记录，下一阶段为 DCP-1.1
> 编写日期：2026-07-23  
> 最近审查：2026-07-28
> 适用范围：当前 `know_rag` 主分支  
> 本文性质：融合开发的权威路线图；“已完成”和“计划”能力必须按阶段状态区分

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
→ 页内 Layout Region 与 Visual Item 分组
→ Canonical Markdown + Asset Sidecar
→ 内容来源通道与字段级索引准入
→ ChunkPolicyResolver
→ 按 section/block 执行差异化切分
→ Canonical provenance + relation graph
→ embedding / BM25 / hybrid retrieval
```

这里的“差异化”不是让一份文档只能选择一种策略。一本画册可以同时包含目录文本页、作品视觉页和
附录表格页；最终策略必须能够在同一文档内逐页、逐 section 变化。

本方案还有两个不可退让的安全原则：

1. 页面或文档分类只影响推荐策略，不能直接删除整页、整块或全部文本。
2. `native_text`、人工 caption 和已有 VLM 描述属于独立内容通道；即使视觉 OCR 被判为低可信，
   这些可靠通道仍必须参与索引。

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

- `enable_media_crop` 已由 DCP-1 接入页面主视觉保护，但字段名仍没有准确表达其运行时语义。
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

- `ParsedBlock` 到 Chunk 尚未形成字段级内容通道契约；当前页面级
  `chunk_policy_image_ocr_mode` 会被复制到同页所有 Chunk。
- `CanonicalSourceNode.index_policy` 已存在，但尚不足以区分原生文本、parser 文本、视觉 OCR、
  caption 和 VLM 描述各自的准入状态。
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
- DCP-4 初版虽然实现了视觉 OCR 隔离开关，但按页面策略跳过整个 Chunk，向量与 BM25 的字段级
  准入尚未统一；真实回放已证明该实现不能进入灰度。

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

当前 v1 已实现单一 `primary_kind`；DCP-2.1 将其升级为多轴画像。目标纯数据对象为：

```python
@dataclass
class DocumentProfile:
    schema_version: int
    primary_kind: str
    secondary_kinds: list[str]
    layout_distribution: dict[str, float]
    semantic_hints: list[str]
    text_origin_distribution: dict[str, float]
    ocr_trust_distribution: dict[str, float]
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

`PageProfile` 是核心决策对象。页面布局、视觉语义、文本来源和 OCR 可信度必须分轴表达，不能再用
`visual_dominant / poster` 二分法同时承担四类决策：

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
    layout_kind: str
    semantic_hints: list[str]
    text_origins: list[str]
    ocr_trust_score: float | None
    uncertainty_reasons: list[str]
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

多轴字段的职责如下：

| 轴 | 示例值 | 允许影响 | 不允许影响 |
|---|---|---|---|
| `layout_kind` | `text_dominant / visual_dominant / table_dense / slide_like / mixed` | 页面边界、切分器和 parent 选择 | 直接删除文本 |
| `semantic_hints` | `calligraphy / painting / poster / diagram / photo / scanned` | 解释、候选策略和评估分桶 | 单独触发隔离 |
| `text_origin` | `native_text / parser_text / visual_ocr / caption / vlm_description` | 字段级索引准入 | 被页面类型覆盖 |
| `ocr_trust` | `trusted / auxiliary / shadow / rejected` | 仅控制 OCR 通道 | 影响 caption/VLM/native text |

VLM 不用于判断某个 block 是否为图片。图片身份仍由 parser block、PDF 图像事实、bbox、媒体解码和
`CanonicalAsset` 确定。只有在资产已经确认是图片后，系统才消费现有 VLM 描述，作为检索文本和可选
`semantic_hints`；没有 VLM 时必须安全降级，不能因此丢图或丢原生文本。

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

索引侧进一步使用字段级内容通道，首期直接保存在既有 metadata/artifact 中，不新增迁移：

```json
{
  "content_channels": {
    "native_text": ["出版社内嵌的艺术家介绍"],
    "caption": ["王羲之《兰亭序》局部"],
    "vlm_description": ["一幅纵向行书作品"],
    "visual_ocr": ["低可信的草书识别结果"]
  },
  "index_sources": ["native_text", "caption", "vlm_description"],
  "shadow_sources": ["visual_ocr"],
  "admission_version": "content-admission-v2"
}
```

### 4.4 页内区域与作品单元

页面不能直接等同于一件视觉作品。画册、书法集、图录和宣传页都可能在同一页包含多个作品，文本
也可能位于图片上、下、左、右或多个图片之间。DCP-1.1 在 Page 与 Canonical source node 之间增加
两个只读结构：

```python
@dataclass
class LayoutRegion:
    region_id: str
    page_no: int
    bbox_normalized: tuple[float, float, float, float]
    region_kind: str  # visual / text / table / separator / page_fallback
    source: str       # mineru / pdf_embedded / parser / opencv / page_render
    source_node_ids: list[str]
    evidence: list[dict]


@dataclass
class VisualItem:
    visual_item_id: str
    page_no: int
    visual_region_ids: list[str]
    asset_ids: list[str]
    text_assignments: list[dict]
    status: str       # bound / shared / ambiguous / unbound
    evidence: list[dict]
```

`text_assignments` 的关系只允许：

- `caption`：明确属于单个作品的标题或说明；
- `inscription`：作品内部或紧邻作品的题跋/OCR 候选；
- `shared_context`：页面标题、跨栏介绍或同时适用于多个作品的文字；
- `ambiguous`：候选超过一个且证据不足；
- `unbound`：当前不能安全关联。

所有 bbox 统一为页面归一化坐标 `[0, 1]`，同时保留原始坐标空间和来源。整页
`page_primary_visual` 是防止漏图的 fallback region，不默认代表一个 `VisualItem`，也不参与局部
作品分组；只有页面没有任何可用局部视觉区域时，才作为一个未分割的保守作品单元。

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

### 7.2 字段级准入，而不是整块隔离

每个内容通道独立进入以下状态：

1. `normal`：进入 embedding 和 BM25。
2. `observe`：仍进入索引，同时记录候选和评估分桶。
3. `shadow_auxiliary`：不进入普通索引，但保留在 artifact、asset metadata 和前端审计视图。
4. `rejected`：仅限明确噪声，仍保留来源证据；首期不自动使用。

视觉 OCR 进入 `shadow_auxiliary` 至少需要多项独立证据：

- 与页面主视觉或作品区域高度重叠。
- 无对应 PDF 原生文本。
- OCR 行/字布局不像连续正文。
- 与页面外印刷标题和说明属于不同区域。
- 规则在标注集上达到隔离精确率门槛。

`native_text_chars > 0` 是可靠内容存在的正向证据：至少对应的原生文本通道必须保持 `normal`，
无论本页是否存在整页主视觉。`page_primary_visual` 只表达“该图片必须保留”，不能等价为
“本页是纯视觉作品”或“本页全部文本均为 OCR”。

Canonical source node 和 text/image child 必须透传内容来源；向量和 BM25 必须消费同一个
`compose_index_content()` 结果，避免只从向量移除但仍被关键词检索召回。任何页面分类错误的最坏
结果只能是“可疑 OCR 暂时参与或退出索引”，不能是可靠文本、caption、VLM 和图片全部消失。

### 7.3 OCR 质量信号

DCP-2.1 首期只使用代码和现有 Python 依赖，不引入新 API 或高算力模型：

- 字符类别比例：CJK、字母、数字、控制符、替换字符和乱码符号；
- 重复 n-gram、重复行、字符熵、异常标点密度、单字行比例和行长分布；
- OCR 与 PDF 原生文本、caption、相邻印刷文本的规范化重合度；
- bbox 的文本面积、行方向、区域连续性以及与主视觉重叠度；
- OpenCV 连通域、行列规律、留白和印章候选等轻量视觉特征；
- 已有 VLM 描述中的书法/绘画/海报等词仅作为弱语义证据，不能单独隔离。

当前 `image_ocr_mode` 是分类结果，不能反过来作为 OCR 质量输入，否则形成循环判断。硬阈值仍可作为
可配置边界，但必须进入连续评分和不确定区间；29/30、400/401 字符或 bbox 微小扰动不得导致可靠
内容从“全部索引”跳变为“整块消失”。

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
**合成工程语料已满足；真实文档边界质量仍需在 DCP-6 灰度前校准。**

### DCP-4：OCR shadow 与索引准入

**当前状态（2026-07-28）：初版真实回放 FAIL，不得灰度。**

初版已实现页面策略透传、默认关闭开关、embedding 内容清理和测试骨架，但真实文档
`diskfcl87_fcl87_05-页面.pdf` 暴露出准入粒度错误：

- `ResolvedChunkPolicy.image_ocr_mode` 已在 DCP-3 通过 `_apply_chunk_policy_metadata()` 透传
  至同页每个 Chunk 的 `metadata_json.chunk_policy_image_ocr_mode`；
- `content_cleaning.py` 新增 `visual_ocr_quarantine` 可信跳过原因和
  `is_visual_ocr_quarantined()` 纯函数判断（检查 `chunk_policy_image_ocr_mode == "shadow_auxiliary"`）；
- embedding 三个入口在内容构建前按该页面级字段跳过整个 Chunk，并持久化
  `skip_reason=visual_ocr_quarantine`；
- 结果是 23 个含 VLM 描述的 image Chunk 和 21 个没有 OCR 来源证据的 text Chunk 被整块跳过；
  23 个 `visual-collection-v1` 页面均有候选内容但没有 embedding；
- 已持久化的可信 skip reason 会在后续重试时继续生效，仅关闭开关并重新向量化不能保证恢复。

因此 DCP-4 的原退出条件未满足。现有测试证明了旧实现的一致性，但没有证明字段级正确性；在
DCP-4R PASS 前，`DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED` 必须保持关闭。

### DCP-4R：字段级内容准入与存量恢复

**当前状态（2026-07-29）：PASS。**

交付范围：

- 建立 `content_channels / index_sources / shadow_sources` 契约；
- 取消基于页面 `image_ocr_mode` 的整 Chunk 提前跳过；
- 图片 Chunk 保留 caption、已有 VLM 描述和可靠标题，只隔离明确的 `visual_ocr` 字段；
- 视觉页原生 PDF 文本和非 OCR parser 文本保持可索引；
- embedding 与 BM25 共用同一内容合成器和准入诊断；
- 重新向量化时识别并清除旧版 `visual_ocr_quarantine` 污染标记；若无法无歧义恢复则要求重新解析，
  并在前端显示原因；
- 无 VLM、来源不明或画像不确定时 fail-open 保留可靠内容，不把未知当噪声。

前端可视化验收：

- 文档详情新增“索引准入”卡：展示按来源统计的正常、观察、shadow 数和旧标记恢复数；
- 向量状态将“规则跳过”展开到原因和内容来源，不能只显示一个总数；
- 切片详情展示内容通道、最终索引文本预览、被排除字段和理由；
- 搜索调试页用书法标题或 VLM 描述查询时，能看到命中图片、来源通道和匹配路线。

自动化验收样例：

- `visual image + VLM + bad OCR`：图片有 embedding，内容含 VLM、不含坏 OCR；
- `dominant image + native PDF text`：原生文本和图片均可索引；
- `explicit image_ocr_text + shadow`：OCR 字段不进 embedding/BM25，但 artifact 可查看；
- `scanned contract OCR` 与 `poster core text`：仍可索引；
- `stale visual_ocr_quarantine`：重新向量化清理旧标记并恢复合格内容；
- embedding、单文档批量 embedding、知识库批量 embedding 和 BM25 四个入口结果一致。

退出条件：

1. 上述自动化样例全部 PASS；
2. `diskfcl87_fcl87_05-页面.pdf` 的 23 个图片候选不再因 OCR 策略整块跳过；
3. 任意视觉页若存在 caption、VLM 或原生文本，至少有一个可索引 Chunk；
4. 前端能够逐 Chunk 解释“索引了什么、排除了什么、为什么”；
5. 关闭开关时保持当前兼容行为，开启后不产生旧式整块 skip。

实现与验收结果：

- `embedding_content_builder.py` 提供 versioned `ContentAdmissionResult`，按
  `native_text / parser_text / caption / vlm_description / image_content / table_text /
  visual_ocr` 合成最终索引内容；
- 页面级 `shadow_auxiliary` 只影响明确的 `visual_ocr` 通道，不再提前跳过整个 Chunk；
- 旧 `DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED=true` 兼容触发字段级语义，新部署可使用
  `DOCUMENT_FIELD_CONTENT_ADMISSION_ENABLED`；旧开关不再产生整块 skip；
- embedding 的单 Chunk、单文档和知识库入口统一执行准入并持久化诊断；重新向量化后同步刷新
  Elasticsearch；
- BM25 与 embedding 共享相同内容合成结果，正常 `image_ocr_text/equation` 也纳入统一判断；
- 文档向量状态返回内容来源、通道状态、旧标记恢复、残留旧标记和当前 skip reason 统计；
- 文档详情显示“索引准入”卡，切片详情显示各字段状态、预览、索引来源和 shadow 来源。

自动化结果：DCP-4R/embedding/keyword/debug 定向测试 `80 passed`，完整后端回归
`467 passed`；前端 typecheck、ESLint 和 Vitest `9 passed`。本次改动文件 Ruff 全部通过。

真实回放 `diskfcl87_fcl87_05-页面.pdf`
（document id `47e46576-83f9-4700-aa5b-62de442bacdb`）：

- 86 个总 Chunk 中 66 个属于索引类型，最终 embedding `66/66`；
- image Chunk `33/33` 有 embedding；
- 44 个旧 `visual_ocr_quarantine` 标记全部恢复，残留 0；
- 66 个 Chunk 都写入 content admission 诊断；
- 当前 revision 没有独立 shadow OCR 字段；33 个 VLM、33 个 parser text 通道正常保留；
- Elasticsearch 同步为 66 条文档索引记录。

这些结果证明 DCP-4R 的工程闭环和该真实故障恢复，不代表所有真实文档的 OCR 分类准确率。

### DCP-5：前端可观测和逐步启用（原始阶段定义）

**状态：未开始。** 前端不是最后一次性补做；DCP-4R 起每个阶段都必须同时交付本阶段的可视化
验收面。本阶段现细化为下文 DCP-5.1，负责把这些增量整合为统一控制台：

- 上传高级 profile hint，但保持单一上传接口；
- 文档详情展示自动画像、补图、内容通道、OCR、切分和索引统计；
- 页面级画像表支持按类型、风险和策略筛选，显示证据而非只显示类型名；
- 支持文档级重新解析覆盖和“仅重新向量化”，明确两者能否清除旧状态；
- 按知识库灰度启用，各阶段独立关闭并退回 Canonical 全局策略；
- 导出当前 revision 的诊断 JSON，供自动 replay 与人工截图验收使用。

退出条件：产品验收者不查询数据库即可完成“页面分类—切分策略—内容准入—检索命中”的闭环核对。

### DCP-0.1：对抗数据集与混淆对

**依赖：DCP-4R PASS。状态：PASS（2026-07-29）。**

在现有 8 类、11 场景生成器上增加边界扰动、负例和真实故障 replay，仍不依赖网络、版权文档或
临时人工收集。至少新增：

- 主视觉 + 大量原生文本的出版社画册页；
- 29/30、400/401 字符海报边界对；
- `dominant_image_ratio` 和 `parser_text_area_ratio` 阈值两侧 bbox 扰动；
- 普通论文整页插图 + 大量正文，验证 `page_primary_visual` 不等于纯视觉页；
- 楷书可靠短 OCR、草书低质量 OCR、落款极短但有意义 OCR；
- 同页图、题跋、印刷说明混合来源；
- 扫描合同、古籍正文、海报、PPT 和表格的易混淆负例；
- 无 VLM、VLM 失败、重复图片、跨页图文、旧 skip 标记恢复。
- 上下、左右、2×2、大小图混排、中心共享文本和不可分割栅格页等至少 10 个页内布局场景；
  gold 同时标注 region、Visual Item、文本归属和允许的 ambiguous 状态。

前端可视化验收：开发模式的“数据集验收”页或解析评测页展示 case、gold、actual、差异、证据和
可复现命令；每个失败项可跳转到对应页面/Chunk。

自动化出口：生成 PDF、图片素材、MinerU-shaped replay、gold、覆盖率 JSON/HTML；相同 seed 的
artifact hash、画像、策略、Chunk 边界和准入结果确定。

退出条件：所有目标能力同时有正例、负例和阈值扰动例；报告继续保持
`accuracy_claim_allowed=false`，真实准确率结论另设门禁。

实际交付：

- 数据合同由 11 页扩展为 41 页：11 页旧生产回归基线、30 页对抗观测集；
- 包含 11 个混淆组、15 类能力和 `VI-01`～`VI-10` 全部布局 gold；
- 每页生成 PDF、PNG、MinerU-shaped raw、layout、gold 和六层 replay snapshot；
- `coverage.json` 与 `coverage.html` 同时输出 case/gold/actual/diff 和复现命令；
- 解析评测插件新增 `/parse-eval/datasets/document-profile` 可视化页及只读 API；
- 数据门禁、旧生产基线和确定性测试均通过；报告保持
  `accuracy_claim_allowed=false`，布局 actual 明确显示“pending DCP-1.1”，不伪造能力。

### DCP-1.1：页内视觉区域与作品单元

**依赖：DCP-0.1 PASS。状态：未开始。**

目标是将“一页多个书法、画作或其他图片”表达为多个可追踪作品单元，并把页内文本保守地关联到
单个作品、多个作品或页面上下文。该阶段只建立区域、候选关系和证据，不执行高风险 OCR 删除，也
不依赖新 API 或新增高算力模型。

输入证据：

- MinerU/parser 已有 image、text、table block 和 bbox；
- PDF 嵌入图片 bbox、页面尺寸和原生文本区域；
- DCP-1 页面渲染及 `page_primary_visual`；
- OpenCV 的连通域、留白带、边框、投影直方图和分隔线等轻量候选；
- 已有 caption、footnote 和 VLM 描述；VLM 只提供弱语义提示，不负责检测图片区域。

确定性处理顺序：

1. 归一化 parser、PDF 和渲染坐标，生成 `LayoutRegion`；
2. 按 page、bbox overlap、媒体 hash 和来源优先级消除局部图片与整页 fallback 重复；
3. 使用留白、边框、对齐、栏结构和视觉区域间距产生候选 `VisualItem`；
4. 对文本计算包含关系、归一化边缘距离、水平/垂直对齐、跨栏范围、分隔线、阅读顺序和来源；
5. 唯一强候选标记 `caption/inscription`，多个同等级候选标记 `shared_context/ambiguous`；
6. 输出 page layout graph 和 Visual Item，不在本阶段直接改变原始内容准入；
7. DCP-3.1 再以 `VisualItem` 作为作品 parent 的候选边界，禁止整页多个作品默认合并。

必须保守降级：

- 单张栅格页面没有留白、边框或可分割结构时，保留整页 fallback，标记
  `unsegmented_page_visual`，不得伪造多个确定作品；
- 文本位于两幅图正中且空间证据相同，必须标记 `ambiguous/shared_context`，不得按阅读顺序强绑；
- 一个局部 asset 与整页 fallback 重叠时保留两者 provenance，但只有局部 asset 进入作品分组；
- OCR、VLM 或 OpenCV 失败时保留 parser/PDF 已有区域，不影响可靠文本和图片；
- 页面区域结果只能增加结构信息，不能导致已有 asset、原生文本或 Chunk 被删除。

前端可视化验收：

- `DocumentContentView` 增加“布局验收”模式，在页面图上叠加 visual/text/separator bbox；
- 每个 `VisualItem` 使用稳定编号和不同描边，选中后高亮其 asset、caption、题跋和关联 Chunk；
- 以连线和文字同时展示 `caption / inscription / shared / ambiguous / unbound`；
- 可分别开关 parser、PDF、OpenCV、整页 fallback 图层，查看候选来自哪里；
- 页面侧栏显示局部图片数、作品单元数、未绑定文本数、歧义数和重复 fallback 数；
- 不可分割页面显示“保留整页，未强制拆分”，不能显示成解析成功的多个作品。

自动化验收样例：

- `VI-01`：上下两幅作品，各有下方说明，必须得到两个 Visual Item；
- `VI-02`：左右两幅书法，中间一段跨栏文字，文字必须为 shared/ambiguous；
- `VI-03`：2×2 画册网格，每图独立 caption，必须一图一作品；
- `VI-04`：一幅大图加多幅小图，不能把小图全部并入大图；
- `VI-05`：图片上下左右均有文本，只绑定唯一强空间候选；
- `VI-06`：MinerU 返回多个局部图，同时存在整页 fallback，作品数不能重复；
- `VI-07`：MinerU 只返回整页栅格图但有明显留白分栏，生成候选区域并保留 fallback provenance；
- `VI-08`：无边界、不可分割整页图，只输出一个 `unsegmented_page_visual`；
- `VI-09`：普通论文一张大插图和大量正文，不得把正文拆成多个作品说明；
- `VI-10`：相同布局 bbox 扰动 ±1%，Visual Item 数量和确定关系不得变化。

建议测试文件：

```text
backend/tests/test_document_visual_items_dcp11.py
backend/tests/test_document_visual_item_production_flow.py
frontend/src/views/__tests__/DocumentLayoutOverlay.spec.ts
frontend/e2e/document-layout-acceptance.spec.ts
```

退出条件：

1. `VI-01`～`VI-10` 全部 PASS，重复作品单元为 0；
2. 唯一 caption 绑定准确率在合成 gold 上为 `100%`，歧义例强制误绑定为 0；
3. 普通论文和单作品页面相对 DCP-1 的 asset 数、原生文本和索引候选不减少；
4. 不可分割样例全部走保守 fallback，并提供非空 reason/evidence；
5. 前端可逐页复核区域、分组、关系状态及下游 parent 候选。

### DCP-2.1：多轴画像与 OCR 可信度

**依赖：DCP-1.1 PASS。状态：未开始。**

- 将单一页面类别拆为 `layout_kind / semantic_hints / text_origins / ocr_trust`；
- 消费 DCP-1.1 的 `visual_item_count / unsegmented / ambiguous` 证据，区分单作品、多作品和不可分割页；
- `primary_visual_count` 只影响资产保护，不再单独触发视觉隔离策略；
- 原生文本、文本面积、图像面积、连续性和质量信号进入连续评分；
- 设置 `uncertain` 区间和跨页弱平滑；相邻页面只能提供先验，不能覆盖强页面证据；
- 首期使用确定性代码、PyPDF/pdfplumber/OpenCV/numpy；可选的 scikit-learn 轻量分类器仅在有
  真实 gold、离线优于规则且仍保留可解释特征时进入后续 shadow。

前端可视化验收：文档详情展示四轴分布；页面抽屉展示各特征值、阈值、评分贡献、最终判定和
不确定原因；允许在前端并排比较同一页 v1/v2 画像，但此阶段不允许人工结果写回生产。

自动化验收：混淆对不能因单字符或 bbox 微扰发生整块准入翻转；`native_text_chars > 0` 页的原生
通道始终 normal；相同输入结果确定；所有判定具有非空 evidence。

退出条件：对抗集通过，旧固定集不退化，未知页安全降级，画像只在 shadow 模式运行一个真实回放周期。

### DCP-3.1：边界、父块和内容准入策略解耦

**依赖：DCP-2.1 PASS。状态：未开始。**

将 `ResolvedChunkPolicy` 拆为三个可组合决定：

- `BoundaryPolicy`：标题、段落、条款、页面、表格行组、代码围栏；
- `ParentPolicy`：章节、条款、页面作品、幻灯片或不建 parent；
- `ContentAdmissionPolicy`：各内容来源是否进入 embedding/BM25。

同一文档按 page/section 使用不同策略。针对视觉作品页，作品图、标题、题跋和印刷说明不得被多个
作品混入同一 parent；跨页续接必须有实际浮动内容和稳定上下文证据。普通结构化文档保持原 fallback。

前端可视化验收：文档详情显示各策略产生的 Chunk 数量和长度 P50/P95；页面/section 时间线显示
边界；切片详情显示 parent、兄弟块、来源页面和策略 reasons；异常短块、孤立块、跨页块和超长块
以告警卡展示。

自动化验收：合同条款、作品页、表格续头、PPT、代码围栏、混合文档、孤立块及多个作品混合的回归
样例；关闭 adaptive flag 时普通正文 content、range 和 parent 关系与基线完全一致。

退出条件：固定集无孤立 child、无无证据跨页、无作品跨 parent 混合；普通文本零差异；真实样本
边界报告人工 PASS。

### DCP-5.1：统一前端验收台与受控覆盖

**依赖：DCP-3.1 PASS。状态：未开始。**

在现有 `DocumentDetailView`、`DocumentContentView`、`ChunkDetailView`、`SearchDebugView` 和
解析评测入口上扩展，不新建特殊上传流程。新增只读聚合 API，建议：

```text
GET /api/v1/documents/{id}/processing-profile
GET /api/v1/documents/{id}/processing-pages?page=&kind=&risk=
GET /api/v1/documents/{id}/processing-pages/{page_no}/layout
GET /api/v1/documents/{id}/index-admission
```

API 返回当前 `parse_revision`、算法版本和数据来源；页面事实、画像、策略、Chunk 和索引状态必须
属于同一 revision。覆盖操作沿用统一重新解析入口，显式保存 `document_profile_hint`，并展示
“用户提示”和“自动判定”的差异。

首期从现有 canonical artifact、Chunk metadata 和解析统计聚合，不新增数据库迁移。只有在需要保存
人工覆盖历史或跨 revision 对比后，才另行评估专用表。

聚合接口最小契约：

```json
{
  "document_id": "doc-id",
  "parse_revision": "revision-id",
  "status": "ready",
  "algorithm_versions": {
    "layout_regions": "visual-item-v1",
    "profile": "document-profile-v2",
    "chunk_policy": "dcp3.1-v1",
    "content_admission": "content-admission-v2"
  },
  "profile_summary": {
    "layout_distribution": {},
    "semantic_hints": [],
    "text_origin_distribution": {},
    "ocr_trust_distribution": {}
  },
  "page_summary": {"total": 0, "uncertain": 0, "warning": 0},
  "visual_item_summary": {
    "items": 0,
    "shared_text": 0,
    "ambiguous_text": 0,
    "unsegmented_pages": 0
  },
  "policy_summary": [],
  "admission_summary": {
    "normal": 0,
    "observe": 0,
    "shadow_auxiliary": 0,
    "recovered_legacy_skip": 0
  },
  "warnings": []
}
```

文档详情验收面建议保持为一个可下钻的闭环：

```text
┌ 文档处理概览 ─ revision / 算法版本 / 总体状态 ┐
│ 页面画像分布 │ 内容来源与准入 │ Chunk 质量 │ 告警 │
├ 页面时间线：页码 · layout · semantic · trust · policy ┤
├ 选中页面：原图/内容预览 │ 证据与评分 │ 关联 Chunk ┤
└ 检索验证：测试查询 → text/image 命中 → 匹配来源与理由 ┘
```

颜色只表示状态，必须同时有文字：绿色“正常索引”、蓝色“观察”、橙色“辅助保留”、红色“错误/状态
不一致”、灰色“无该通道”。不能用红色表示正常的 shadow 策略，以免和处理失败混淆。

自动化验收包括 API schema、权限/知识库隔离、分页、空文档、旧 artifact 兼容、Vue 单测和
Playwright 级闭环：上传合成 PDF → 等待解析 → 查看页内 bbox/Visual Item → 查看页面画像 →
查看 Chunk 准入 → 向量化 → 检索命中图片。

退出条件：前端闭环录像/截图和自动 E2E 同时 PASS；普通用户无需阅读 metadata JSON。

### DCP-6：检索质量、灰度与回滚

**依赖：DCP-5.1 PASS。状态：未开始。**

- 以知识库为最小灰度单元，记录策略版本、前后指标和回滚点；
- 分别评估 text/image Recall@K、MRR/nDCG、OCR 隔离 precision、零结果率和索引规模；
- 搜索调试页支持基线/候选策略并排展示，标明向量、BM25、关系扩展和内容来源；
- 灰度失败关闭对应开关并重新构建该知识库索引，不回滚解析原始产物；
- 不以合成集通过宣称真实准确率，至少使用授权的本地真实样本完成校准。

建议在阶段启动时冻结以下首轮门槛，若基线数据要求调整，必须先修改本文并记录理由：

- 安全集可靠内容通道召回率 `100%`；
- OCR shadow precision `>= 98%`，允许通过 `uncertain/observe` 放弃 recall；
- 普通文本 Recall@5 相对当前基线下降不超过 `1` 个百分点；
- 视觉语义 Recall@5 相对当前基线提升至少 `10` 个百分点；
- 索引构建 P95 耗时增加不超过 `15%`，索引体积增加不超过 `25%`；
- 开关关闭并重建索引后，基线结果和状态可恢复。

退出条件：上述门槛全部达到、零可靠内容丢失、回滚演练成功，才允许默认开启。

### DCP-7：关系图恢复门禁

当前 relation graph Phase 0～3 已 PASS，Phase 4 暂缓，不废弃现有实现。满足以下条件后再继续：

1. DCP-4R 字段级准入、DCP-1.1 页内作品单元、DCP-2.1 多轴画像和 DCP-3.1 边界策略均 PASS；
2. 图片资产召回、page/section 边界和 caption/VLM 内容来源稳定；
3. 关系候选只消费已确认 asset/Chunk，不反向承担漏图、OCR 清洗或切分修复职责。

恢复后先重跑既有 relation replay，再开发隐式方向引用。前端关系图验收必须能回溯到 page、bbox、
source node、规则版本和候选证据；不能只显示一条无来源连线。

## 11. 代码改动地图

| 位置 | 计划职责 |
|---|---|
| `document_parser/page_visual_profile.py` | PDF 页面视觉事实和页面画像 |
| `document_parser/pdf_page_renderer.py` | 统一的选页渲染 |
| `document_parser/page_visual_enricher.py` | 补充页面主视觉 asset |
| 新增 `document_parser/page_layout_regions.py` | 统一页内区域坐标、来源和候选去重 |
| 新增 `document_parser/visual_item_resolver.py` | 多作品分组、文本归属和保守歧义状态 |
| `document_parser/document_profiler.py` | 汇总文档、页面、块画像 |
| `chunker/chunk_policy_resolver.py` | 画像到 `ResolvedChunkPolicy` |
| `chunker/canonical_parent_child.py` | 生产路径按 section 应用 resolved policy |
| `document_parser/canonical_document.py` | 透传块角色、质量证据和索引策略 |
| `content_cleaning.py` / 新增 content admission 模块 | 字段级通道、最终统一索引内容与诊断 |
| `embedding_service.py` / `keyword_index_service.py` | 复用同一内容合成结果，不再各自跳过整块 |
| `document_service.py` | 编排、事件和统计 |
| `DocumentUploadView.vue` | 高级 hint，不新增上传接口 |
| `ChunkerConfigView.vue` | 全局 fallback、自动策略及 resolved policy 说明 |
| `DocumentDetailView.vue` / `DocumentContentView.vue` | 画像、页面时间线、准入统计和可下钻证据 |
| `ChunkDetailView.vue` | 内容通道、最终索引文本、排除字段和策略原因 |
| `SearchDebugView.vue` | text/image 命中、匹配来源、基线/候选对比 |

不要把核心实现只放在 `chunking/rebuild_service.py`：当前生产入口最终仍由 `chunk_service` 的
Canonical 路径负责。

## 12. 测试和验收

### 12.0 每阶段统一 Definition of Done

任一阶段只有同时满足以下四层才能标记 PASS：

1. **纯函数层**：规则、评分、内容合成和边界算法单元测试。
2. **生产入口层**：从 `parse_document` 到 embedding/BM25 的真实调用链测试。
3. **前端可视层**：用户能够看到本阶段的输入证据、最终决定、影响数量和失败原因。
4. **固定 replay 层**：正例、负例、边界扰动和历史故障样例均进入覆盖率报告。

只通过合成正例、只通过后端测试、只在 metadata 中可见或只凭人工截图，均不得标记阶段 PASS。

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
11. 同页上下、左右和 2×2 多作品布局。
12. 文本位于图片上下左右、跨栏中间和作品内部。
13. 多个局部图片与整页 fallback 同时存在。
14. 单张栅格整页图可分割与不可分割的对照场景。

真实问题文档只作为本地私有 replay；可提交数据集使用自行生成、不含版权风险的模板和素材。

### 12.2 指标

- 页面主视觉召回率和普通页误补率。
- 每页 `page_primary_visual` 唯一性。
- 每页 Visual Item 数量、重复率、未绑定率和歧义强绑率。
- caption/题跋/shared context 的关系准确率。
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

### 12.4 自动化样例矩阵

| Case | 输入 | 必须可视化的结果 | 自动断言 |
|---|---|---|---|
| `MM-01` | 整页图 + 原生艺术家说明 | 图片、native text 两个 normal 通道 | 两者均有索引候选 |
| `MM-02` | 书法图 + 坏 OCR + VLM | VLM normal、OCR shadow | 图片 embedding 含 VLM 不含 OCR |
| `MM-03` | 扫描合同 | scanned body OCR normal | 条款可由向量和 BM25 命中 |
| `MM-04` | 29/30 与 400/401 字海报 | 连续评分和 uncertainty | 不发生整块索引翻转 |
| `MM-05` | 普通论文大插图 + 正文 | 主视觉保留、正文 normal | 不误判全部正文为 OCR |
| `MM-06` | 画作 + 题跋 + 印刷说明 | 三种来源与各自准入 | 不混入错误 parent |
| `MM-07` | 跨页表格 | 表头重复、行组边界 | 不跨表、不丢行 |
| `MM-08` | PPT 图文混排 | 每页 parent、标题/要点/图片 | 不跨幻灯片 |
| `MM-09` | 代码与 XML | 围栏和标签保持 | 内容及 range 与基线一致 |
| `MM-10` | 无 VLM 或 VLM 失败 | 明确 fallback 原因 | 图片仍保留，可靠文本仍索引 |
| `MM-11` | 历史 quarantine 标记 | 恢复数量与结果 | 重建后旧标记不再误跳过 |
| `MM-12` | 同一文档多种页面 | 页面策略时间线 | 各页采用独立策略 |
| `MM-13` | 一页上下/左右多个作品 | bbox、Visual Item 和文本连线 | 作品不重复、不混入同一 parent |
| `MM-14` | 文本位于多图中间 | shared/ambiguous 状态 | 不产生无证据强绑定 |
| `MM-15` | 多个局部图 + 整页 fallback | 可切换来源图层 | fallback 不重复进入作品分组 |
| `MM-16` | 不可分割整页栅格图 | unsegmented 告警 | 保留整页且不伪造局部作品 |

建议对应测试文件：

```text
backend/tests/test_dcp4r_content_admission.py
backend/tests/test_document_profile_dcp01_adversarial.py
backend/tests/test_document_visual_items_dcp11.py
backend/tests/test_document_profile_dcp21.py
backend/tests/test_document_profile_dcp31.py
backend/tests/test_document_processing_profile_api.py
frontend/src/views/__tests__/DocumentProcessingProfile.spec.ts
frontend/src/views/__tests__/ChunkAdmissionDetail.spec.ts
frontend/e2e/document-processing-acceptance.spec.ts
```

命令级验收：

```bash
docker compose exec backend python -m pytest \
  tests/test_dcp4r_content_admission.py \
  tests/test_document_profile_dcp01_adversarial.py \
  tests/test_document_visual_items_dcp11.py \
  tests/test_document_profile_dcp21.py \
  tests/test_document_profile_dcp31.py -v
docker compose exec backend ruff check app tests
cd frontend && npm run check
```

不存在的阶段测试文件在该阶段开发时创建；文档列出文件名不代表当前已经实现。

### 12.5 阶段证据包

每个阶段合并前必须生成一个可归档证据包，CI 中保留 JSON，人工验收保留 HTML/截图：

| 阶段 | 最小样例数 | 自动证据 | 前端证据 | PASS 数值条件 |
|---|---:|---|---|---|
| DCP-4R | 6 个准入场景 + 1 个真实 replay | 四索引入口决策 diff、旧标记恢复报告 | 文档准入卡、切片通道、图片检索命中 | 可靠通道丢失 0；入口不一致 0 |
| DCP-0.1 | 不少于 32 个页面场景 | PDF/replay/gold hash、覆盖率 HTML | case/gold/actual 差异页 | 每项能力均有正例、负例、扰动例 |
| DCP-1.1 | 10 个布局场景 | region/item/relation gold diff、确定性报告 | bbox 图层、作品高亮和关系连线 | 重复 item 0；歧义强绑 0；可靠内容丢失 0 |
| DCP-2.1 | DCP-0.1 全量 | v1/v2 画像 diff、确定性报告 | 四轴画像和证据抽屉 | evidence 缺失 0；安全翻转 0 |
| DCP-3.1 | DCP-0.1 全量 + 7 个边界回归 | Chunk range/parent diff | 策略时间线和边界告警 | 孤立 child 0；无证据跨页 0；普通文本差异 0 |
| DCP-5.1 | 至少 3 个端到端文档 | API contract、Vue、E2E 报告 | 上传到检索的录屏或截图 | 主路径 E2E 全部 PASS |
| DCP-6 | 固定集 + 授权真实校准集 | 检索指标、性能、回滚报告 | baseline/candidate 对比页 | 达到 DCP-6 冻结门槛 |

## 13. 开关与回滚

建议使用相互独立、默认关闭的开关：

- `DOCUMENT_PROFILE_ENABLED`
- `DOCUMENT_PAGE_VISUAL_PRESERVATION_ENABLED`
- `DOCUMENT_VISUAL_ITEM_RESOLUTION_ENABLED`
- `DOCUMENT_ADAPTIVE_CHUNK_POLICY_ENABLED`
- `DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED`
- `DOCUMENT_FIELD_CONTENT_ADMISSION_ENABLED`
- `DOCUMENT_PROFILE_V2_ENABLED`
- `DOCUMENT_ADAPTIVE_CHUNK_POLICY_V2_ENABLED`

旧 `DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED` 现在只作为字段级准入的兼容触发器，不再隔离整个
Chunk；新部署使用 `DOCUMENT_FIELD_CONTENT_ADMISSION_ENABLED`。两者关闭时 visual OCR 继续按
兼容行为进入索引。v2 开关稳定后再制定旧开关移除计划。

关闭全部新开关后，行为必须退回当前链路：

```text
现有 parser
→ Canonical Markdown
→ 全局 chunker_config
→ 当前 embedding/BM25
```

每个新产物必须带 algorithm/policy version。重新解析失败时沿用当前事务回滚语义，不能出现新
artifact 和旧 chunk 混合。

## 14. 推进顺序与阶段看板

| 顺序 | 阶段 | 当前状态 | 主要产物 | 进入下一阶段的硬门禁 |
|---:|---|---|---|---|
| 1 | DCP-4R | PASS | 字段级准入、存量恢复、首个前端准入卡 | 已满足 |
| 2 | DCP-0.1 | PASS | 41 页数据集、MinerU replay、gold、JSON/HTML 和验收页 | 已满足 |
| 3 | DCP-1.1 | 下一阶段 | 页内区域、Visual Item、文本归属和布局叠加图 | 多作品不重复、不混合、不强绑歧义 |
| 4 | DCP-2.1 | 未开始 | 多轴画像、OCR 连续评分、uncertain | shadow replay 无可靠内容丢失 |
| 5 | DCP-3.1 | 未开始 | 边界/parent/准入策略解耦 | 普通文本零差异、边界报告 PASS |
| 6 | DCP-5.1 | 未开始 | 统一前端验收台、只读聚合 API | API/UI/E2E 闭环 PASS |
| 7 | DCP-6 | 未开始 | 检索评估、知识库灰度和回滚 | 真实样本门槛与回滚演练 PASS |
| 8 | DCP-7 | 门禁等待 | 恢复 relation graph Phase 4 | 上游四项稳定门禁全部满足 |

下一工作项是 **DCP-1.1**：消费已经冻结的 `VI-01`～`VI-10` gold，开发页内区域、
Visual Item、文本归属和保守降级。DCP-0.1 报告中现有 observed diff 是下一阶段输入，不代表
真实准确率结论。
