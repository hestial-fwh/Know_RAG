# Architecture

> 状态：Current
> 维护日期：2026-07-24

`know_rag` 是本地优先的 RAG 知识库构建、解析、切分、索引和检索系统。

## 服务拓扑

```text
Vue 3 / Vite (:5174)
        │ /api、/files
        ▼
FastAPI (:8000) ─────── PostgreSQL 16 / pgvector
        │               Redis
        │               Elasticsearch 8 / BM25
        │               MinIO
        ├────────────── MinerU CPU（默认）/ GPU（可选）
        ├────────────── DeepDoc / RAGFlow vendor runtime
        └────────────── Ollama（默认）或显式配置的 OpenAI-compatible provider

MCP (:8010) ──HTTP API──▶ FastAPI KnowledgeContextService
Gateway (:8483) ────────▶ FastAPI + MCP
```

## 文档主链路

```text
上传 → MinIO → ParseTask
→ MinerU / RAGFlow / Legacy fallback
→ ParsedDocument / ParsedBlock
→ PDF 只读页面事实画像；可选选页渲染与缺失 asset 补偿
→ 展示型富文本规范化（代码围栏除外）
→ Canonical Markdown + Asset Sidecar
→ normalize 后 provenance grounding + FigureLinker
→ CanonicalRelationGraph merger（caption/footnote/explicit/group/shared）
→ DCP-2 文档/页面画像与 resolved chunk policy shadow 记录
→ Parent/Child 或 flat compatibility chunk
→ 同事务写 PostgreSQL chunks + document_canonical_artifacts
→ pgvector / 可选外部向量库 + Elasticsearch BM25
→ Hybrid Retrieval / KnowledgeContextService / MCP
```

图片语义首期以 MinerU 主路径为可靠输入：MinerU adapter 将 caption、footnote、OCR、page
和 bbox 标准化到 `ParsedBlock`，随后 `vision_service.enhance_image_blocks()` 补充 VLM 描述，
Canonical 层再写入 Asset Sidecar 和 image child。RAGFlow 可作为显式辅助解析器或 MinerU
文档级 fallback，但不是 MinerU 图文绑定的运行依赖。统一关系层把 caption、footnote 和 explicit
引用合并为 versioned `CanonicalRelationGraph`；完整 graph 与 Canonical provenance 同 revision
持久化，Chunk 只保留检索所需的 compact summaries。

PDF 页面尺寸、原生文本字符数、嵌入图片数量/覆盖率和 parser 图片覆盖率始终以只读方式采集，
供 DCP-2 画像使用；该步骤不渲染页面、不注入 asset，失败时保持原解析结果。
页面主视觉保护由 `DOCUMENT_PAGE_VISUAL_PRESERVATION_ENABLED` 控制并默认关闭，同时尊重文档
上传选项 `enable_media_crop`。开启后，系统才会对存在高覆盖嵌入图像、且 parser 没有近整页
image block 的页面进行选页渲染；已有页面图按 page/bbox 覆盖率去重，普通论文小图页不补整页图。
补入的 block 标记为 `page_primary_visual`，保留权威 PDF page extent，并设置
`exclude_from_asset_group=true`，避免与普通 figure 组成错误图片组。画像或渲染失败时 fail-open
返回原 ParsedDocument，不触发 parser fallback，也不删除任何 OCR/正文。
已有整页 parser 图片也会标记为 `page_primary_visual`。`block_reflow` 对这类视觉页使用页级
section scope：只接受本页顶部、短文本形态可靠的作品标题；页面中部的 parser 标题会降级为
普通文本，没有可靠标题时生成“第 N 页内容”边界。视觉页标题不会跨页传播，普通论文和连续正文
仍保留原有跨页章节继承。父子切分开启时，只有一个 `page_primary_visual` child 的页级 section
仍保留轻量 context-only parent，避免页面主视觉在树形接口中成为孤立块；普通单子块 section
继续沿用原有压平规则。若一个未分裂的 WeKnora source child 在 Canonical 物化后展开成两个
以上 table/image/text child，则按最终子块数量补建 source parent，避免续表等多块结构因
“先判父块、后展开”而整体落入孤立区。若单个未挂载 source child 与已保留的相邻父窗口
存在至少 25%（上限 64 字符）的明确区间重叠，则归入重叠度最高的父窗口；该规则只修复
父窗口边界遗漏，不为真正独立的单子块创建内容重复的父块。
当 `page_primary_visual` 至少覆盖 3 页且占已知页面 60% 以上时，文档进入视觉页主导切分：
先按 Canonical provenance 的权威页边界分段，再在页内生成检索子块，最终将同页 text/image
统一挂到唯一的 page-scoped context parent。该策略不跨页合并作品；纯图片页的父块使用图片
检索内容，避免只保存无信息的 `asset://` 标记。普通论文和低视觉覆盖文档仍使用通用滑窗。

Canonical 构建图片 asset 前会调用 `image_relation_resolver`：过滤与 OCR 重复的纯编号 caption，
将包含多个不同图号的单图 caption 标记为 ambiguous，并在 MinerU 原生字段缺失时使用归一化
同页 bbox、水平重叠、垂直距离和阅读顺序选择唯一 caption/footnote 候选。候选不唯一或坐标
不可靠时保持图片 Chunk 可用但禁止确定绑定；同页候选会先进行多图片所有权判定，caption 与
footnote 歧义分别记录，脚注冲突不会污染可靠 caption 的图号绑定资格。

`figure_linker` 在 Canonical normalize 后、父子切分前识别“如图 1 所示 / see Fig. 1”等
显式正文引用；中文范围同时支持“图 1～图 2”和期刊常见的“详见表 1、图 1～2”写法。
它只链接 P1 允许确定绑定的 asset。唯一图号直接绑定；重复图号按 page、section
依次要求唯一证据，否则保留 ambiguous 候选。规则分、页/章节匹配和距离等候选证据随
`document_canonical_artifacts` 持久化，明确不冒充概率 confidence。source node 保留 page、bbox、
source order、semantic role 和 normalize 前后字符范围；chunk 继承必要的 `source_node_ids`。
chunk 的 `canonical_parse_revision` 与当前 artifact 对齐；text child 精确范围和 ORM 双侧
metadata 属于 P3/P4。

显式关系的 Phase 1 边界已收敛：P4 occurrence 只写入完整覆盖 `[start,end)` 的最小正文 child，
排除 parent/image/table/OCR/equation，并在图片侧写有序 `image_bindings[]`；旧
`image_binding` 保留稳定的 v1 单关系投影。P5 对每个原始 hit、每种关系类型分别限制 3 张图和
5 个正文 child，按 metadata 声明顺序恢复结果，只允许同 KB 且同 document 的目标，数据库或
metadata 异常时 fail-open。物化与扩展分别受两个默认关闭的环境开关控制。

Phase 2 graph 由 `DOCUMENT_RELATION_GRAPH_ENABLED` 独立控制并默认关闭。开启 graph、关闭
materialization 时只 shadow 写 artifact，不改变 Chunk 数量、正文或检索；两者同时开启时才写
`relation_ids`、`relation_summaries`、有序出入边 Chunk ID 等轻量摘要。新 artifact 使用 schema v2
和 `relation_graph_json`，继续写 `figure_links_json` 兼容投影；旧 artifact 读取时可从兼容投影构造
仅含 explicit edge 的 graph。带 graph schema 标记的空图不会回退并复活旧关系。

Phase 3 由 `DOCUMENT_ASSET_GROUP_ENABLED` 独立控制并默认关闭，且只有总 graph 开启时才运行。
MinerU raw `page_width/page_height` 同时有效时，adapter 将其标为 authoritative extent；否则
page layout graph 只能使用 derived/missing extent。只有同页、同 section、权威 extent、唯一的
横向/纵向 partition，并且存在同 section 的显式 shared-context 节点时才生成 bound group；
缺少组级证据、缺 bbox、derived extent 或并列 partition 保持 ambiguous，跨 section rejected。
顶部公共说明只有覆盖整个 group 且候选唯一时才能 bound；
各图已有独立 caption 时 shared context rejected。Phase 4 的上下左右、以下 N 图和通用代词
仍未实现。

非 Markdown parser 输出进入 Canonical 前会移除 `<sub>/<sup>/<span>/<font>/<em>` 等展示型
HTML 标签并保留标签内文本；脚本、样式和 SVG 内容不进入检索正文。围栏代码及
`content_kind=code_block` 的 XML/配置标签保持原样。父子物化后，同一 `asset_id` 因 overlap
落入相邻 source child 时只生成一个 image child；正文仅对“来源范围重叠 + 精确后缀/前缀重叠”
产生的一句话短尾做定向合并，不启用通用短块合并。

数据库 `chunker_config` 决定父子切分是否启用，不能只通过 `.env` 判断部署实例的实际切分分支。

DCP-2 在 Canonical provenance 已定位后生成确定性的 `DocumentProfile / PageProfile`，再按页面
解析 versioned `ResolvedChunkPolicy`。证据来自 PDF 页面事实、page primary visual、source node
类型与 semantic role，不依赖文件名或知识库名称。结果写入 canonical artifact 的 `stats_json`，
包括文档主画像、逐页画像、策略详情和计数。当前所有策略均为 `shadow_only=true`：不会改变
现有 splitter 输入、Chunk 数量、边界、索引准入，也不会向 Chunk 写入 `chunk_policy_id`。
真正按页面/section 应用策略属于 DCP-3。

## 代码边界

- `api/`：鉴权后的 HTTP 路由和参数绑定。
- `services/document_parser/`：格式解析与统一 ParsedDocument。
- `services/chunker/`：Canonical 和低层切分算法。
- `services/chunking/`：草稿构建、metadata、关联和持久化辅助。
- `services/document_relation/`：无 ORM 依赖的 graph 模型、page layout graph、group/shared resolver、
  merger、legacy adapter 与纯物化逻辑。
- `document_relation_persistence_service.py`：Canonical artifact/graph 的 ORM 持久化与 dual-read 边界。
- `document_relation_expansion_service.py`：scoped retrieval expansion 只读查询边界。
- `services/embedding/`：索引文本构建；`embedding_service.py` 负责模型调用与写入。
- `retrieval_service.py`：向量、BM25、RRF、关系扩展开关和结果映射。
- `context/knowledge_context_service.py`：前端与 MCP 共用的上下文出口。

## 数据边界

原始文件、解析块、chunk、embedding 和索引默认保留在本地基础设施。远程模型供应商不是默认路径，
但管理员可以在模型配置中显式启用；启用后，发送给该模型的文本或图片将离开本地边界。
完整 embedding 向量不通过普通 chunk API 返回，只提供维度、范数和最多 16 维预览。

## MCP

项目已提供独立的只读 MCP 服务，通过现有 HTTP API 调用统一 Context 服务。`mcp-placeholder`
路由仍是早期兼容占位接口，不代表实际 MCP 服务状态。
