# Deployment

> 状态：Current
> 维护日期：2026-08-05

前端镜像默认使用 `NPM_REGISTRY=https://registry.npmmirror.com`，并在构建时统一替换
`package-lock.json` 中的 registry host。若当前网络访问官方源更稳定，可在 `.env` 设置：

```text
NPM_REGISTRY=https://registry.npmjs.org
```

## 启动

```bash
cd know_rag
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

默认后端镜像安装 `requirements-dev.txt`，因此容器内可以直接运行测试和 Ruff。生产镜像可使用
`INSTALL_DEV_DEPENDENCIES=false` 构建参数仅安装运行依赖。

## 质量检查

```bash
docker compose exec backend ruff check app tests
docker compose exec backend python -m pytest tests/ -q

cd frontend
npm ci
npm run check
npm run build
```

确认 `chunk_embeddings` 表：

```bash
docker compose exec postgres psql -U know_rag -d know_rag -c "\d chunk_embeddings"
```

## Ollama

Ollama 不放入 Compose。宿主机需要先安装并拉取模型：

```bash
ollama pull bge-m3
```

容器默认通过以下地址访问宿主机 Ollama：

```text
http://host.docker.internal:11434
```

## 环境变量

```text
DEEPDOC_AUTO_DOWNLOAD=0
OLLAMA_EMBEDDING_MODEL=bge-m3
OLLAMA_EMBEDDING_DIMENSION=1024
AUTO_EMBED_AFTER_PARSE=false
HYBRID_VECTOR_WEIGHT=0.7
HYBRID_KEYWORD_WEIGHT=0.3
KEYWORD_SEARCH_BACKEND=elasticsearch
ELASTICSEARCH_URL=http://elasticsearch:9200
STORAGE_BACKEND=minio
DOCUMENT_FIELD_CONTENT_ADMISSION_ENABLED=false
DOCUMENT_VISUAL_ITEM_RESOLUTION_ENABLED=false
```

`DOCUMENT_FIELD_CONTENT_ADMISSION_ENABLED=true` 启用 DCP-4R 字段级内容准入。旧
`DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED=true` 也会兼容触发相同的字段级行为，不再跳过整个
Chunk；新部署应使用前者。

`DEEPDOC_AUTO_DOWNLOAD=0` 阻止首次启动时自动下载 DeepDoc ONNX 模型（约 1-2 GB）。
如后续需要 DeepDoc 作为 MinerU 的 fallback 解析器，改回 `1` 重启即可。

`DOCUMENT_VISUAL_ITEM_RESOLUTION_ENABLED` 是 DCP-1.1 状态标记，默认 `false`。无论开关值如何，
当前版本都会生成只读 visual layout artifact 供验收；关闭时返回 `shadow_only=true`，且不会改变
内容准入、切分或检索。进入 DCP-3.1 前不应把该开关理解为已启用作品级 parent 切分。

## 数据库

PostgreSQL 使用 `pgvector/pgvector:pg16`，迁移要求 pgvector 可用。关键词索引默认使用 Elasticsearch。

端口：

- PostgreSQL: `127.0.0.1:5434`
- Redis: `127.0.0.1:6380`

上传文件仍保存在：

```text
/data/files
```

默认 Compose 使用 MinIO 保存文件对象，并将解析缓存挂载到根目录 `cache/`。MCP 服务监听 `8010`，
统一网关监听本机 `8483`。部署到非本机网络前必须修改默认管理员密码、JWT secret 和服务 token。

项目后端不限制上传文件大小；内置 Nginx 仅对 `/api/v1/documents/upload` 使用
`client_max_body_size 0`，其他接口仍保留 100 MiB 上限。上传链路复用 FastAPI 已接收的
临时文件流计算 hash 并写入 MinIO，不再创建第二份完整临时副本。若前面还有云负载均衡、
CDN 或其他反向代理，需要同步取消其上传接口请求体大小限制。
