# Architecture

> 状态：Current
> 维护日期：2026-07-23

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
→ 展示型富文本规范化（代码围栏除外）
→ Canonical Markdown + Asset Sidecar
→ normalize 后 provenance grounding + FigureLinker
→ CanonicalRelationGraph merger（caption/footnote/explicit）
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

Canonical 构建图片 asset 前会调用 `image_relation_resolver`：过滤与 OCR 重复的纯编号 caption，
将包含多个不同图号的单图 caption 标记为 ambiguous，并在 MinerU 原生字段缺失时使用归一化
同页 bbox、水平重叠、垂直距离和阅读顺序选择唯一 caption/footnote 候选。候选不唯一或坐标
不可靠时保持图片 Chunk 可用但禁止确定绑定；同页候选会先进行多图片所有权判定，caption 与
footnote 歧义分别记录，脚注冲突不会污染可靠 caption 的图号绑定资格。

`figure_linker` 在 Canonical normalize 后、父子切分前识别“如图 1 所示 / see Fig. 1”等
显式正文引用，只链接 P1 允许确定绑定的 asset。唯一图号直接绑定；重复图号按 page、section
依次要求唯一证据，否则保留 ambiguous 候选。规则分、页/章节匹配和距离等候选证据随
`document_canonical_artifacts` 持久化，明确不冒充概率 confidence。source node 保留 page、bbox、
source order、semantic role 和 normalize 前后字符范围；chunk 继承必要的 `source_node_ids`。
chunk 的 `canonical_parse_revision` 与当前 artifact 对齐；text child 精确范围和 ORM 双侧
metadata 属于 P3/P4。

显式关系的 Phase 1 边界已收敛：P4 occurrence 只写入完整覆盖 `[start,end)` 的最小正文 child，
排除 parent/image/table/OCR/equation，并在图片侧写有序 `image_bindings[]`；旧
`image_binding` 保留稳定的 v1 单关系投影。P5 对每个原始 hit、每种关系类型分别限制 3 张图和
5 个正文 child，按 metadata 声明顺序恢复结果，只允许同 KB 且同 document 的目标，数据库或
metadata 异常时 fail-open。物化与扩展分别受两个默认关闭的环境开关控制。MinerU adapter 当前
仍没有把真实 page width/height 标准化进 `ParsedBlock`；Canonical page extent 是内容 bbox
推导值，不能当成权威页面尺寸。

Phase 2 graph 由 `DOCUMENT_RELATION_GRAPH_ENABLED` 独立控制并默认关闭。开启 graph、关闭
materialization 时只 shadow 写 artifact，不改变 Chunk 数量、正文或检索；两者同时开启时才写
`relation_ids`、`relation_summaries`、有序出入边 Chunk ID 等轻量摘要。新 artifact 使用 schema v2
和 `relation_graph_json`，继续写 `figure_links_json` 兼容投影；旧 artifact 读取时可从兼容投影构造
仅含 explicit edge 的 graph。带 graph schema 标记的空图不会回退并复活旧关系。图片组、公共上下文
和方向隐式规则仍未进入本阶段。

非 Markdown parser 输出进入 Canonical 前会移除 `<sub>/<sup>/<span>/<font>/<em>` 等展示型
HTML 标签并保留标签内文本；脚本、样式和 SVG 内容不进入检索正文。围栏代码及
`content_kind=code_block` 的 XML/配置标签保持原样。父子物化后，同一 `asset_id` 因 overlap
落入相邻 source child 时只生成一个 image child；正文仅对“来源范围重叠 + 精确后缀/前缀重叠”
产生的一句话短尾做定向合并，不启用通用短块合并。

数据库 `chunker_config` 决定父子切分是否启用，不能只通过 `.env` 判断部署实例的实际切分分支。

## 代码边界

- `api/`：鉴权后的 HTTP 路由和参数绑定。
- `services/document_parser/`：格式解析与统一 ParsedDocument。
- `services/chunker/`：Canonical 和低层切分算法。
- `services/chunking/`：草稿构建、metadata、关联和持久化辅助。
- `services/document_relation/`：无 ORM 依赖的 graph 模型、merger、legacy adapter 与纯物化逻辑。
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
