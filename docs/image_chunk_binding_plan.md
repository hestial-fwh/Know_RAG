# MinerU 主链路图文绑定方案

> 状态：Current（P0 已实现，P1-P5 规划中）  
> 维护日期：2026-07-16  
> 首期范围：MinerU 主解析链路  
> 非首期范围：RAGFlow/DeepDoc fallback 的高质量图文绑定

## 1. 结论

图文绑定是 Know_RAG 自己的 Canonical/Relation 层能力，不依赖 RAGFlow 运行链路。

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
         │  ├─ parent/child split
         │  └─ image asset materialization
         └─ 在内存中生成 Chunk，统一替换数据库旧 Chunk
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
| caption 图片上下文 | 只接受当前 source child 中图片后的立即正文，不回退顺序邻居 |
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
→ Parent/Child split + 精确 source_ranges（P3）
→ 内存中生成全部 Chunk ID
→ asset/range 映射并补全双侧 metadata（P4）
→ 同一事务替换旧 Chunk
→ embedding / BM25 / retrieval expansion（P5）
```

## 7. P1：ImageRelationResolver

目标是校验图片自身与 caption/footnote 的局部关系，不负责全文引用。

```python
@dataclass
class ImageRelation:
    caption: str = ""
    footnote: str = ""
    caption_method: str = ""
    binding_score: float = 0.0
```

规则优先级：

1. MinerU 已绑定到 image block 的原生 caption/footnote。
2. 同页 bbox 候选：水平重叠、垂直距离、阅读方向。
3. 仅有顺序、没有 page/bbox 的候选不做高分绑定。
4. 无唯一候选时不绑定。

bbox 在进入 resolver 前统一成 `[0,1]` 坐标，并保留原坐标和 coordinate space 供审计。

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

图号规范化至少覆盖：

| 输入 | figure_id |
|---|---|
| 图1 / Fig.1 / Figure 1 | `figure:1` |
| 图1-2 / Figure 1.2 | `figure:1-2` |
| 图1(a) | `figure:1:a` |

重复图号不能仅凭 `figure_id` 强绑定。候选需结合 section、page、source order 和局部距离；
不能唯一确定时保留候选或不绑定。

## 9. P3：精确 source ranges

当前 materialize 后的多个 child 共享 source child 宽范围，不能直接用于关系映射。P3 必须为
图片前正文、图片 marker、图片后正文分别生成 normalized Markdown 字符范围。

约束：

- 坐标单位统一为 Python 字符偏移，不称 byte offset。
- overlap 导致多个 child 覆盖引用时，优先完整覆盖引用且范围最小的 child。
- `parent_text` 是 context-only，不写入 `reference_chunk_ids`。
- 关系结果去重并保持文档顺序稳定。

## 10. P4：Chunk 关系物化

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
    "version": 1,
    "figure_id": "figure:1",
    "reference_type": "explicit",
    "image_chunk_ids": ["..."]
  }]
}
```

图片 Chunk：

```json
{
  "image_binding": {
    "version": 1,
    "figure_id": "figure:1",
    "binding_method": "figure_id_exact",
    "binding_score": 0.98,
    "reference_chunk_ids": ["..."]
  }
}
```

首期不复用 `relation_chunks` / `indirect_relation_chunks`。

## 11. P5：检索扩展

- 命中正文引用时补对应图片 Chunk。
- 命中图片时补对应正文 child。
- 限制在同知识库、同文档。
- 默认最多补 3 张图、5 个正文 child。
- 关系读取或扩展失败时返回原始检索结果。
- `binding_score` 经标注集校准前不得改名为 confidence。

## 12. 历史数据、开关和回滚

当前数据库不持久化 ParsedBlock、Canonical Markdown 和 CanonicalAsset sidecar，因此 P0-P4 的
历史文档更新统一使用 reparse。只有未来持久化版本化 canonical artifacts 后才能支持通用
rechunk。

以下能力在代码落地前均标记为 Planned，不能当作当前运维命令：

- 图文绑定批处理脚本；
- ImageRelation/FigureLinker/检索扩展开关；
- canonical artifacts 持久化和 rechunk；
- 前端人工覆盖。

回滚原则：每个后续阶段先提供开关，再扩大生产范围；关系功能关闭时必须退化为当前普通
text/table/image chunk 检索，不影响文档基础可用性。

## 13. 测试计划

| 阶段 | 必测内容 |
|---|---|
| P0 | MinerU 字段贯通、footnote 去重、页/标题/步数/tail 边界 |
| P1 | 同页候选、双栏、不完整 bbox、无候选不绑定 |
| P2 | 图1≠图10、子图、英文图号、重复图号消歧 |
| P3 | normalized range、overlap、图片前后 child 精确范围 |
| P4 | 双侧 metadata、原子替换、幂等和删除清理 |
| P5 | 双向扩展、跨文档隔离、数量限制、失败回落 |

每阶段必须覆盖生产入口 `rebuild_document_chunks()`，不能只测试未接入 dispatcher 的辅助模块。
