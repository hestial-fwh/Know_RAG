# Elasticsearch BM25 混合检索改造说明

本次改造将原 `retrieval_service.keyword_search()` 中的 PostgreSQL `ILIKE '%query%'` 子串匹配，替换为 Elasticsearch 倒排索引 + BM25 打分。

## 新增能力

- `elasticsearch` Docker 服务，默认地址：`http://elasticsearch:9200`
- 新增 `keyword_index_service.py`：
  - 自动创建 `know_rag_chunks` 索引
  - chunk 入库后同步写入 ES
  - 文档/知识库删除时同步清理 ES
  - 检索时使用 ES BM25 返回关键词召回结果
- `retrieval_service.py` 保留原有向量检索和 RRF/weighted 融合，关键词侧改为 ES BM25。

## 字段设计

ES 索引包含：

- `content`：chunk 正文 / 图片语义文本 / 表格文本
- `title`：文档标题
- `file_name`：文件名
- `context_header`：标题路径 / 上下文标题
- `keywords`：轻量规则关键词增强字段
- `ocr_text`：OCR 文本
- `vlm_description`：图片理解描述
- `table_text`：表格文本

当前不依赖 IK 插件，使用 Elasticsearch 内置 analyzer + ngram 字段解决中文召回。后续如果部署 IK，可以重建索引并调整 analyzer。

## 启动

```bash
docker compose up -d --build
```

检查 ES：

```bash
curl http://127.0.0.1:9200
```

## 旧数据重建 ES 索引

新上传/重新解析的文档会自动写入 ES。旧数据需要手动重建：

```bash
docker exec -it know-rag-backend python scripts/rebuild_keyword_index.py --all
```

或只重建某个知识库：

```bash
docker exec -it know-rag-backend python scripts/rebuild_keyword_index.py --kb <knowledge_base_id>
```

## 配置

```env
KEYWORD_SEARCH_BACKEND=elasticsearch
ELASTICSEARCH_URL=http://elasticsearch:9200
ELASTICSEARCH_INDEX=know_rag_chunks
ELASTICSEARCH_TIMEOUT_SECONDS=10
ELASTICSEARCH_FALLBACK_TO_SQL=false
```

`ELASTICSEARCH_FALLBACK_TO_SQL=false` 表示关键词检索必须走 ES BM25，不再回退到 ILIKE。只有应急时才建议改为 `true`。
