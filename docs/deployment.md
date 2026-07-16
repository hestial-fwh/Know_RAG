# Deployment

> 状态：Current
> 维护日期：2026-07-15

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
OLLAMA_EMBEDDING_MODEL=bge-m3
OLLAMA_EMBEDDING_DIMENSION=1024
AUTO_EMBED_AFTER_PARSE=false
HYBRID_VECTOR_WEIGHT=0.7
HYBRID_KEYWORD_WEIGHT=0.3
KEYWORD_SEARCH_BACKEND=elasticsearch
ELASTICSEARCH_URL=http://elasticsearch:9200
STORAGE_BACKEND=minio
```

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
