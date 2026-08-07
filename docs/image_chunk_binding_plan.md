# MinerU 主链路图文绑定方案

> 状态：Current（显式图文关系 P0-P5 与通用关系图 Phase 3 已完成；运行开关默认关闭）
> 维护日期：2026-07-23
> 首期范围：MinerU 主解析链路  
> 非首期范围：RAGFlow/DeepDoc fallback 的高质量图文绑定

## 1. 结论

图文绑定是 Know_RAG 自己的 Canonical/Relation 层能力，不依赖 RAGFlow 运行链路。

当前状态：P0、P1 已完成并通过字段链路验收；低质量 caption、多图号合并及同页空间
候选问题已在 P1 处理。P2 已在 normalize 后的 Canonical Markdown 上生成正文 figure reference
到 asset 的确定、歧义或未匹配结果。P2.5 已持久化规范化 Canonical Markdown、source node
provenance、asset sidecar 和完整候选证据；chunk 继承 `source_node_ids`。引用到 text child 的
精确 range、P4 occurrence 双侧摘要与 P5 scoped expansion 均已完成；运行时双开关默认关闭。
Phase 3 已增加同页图片 group 与顶部公共说明关系：仅 authoritative page extent 的唯一横/竖
partition 可以 bound；独立 caption、跨 section、缺 bbox 和 derived extent 均由保守 guard 处理。

- MinerU 是首期可靠输入，提供 caption、footnote、OCR、VLM、page 和 bbox。
- RAGFlow 是可显式选择的辅助解析器，同时也是 MinerU 整篇解析失败时的文档级 fallback；
  本阶段只保证其现有基础 text/table/image chunk，不纳入图文绑定准确率验收。
- 可以参考 RAGFlow 的 bbox 邻近、图题识别等算法思想，但不把 RAGFlow 解析结果混入
  MinerU 的同一份文档结果。
- 关系解析失败时保留普通 Chunk，不能为了“有绑定”而错误关联。

当前 P0 只解决字段贯通和上下文污染，不包含正文“如图 1 所示”到图片 Chunk 的持久化关系；
后者由 P2-P4 完成。

## 2. 当前生产链路

```text
POST /api/v1/documents/{id}/parse
└─ document_service._execute_parse_task()
   ├─ parser_service.parse_local_document()
   │  ├─ MinerU（主路径）
   │  └─ RAGFlow（显式辅助路径或文档级 fallback）
   ├─ vision_service.enhance_ocr_blocks()/enhance_image_blocks()
   ├─ block_reflow.reflow_parsed_document()
   └─ chunk_service.rebuild_document_chunks()
      └─ _rebuild_canonical_weknora_parent_child()
         ├─ build_canonical_document()
         ├─ build_canonical_parent_child_plan()
         │  ├─ normalize_canonical_markdown()
         │  ├─ provenance grounding + FigureLinker
         │  ├─ parent/child split
         │  └─ image asset materialization
         └─ 同一事务替换 Chunk 与 document_canonical_artifacts
```

生产 RAGFlow 路径是 `ragflow_format_adapter.py`。`ragflow_deepdoc_adapter.py` 不是当前
`parser_service` 的调用目标，不应把对它的修改记为生产能力。

## 3. 能力边界

| 解析路径 | 定位 | 首期图文绑定保障 |
|---|---|---|
| MinerU | 主生产路径 | 完整支持目标 |
| RAGFlow/DeepDoc | 显式辅助解析器、MinerU 文档级 fallback | Best effort；不纳入首期验收 |
| Markdown/Office/图片 | 通用 adapter | 有标准字段时可复用，非首期准确率目标 |

禁止以下耦合：

1. MinerU 成功后再次调用 RAGFlow 补图片关系。
2. 将两套解析器的 bbox、page 或 block order 直接混合。
3. 把 RAGFlow 是否可用作为 MinerU 图文绑定的前置条件。

## 4. 标准字段

### 4.1 ParsedBlock.image metadata

```json
{
  "caption": "图 1 系统架构",
  "image_footnote": "来源：内部设计文档",
  "ocr_text": "Vue FastAPI PostgreSQL",
  "vlm_description": "系统组件连接关系图",
  "parser": "mineru",
  "source": "mineru_content_list"
}
```

`ParsedBlock.page_no` 使用一基页码，`bbox` 保留解析器坐标；坐标归一化属于 P1。

### 4.2 CanonicalAsset

```text
asset_id
caption
footnote
ocr_text
vlm_description
page_no / bbox
context_header
nearby_before / nearby_after
source_block_order
metadata
```

Canonical Markdown 只保存 `asset://` marker，图片语义保存在 asset sidecar，最终在图片
child materialize 时进入 `Chunk.content` 和 `metadata_json`。

## 5. P0：字段贯通与上下文安全

### 5.1 已实现

| 项目 | 行为 |
|---|---|
| MinerU footnote 标准化 | `image_footnote/chart_footnote/footnote` 统一写入字符串 metadata |
| Canonical footnote | 写入 `CanonicalAsset.footnote` |
| 图片 Chunk footnote | 写入“图片脚注”；若完整脚注已包含在 OCR 中则不重复 |
| 邻居页边界 | 已知 `page_no` 时遇到其他页立即停止 |
| 邻居扫描上限 | 最多检查 5 个 block |
| 标题边界 | 遇到标题 block 停止，不跨 section 找邻居 |
| caption 图片上下文 | MinerU 保留受页/标题/步数保护的前文和立即 tail；其他 parser 不回退不可靠前文 |
| tail 边界 | 在下一图片、下一 Markdown 标题或下一段落前停止 |

### 5.2 P0 明确不做

- 不实现 FigureLinker。
- 不写 `figure_references` 或 `image_binding`。
- 不修改检索扩展。
- 不修改 RAGFlow parser。
- 不增加 DB schema 或 ES mapping。
- 不承诺历史数据 rechunk；当前需要 reparse 才能重新获得 ParsedBlock/asset 字段。

### 5.3 P0 验收

1. MinerU footnote 从 content_list 进入 ParsedBlock、CanonicalAsset 和图片 Chunk。
2. footnote 与 OCR 重复时只显示一次。
3. 图片邻居不跨页、不跨标题、扫描不超过 5 个 block。
4. 有 caption 且没有可靠局部 tail 时，不添加顺序扫描上下文。
5. tail 不越过下一图片、标题或段落。
6. Canonical/父子切分既有测试保持通过。

## 6. 后续正确主链路

```text
MinerU
→ 标准 ParsedBlock
→ Block Reflow
→ ImageRelationResolver（P1）
→ CanonicalDocument
→ Normalize Canonical Markdown（唯一正式坐标空间）
→ FigureLinker（P2）
→ 持久化 Canonical provenance + evidence ledger（P2.5）
→ Parent/Child split + 精确 source_ranges（P3）
→ 内存中生成全部 Chunk ID
→ asset/range 映射并补全双侧 metadata（P4）
→ 同一事务替换旧 Chunk
→ embedding / BM25 / retrieval expansion（P5）
```

## 7. P1：ImageRelationResolver

目标是校验图片自身与 caption/footnote 的局部关系，不负责全文引用。

### 7.1 已实现：caption 质量与歧义规则

```python
@dataclass
class ImageRelation:
    caption: str = ""
    original_caption: str = ""
    footnote: str = ""
    caption_method: str = ""
    binding_score: float = 0.0
    ambiguous: bool = False
    caption_ambiguous: bool = False
    footnote_ambiguous: bool = False
    figure_ids: list[str] = field(default_factory=list)
    caption_suppressed: bool = False
    deterministic_binding_allowed: bool = False
    composite_figure: bool = False
    spatial_status: str = "not_evaluated"
    normalized_bbox: tuple[float, float, float, float] | None = None
    raw_bbox: tuple[float, float, float, float] | None = None
    normalization_page_extent: tuple[float, float, float, float] | None = None
    bbox_coordinate_space: str = ""
    selected_candidate_orders: list[int] = field(default_factory=list)
    spatial_candidates: list[dict] = field(default_factory=list)
```

已接入 `build_canonical_document()` 生产入口，结果写入 `CanonicalAsset` 和 image Chunk
metadata。当前规则：

1. caption 与 OCR 归一化后完全相同，且 caption 仅为纯数字或部件编号时：保留
   `original_caption` 供审计，清空可绑定 caption，设置
   `caption_method=suppressed_duplicate_identifier`、`binding_score=0.05`。
2. 单张图片 caption 中出现两个及以上不同图号时，区分两种情形：
   - MinerU 原生换行分隔（如 `”图3\n图4”`）→ 识别为 `composite_figure`，
     保留全部 `figure_ids`，记录 `ambiguity_reasons=[composite_figure_allowed]`，
     允许 `deterministic_binding_allowed`。
   - 单行顿号分隔（如 `”图7、图8”`）或 spatial_bbox 来源的多图号 →
     保留 caption 和 `figure_ids`，设置 `ambiguous=true`、
     `ambiguity_reasons=[multiple_figure_ids_in_single_caption]`、
     `binding_score<=0.25`，阻断 `deterministic_binding_allowed`。
3. 同一图号在 caption 中重复出现不构成 ambiguous。
4. 描述性 caption 即使与 OCR 相同也不按编号噪声清除。

### 7.2 已实现：空间关系校验

规则优先级和边界：

1. MinerU 已挂在 image block 上的原生 caption/footnote 优先，标记
   `spatial_status=parser_attached`，不会被同页候选覆盖。
2. 仅在原生字段缺失时检查同页独立 caption/footnote block；非 MinerU parser 标记
   `disabled_unreliable_parser`，不启用空间确定绑定。
3. bbox 在进入 resolver 前按当前页有效 block extent 归一化到 `[0,1]`；同时保留图片和候选
   raw bbox、`normalization_page_extent` 与 `bbox_coordinate_space` 供审计。
4. 候选必须与图片水平重叠至少 `0.35`，垂直间距不得超过图片/候选最大高度的 `1.5` 倍；
   下方候选优先于上方候选，并结合 source order 评分。
5. 空间绑定最低分为 `0.62`；最高两个候选分差小于 `0.08` 时标记
   `ambiguous_candidates`，不选择 caption/footnote。
6. 同页所有图片会共同参与候选所有权判定；同一候选被多张图片近似同分接受时，相关图片标记
   `spatial_caption_candidate_shared_between_images`，不允许重复确定占用。
7. caption 与 footnote 歧义分别写入 `caption_ambiguous`、`footnote_ambiguous`；footnote
   候选冲突不会关闭可靠原生 caption 的图号确定绑定。
8. 缺失图片 bbox、候选 bbox 不完整、双栏无水平重叠、无候选时分别降级为
   `missing_image_geometry`、`missing_candidate_geometry` 或 `no_candidate`，不做高分绑定。
9. 空间选中的 caption 继续执行 OCR 编号降噪、多图号 ambiguous 和
   `deterministic_binding_allowed` 规则；被 OCR 编号规则清除时标记
   `spatial_candidate_suppressed`。

空间结果已进入 `CanonicalAsset`、image child `metadata_json` 和生产 ORM Chunk；详细候选
评分保存在嵌套 `image_relation.spatial_candidates` 中。P1 不创建正文引用关系。

## 8. P2：FigureLinker

FigureLinker 处理正文中的“如图 1 所示”和图片 caption 的全文关系。

执行顺序必须是：

```text
build_canonical_document()
→ normalize_canonical_markdown()
→ FigureLinker(normalized_markdown, assets)
→ parent/child split
```

不能在 normalize 前计算 range；超长代码块和表格归一化可能改变后续字符偏移。

图号规范化当前覆盖：

| 输入 | figure_id |
|---|---|
| 图1 / Fig.1 / Figure 1 | `figure:1` |
| 图1-2 / Figure 1.2 | `figure:1-2` |
| 图1(a) | `figure:1:a` |
| 图一 | `figure:1` |
| 图S1 / Fig. S1 | `figure:s1` |
| 图1、图2 / Figs. 1 and 2 | 两个独立引用 |

重复图号不能仅凭 `figure_id` 强绑定。候选需结合 section、page、source order 和局部距离；
不能唯一确定时保留候选或不绑定。

P2 已实现：

- `figure_linker.extract_explicit_figure_references()` 覆盖图/Fig./Figs./Figure/Figures、
  中文数字、章节号、补充图、子图号和组合引用；
- 仅识别带“如、见、所示、可见、中、see、as shown in、refer to”等引用语义的正文，
  排除 `asset://` marker、围栏代码、内联代码和无引用语义的裸 caption；
- 只使用 P1 `deterministic_binding_allowed=true` 的 asset；唯一图号使用
  `figure_id_exact`，重复图号优先要求 page 唯一，其次才允许 section 唯一；已知页码全部冲突时
  不跨页强绑；
- 其他重复图号保持 `ambiguous` 并按 page、section、局部字符距离、source order 稳定排列候选，
  不凭最近距离强绑定；
- 每个候选记录图号相等、P1 分数、caption 方法、page/section 是否相符、source order 距离、
  字符距离和 `rule_score`；该分数明确标记为 `heuristic_rule_score`，不是概率 confidence；
- `FigureLinkResult.coordinate_space=normalized_canonical_markdown`，结果先挂在
  `CanonicalChunkPlan.figure_links`，由 P2.5 随 Canonical artifact 持久化。

## 9. P2.5：Canonical provenance 与证据模型持久化

P2.5 使用文档级当前版本表 `document_canonical_artifacts`，由迁移
`0011_canonical_provenance_p25.py` 创建。每次成功 reparse/rechunk 生成新 `parse_revision`，并保存：

- `schema_version`、`algorithm_version`、`source_format` 和 `canonical_hash`；
- 规范化后的 `canonical_markdown`；
- `provenance_json.nodes`：稳定 `node_id`、node type、source order、原文 hash、page、bbox、
  section、semantic role、asset ID，以及 normalize 前后字符范围和 resolution；
- `assets_json.items`：caption/OCR/VLM/空间关系/图号/来源 node 等 asset sidecar；不保存
  `image_base64`，原始媒体仍由既有文件/对象存储负责；
- `figure_links_json`：bound/ambiguous/unmatched 结果与所有候选证据；
- `stats_json`：节点 grounding、asset 和 link 状态统计。

原子性约束：完整 plan 与 Chunk 必须先在内存构建并通过完整性校验，随后在调用方同一数据库
事务内替换旧 embedding、旧 Chunk 和当前 Canonical artifact。任何 flush/commit 失败均由调用方
回滚，不允许出现“新 provenance + 旧 chunk”或相反状态。Canonical-empty fallback 成功时删除
旧 artifact，避免保留与新 Chunk 不一致的账本。

chunk 仅保存必要引用：image child 使用准确的 asset source node；text/table child 在 P3 前保存
`source_node_ids` 和 `source_provenance_resolution=source_child_overlap`，所有 Canonical chunk 保存
与 artifact 一致的 `canonical_parse_revision`，不把宽范围伪装成精确范围。

## 10. P3：精确 source ranges

P3 已实现。精确 source ranges 规范如下：
每个 child 在 normalized Canonical Markdown 中拥有独立字符偏移。

约束：

- 坐标单位统一为 Python 字符偏移，不称 byte offset。
- overlap 导致多个 child 覆盖引用时，优先完整覆盖引用且范围最小的 child。
- `parent_text` 是 context-only，不写入 `reference_chunk_ids`。
- 关系结果去重并保持文档顺序稳定。

## 11. P4：Chunk 关系物化

现有代码已在内存中生成全部 Chunk，再统一写数据库，因此不需要先写半成品 Chunk。

```text
Phase A：生成全部 Chunk 和 ID
Phase B：建立 asset_id → image_chunk_id、source_range → text_chunk_id
Phase C：在内存中补全双侧 metadata
Phase D：同一事务替换旧 Chunk
```

正文 Chunk：

```json
{
  "figure_references": [{
    "version": 2,
    "occurrence_id": "figure:1@120:121:node-000004",
    "figure_id": "figure:1",
    "reference_type": "explicit",
    "image_chunk_ids": ["..."]
  }]
}
```

图片 Chunk：

```json
{
  "image_bindings": [{
    "version": 2,
    "occurrence_id": "figure:1@120:121:node-000004",
    "figure_id": "figure:1",
    "binding_method": "figure_id_exact",
    "binding_score": 0.82,
    "score_kind": "heuristic_rule_score",
    "reference_chunk_ids": ["..."],
    "start": 120,
    "end": 121
  }],
  "image_binding": {
    "version": 1,
    "figure_id": "figure:1",
    "reference_chunk_ids": ["..."]
  }
}
```

`image_bindings[]` 是当前有序多 occurrence 字段；`image_binding` 只是供旧读取端使用的首条稳定
投影。正文 occurrence 只选择完整覆盖其范围的最小正文 child，禁止写入 parent、图片、表格、
OCR 或公式 child。

首期不复用 `relation_chunks` / `indirect_relation_chunks`。

## 12. P5：检索扩展

- 命中正文引用时补对应图片 Chunk。
- 命中图片时补对应正文 child。
- 限制在同知识库、同文档。
- 每个原始 hit、每种关系类型分别最多补 3 张图、5 个正文 child。
- 同时读取 `image_bindings[]` 与旧 `image_binding`，去重后恢复声明顺序。
- 关系读取或扩展失败时返回原始检索结果。
- `binding_score` 经标注集校准前不得改名为 confidence。

> **已知缺口**：P5 扩展和上下文构建器 `build_cloud_flat_chunks_payload()` 均未消费 P0-P4 产出的
> 绑定数据。上下文构建仍基于同 parent 位置猜谜（`_cloud_needs_expansion`），而非读取
> `figure_references` / `image_bindings` 做确定性扩展。改造方案见
> [retrieval_context_building_plan.md](retrieval_context_building_plan.md)。

## 13. 历史数据、开关和回滚

当前数据库已持久化规范化 Canonical Markdown、CanonicalAsset sidecar 和 source provenance，
但旧文档不会自动补齐，首次获得 P2.5 artifact 仍需 reparse。P2.5 保存的是当前 parse revision，
不是 ParsedBlock 历史版本库；基于 artifact 的通用 rechunk API 尚未开放。

以下能力仍标记为 Planned，不能当作当前运维命令：

- 图文绑定批处理脚本；
- 基于 canonical artifact 的独立 rechunk API；
- 前端人工覆盖。

当前已实现三个独立环境开关，均默认 `false`：

- `DOCUMENT_RELATION_GRAPH_ENABLED`
- `DOCUMENT_RELATION_MATERIALIZATION_ENABLED`
- `DOCUMENT_RELATION_RETRIEVAL_EXPANSION_ENABLED`

graph 开关控制 versioned graph shadow 生成；materialization 开关只控制新 Chunk 写关系摘要；
retrieval expansion 开关使 hybrid search 保持原始结果、排序与数量。旧文档
无需立即 reparse，扩展读取端继续兼容 v1 `image_binding`。

回滚原则：每个后续阶段先提供开关，再扩大生产范围；关系功能关闭时必须退化为当前普通
text/table/image chunk 检索，不影响文档基础可用性。

## 14. 测试计划

| 阶段 | 必测内容 |
|---|---|
| P0 | MinerU 字段贯通、footnote 去重、页/标题/步数/tail 边界 |
| P1 | 同页候选、双栏、不完整 bbox、无候选不绑定 |
| P2 | 图1≠图10、子图、英文图号、重复图号消歧 |
| P2.5 | migration、node grounding、页码证据、无 base64 sidecar、chunk refs、同事务 upsert |
| P3 | normalized range、overlap、图片前后 child 精确范围 |
| P4 | 双侧 metadata、原子替换、幂等和删除清理 |
| P5 | 双向扩展、跨文档隔离、数量限制、失败回落 |

每阶段必须覆盖生产入口 `rebuild_document_chunks()`，不能只测试未接入 dispatcher 的辅助模块。

### 13.1 已落地的跨阶段 Chunk 质量约束

- WeKnora overlap 将同一 `asset://` marker 带入相邻 source child 时，按文档内稳定
  `asset_id` 只物化一个 image child，并记录被折叠的 source child 序号。
- 普通解析正文、caption、OCR、VLM、脚注和图片邻近上下文在进入 Canonical/Embedding 前移除
  展示型 HTML 标签并保留内文；`script/style/noscript/svg` 内容不入向量。
- 围栏代码以及 `content_kind=code_block` 保留 XML/配置标签，避免把真实源码当作展示标签删除。
- 一句话短尾只在来源范围重叠且存在至少 16 字符精确后缀/前缀重叠时并回前块；不启用
  `merge_short_text_siblings()` 通用合并。
