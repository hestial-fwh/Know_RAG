# know_rag

`know_rag` 是本地优先的 RAG 知识库系统，由 Vue 3/Vite 前端、FastAPI 后端、
PostgreSQL/pgvector、Elasticsearch、MinIO 和文档解析服务组成。

> 状态：Current
> 维护日期：2026-07-16
> 文档入口：`docs/index.md`

## 核心能力

- 文档上传、解析、结构化切分、父子 Chunk 和解析质量评测。
- 本地 Ollama Embedding；管理员可显式配置 OpenAI-compatible 模型供应商。
- pgvector 向量召回与 Elasticsearch BM25，通过 RRF 融合。
- 图片、表格、公式、代码块及 OCR/VLM 结果管理。
- 文档、Chunk、向量化状态和检索调试前端。
- 只读 MCP 知识检索接口。

## 启动

准备默认 Embedding 模型：

```bash
ollama pull bge-m3
```

启动服务并执行数据库迁移：

```bash
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

默认入口：

- 前端：`http://localhost:5174`
- 后端健康检查：`http://localhost:8000/health`
- FastAPI 契约：`http://localhost:8000/docs`

首次本地启动使用 `.env` 配置管理员账号；不要提交真实密码、Token 或模型密钥。

## 质量检查

```bash
docker compose exec backend ruff check app tests
docker compose exec backend python -m pytest tests/ -q
cd frontend
npm ci
npm run check
```

## 常用配置

配置项及默认值以 `.env.example` 和 `backend/app/core/config.py` 为准。常用变量包括：

```text
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_EMBEDDING_MODEL=bge-m3
OLLAMA_EMBEDDING_DIMENSION=1024
AUTO_EMBED_AFTER_PARSE=false
HYBRID_VECTOR_WEIGHT=0.7
HYBRID_KEYWORD_WEIGHT=0.3
ELASTICSEARCH_QUERY_MAX_CHARS=256
ELASTICSEARCH_QUERY_MAX_TERMS=24
ELASTICSEARCH_NGRAM_QUERY_MAX_CHARS=64
ELASTICSEARCH_SAFE_QUERY_MAX_CHARS=96
```

BM25 短查询保留普通字段与 ngram 字段召回；长查询会在应用层压缩为有限词项并停用
ngram 字段。若 Elasticsearch 仍报告子句过多，后端只执行一次更严格的安全重试，
不需要调整集群 `max_clause_count`，也不需要重建现有索引。详见
`docs/elasticsearch_hybrid_search.md`。

远程模型供应商不是默认数据路径。启用前请阅读 `docs/security.md`，确认原始文本、图片、
OCR 结果和查询是否允许离开本机。

## 文档

从 `docs/index.md` 开始阅读。运行时架构、API、数据库、安全、部署和目录主链路均由该索引
指向；历史 Phase、修复日志和重复架构报告已移除，避免与当前实现冲突。
