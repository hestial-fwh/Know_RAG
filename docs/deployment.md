# Deployment

## 启动

```bash
cd know_rag
docker compose up -d --build
docker compose exec backend alembic upgrade head
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
```

## 数据库

PostgreSQL 使用 `pgvector/pgvector:pg16`，Phase 4 迁移要求 pgvector 可用。

端口：

- PostgreSQL: `127.0.0.1:5434`
- Redis: `127.0.0.1:6380`

上传文件仍保存在：

```text
/data/files
```
