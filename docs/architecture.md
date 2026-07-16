# Architecture

> 状态：Current
> 维护日期：2026-07-16

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
→ Canonical Markdown + Asset Sidecar
→ Parent/Child 或 flat compatibility chunk
→ PostgreSQL chunks
→ pgvector / 可选外部向量库 + Elasticsearch BM25
→ Hybrid Retrieval / KnowledgeContextService / MCP
```

图片语义首期以 MinerU 主路径为可靠输入：MinerU adapter 将 caption、footnote、OCR、page
和 bbox 标准化到 `ParsedBlock`，随后 `vision_service.enhance_image_blocks()` 补充 VLM 描述，
Canonical 层再写入 Asset Sidecar 和 image child。RAGFlow 可作为显式辅助解析器或 MinerU
文档级 fallback，但不是 MinerU 图文绑定的运行依赖；正文引用与图片的持久化关系仍属于后续阶段。

数据库 `chunker_config` 决定父子切分是否启用，不能只通过 `.env` 判断部署实例的实际切分分支。

## 代码边界

- `api/`：鉴权后的 HTTP 路由和参数绑定。
- `services/document_parser/`：格式解析与统一 ParsedDocument。
- `services/chunker/`：Canonical 和低层切分算法。
- `services/chunking/`：草稿构建、metadata、关联和持久化辅助。
- `services/embedding/`：索引文本构建；`embedding_service.py` 负责模型调用与写入。
- `retrieval_service.py`：向量、BM25、RRF 和结果映射。
- `context/knowledge_context_service.py`：前端与 MCP 共用的上下文出口。

## 数据边界

原始文件、解析块、chunk、embedding 和索引默认保留在本地基础设施。远程模型供应商不是默认路径，
但管理员可以在模型配置中显式启用；启用后，发送给该模型的文本或图片将离开本地边界。
完整 embedding 向量不通过普通 chunk API 返回，只提供维度、范数和最多 16 维预览。

## MCP

项目已提供独立的只读 MCP 服务，通过现有 HTTP API 调用统一 Context 服务。`mcp-placeholder`
路由仍是早期兼容占位接口，不代表实际 MCP 服务状态。
