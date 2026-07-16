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

## 查询安全策略

`keyword_index_service.prepare_bm25_query()` 会先清理控制字符、异常换行和重复空白。
短查询保留原始语义，普通字段与 `.ngram` 字段共同召回，但进入 ngram analyzer 的文本
最多为 `ELASTICSEARCH_NGRAM_QUERY_MAX_CHARS` 个字符，英文边界不会被切成半个单词。

超过安全阈值的中文、英文或混合查询会被确定性拆成有限的完整英文词、数字或短中文片段：

- 最多使用 `ELASTICSEARCH_QUERY_MAX_TERMS` 个词项；
- 最终文本受 `ELASTICSEARCH_QUERY_MAX_CHARS` 限制；
- 只查询普通字段，不查询任何 `.ngram` 字段；
- 查询词项会在原文范围内均匀取样，避免只保留文档开头；
- 不调用大模型，也不依赖 jieba 等额外分词库。

若 Elasticsearch 首次仍返回 `too_many_nested_clauses`，服务会识别 `root_cause`、
`caused_by`、`type` 和 `reason` 中的错误信息，只进行一次不含 ngram 的安全重试。
安全重试仍失败时，关键词单检索返回明确错误；混合检索若已完成向量召回，则保留向量结果
并通过响应消息说明关键词侧降级。连接、认证等其他 Elasticsearch 错误仍按
`ELASTICSEARCH_FALLBACK_TO_SQL` 决定是否使用应急 SQL 回退。

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
ELASTICSEARCH_QUERY_MAX_CHARS=256
ELASTICSEARCH_QUERY_MAX_TERMS=24
ELASTICSEARCH_NGRAM_QUERY_MAX_CHARS=64
ELASTICSEARCH_SAFE_QUERY_MAX_CHARS=96
ELASTICSEARCH_FALLBACK_TO_SQL=false
```

`ELASTICSEARCH_FALLBACK_TO_SQL=false` 表示关键词检索必须走 ES BM25，不再回退到 ILIKE。只有应急时才建议改为 `true`。

## 2026-07-16 长查询修复结果

- 根因：完整长查询同时进入 8 个普通字段和 8 个 ngram 字段，分析器展开后的布尔子句超过
  Elasticsearch `maxClauseCount=1310`。
- 处理：新增可单测的查询清理/分词/压缩函数、短长两档 DSL、一次安全重试和 Hybrid 分支异常隔离。
- 兼容性：未修改 Elasticsearch 集群配置、mapping、已有数据、`KeywordHit`、分数归一化、
  RRF/weighted 融合、索引写入/删除或知识库重建逻辑；无需重建索引。
- 回归入口：`backend/tests/test_keyword_index_query_safety.py`，使用 mocked HTTP 响应，
  不依赖真实 Elasticsearch。
