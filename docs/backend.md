# Backend

后端使用 FastAPI、SQLAlchemy 2.x、Alembic、PostgreSQL、pgvector 和本地 Ollama。

## Phase 4 已实现

### embedding_service

- `embed_text(text)`
- `embed_chunks(chunk_ids)`
- `embed_document(document_id)`
- `embed_knowledge_base(kb_id)`
- 文档/知识库 embedding 状态查询。

行为：

- 优先调用 Ollama `/api/embed`。
- 兼容旧 `/api/embeddings`。
- 校验 embedding 维度。
- 跳过 disabled chunk 和空 chunk。
- 同一 chunk 已有 embedding 时覆盖更新。
- audit_logs 不记录向量内容。
- 文档向量化接口：`POST /api/v1/documents/{id}/embedding`。
- 文档向量状态接口：`GET /api/v1/documents/{id}/embedding/status`。
- 知识库批量向量化接口：`POST /api/v1/knowledge-bases/{kb_id}/embedding`。
- 知识库向量状态接口：`GET /api/v1/knowledge-bases/{kb_id}/embedding/status`。

向量化过程会记录简短日志：document_id / knowledge_base_id、total_chunks、embedded_chunks、model、dimension。日志不会记录完整 chunk 内容或 embedding 向量。

### ollama_service

- Ollama 状态检查。
- 模型列表检查。
- Embedding 测试。
- Ollama 不可用或模型未下载时返回中文错误。
- 请求超时为 120 秒。

### retrieval_service

- 向量检索：pgvector cosine distance。
- 关键词检索：ILIKE 简化实现。
- hybrid search：按配置权重合并分数。

## 未实现

- knowledge-chat 真实 LLM 回答。
- 会话和消息持久化。
- FAQ embedding。
- MCP Gateway。
- Web Search、Agent、外部 SaaS 数据源。

## PDF 文本清洗

PostgreSQL `TEXT` 字段不能保存 NUL，也就是 `0x00` 字符。PDF 解析结果有时会夹带 NUL 或其他不可见控制字符，系统会在以下位置自动清洗：

- parser 返回文本前。
- 文本切片前。
- 每个 chunk 入库前。
- document service 调用 parser 后。

清洗函数位于 `backend/app/utils/text.py`：

- 删除 `\x00`。
- 删除大部分不可见控制字符。
- 保留中文、英文、数字、标点、数学符号。
- 保留换行 `\n` 和制表符 `\t`。
- 规范化 Windows 换行。

如果 PDF 是扫描件或图片型 PDF，当前基础解析可能无法抽取文本，会返回：“未能从 PDF 中解析出文本内容，可能是扫描件或图片型 PDF”。

## 手动验证文本清洗

可在 backend 容器内执行：

```bash
python -c "from app.utils.text import sanitize_text_for_postgres as s; assert s('abc\x00def') == 'abcdef'; assert '中文' in s('中文\x00abc'); assert s('a\r\nb') == 'a\nb'; print('ok')"
```

chunk 切分空内容验证：

```bash
python -c "from app.services.chunk_service import split_text; assert split_text('\x00  \r\n') == []; print('ok')"
```

chunk 入库验证：

1. 上传包含异常控制字符的 PDF 或 txt。
2. 调用解析接口。
3. 查看 `GET /api/v1/documents/{doc_id}/chunks`。
4. 确认返回了 chunk，且 content/content_preview 不包含 `\x00`。
 
## WeKnora 对齐 Phase A

后端已补充 WeKnora-compatible 数据模型字段，并在服务层写入兼容值：

- 文档创建默认 `source=file`、`channel=local`、`enable_status=enabled`。
- chunk 创建默认 `knowledge_id=document_id`、`chunk_type=text`、`context_header=''`。
- 当前简单字符切分会写入 `start_at/end_at`，并回写 flat chunk 的 `pre_chunk_id/next_chunk_id`。
- chunk 写入 `content_hash=sha256(content)`。
- embedding upsert 时写入 `knowledge_id`、`source_id=chunk_id`、`source_type=chunk`、`content=chunk.content`、`is_enabled=chunk.is_enabled`。

本阶段不改变 embedding 内容生成逻辑，不改变 hybrid-search 融合策略，不实现结构感知 chunker、parent-child chunking、RAG 问答或 MCP Gateway。
 
## WeKnora 对齐 Phase B：结构感知 chunker

后端新增 `backend/app/services/chunker/`，实现 Python 版结构感知 chunker：

- `auto`：默认策略，有 Markdown 标题时走 heading-aware，否则走 recursive，异常时 fallback。
- `heading`：识别 `#` 到 `######`，生成 `heading_path`，并保存到 `context_header` 与 `metadata_json.heading_path`。
- `recursive`：按 separators 递归/优先断点切分，再按 chunk_size 合并，支持 overlap。
- `legacy`：保留简单 fallback 语义，但仍输出 `ParsedChunk`。

protected patterns 当前保护 fenced code block、Markdown 表格、Markdown 图片、Markdown 链接、LaTeX block、`table/pre/code` HTML block。保护目标是避免在常规切分点中间切坏这些结构；超大保护块仍可能按最大窗口截断。

parent-child chunking 已实现但默认关闭。开启 `ENABLE_PARENT_CHILD_CHUNKING=true` 后会生成 `parent_text` 和 `text` 两类 chunk；embedding 仍只处理 `chunk_type='text'` 的切片。

所有格式现在统一经过 Canonical Markdown 切分链路；`enable_parent_child` 仅控制是否持久化父上下文，不再切换到另一套按 block 独立切分实现。切片元数据中的 `coordinate_space=normalized_canonical_markdown` 表示 `start_at/end_at` 属于规范化 Markdown 坐标，不等同于原文件字节或页内坐标。

重解析会先在内存中完整构建并校验新切片，非空后才在数据库事务内替换旧切片。解析器失败、空切分或切片入库失败不会清空最后一版可用切片。关键词索引在数据库提交成功后执行“先写入新 chunk id、再清理旧 chunk id”的同步，失败会记录 `keyword_index_status=pending_retry`。

不支持格式的附件占位不会进入切片、关键词索引或向量索引，文档状态为 `parsed_with_fallback`；支持格式的解析器运行异常则标记为失败。Native Markdown 原文通过独立的 `source_content` 字段贯穿 parse/reflow/canonical 链路。

重新解析文档时会删除该文档旧 chunks 和旧 `chunk_embeddings`，再生成新 chunks。若 `AUTO_EMBED_AFTER_PARSE=true`，会在新 chunks 生成后重新向量化。
## WeKnora 对齐 Phase C

`embedding_service.build_embedding_content(chunk, document)` 负责构造实际索引文本，规则为：

1. 优先使用 `document.title`，为空时使用 `document.file_name`。
2. 如果 `chunk.context_header` 非空，加入上下文标题。
3. 最后加入 `chunk.content`。
4. 统一经过 `sanitize_text_for_postgres` 清洗。
5. 默认最多保留 `EMBEDDING_CONTENT_MAX_CHARS=6000` 字符，超长时保留标题和上下文，截断正文。

生成 embedding 时，`chunk_embeddings.content` 保存实际索引文本，`source_id=chunk.id`，`source_type=chunk`，`knowledge_id=chunk.knowledge_id or chunk.document_id`，`is_enabled=chunk.is_enabled`。重新生成向量会覆盖同一 chunk 的旧索引记录，不重复插入。

embedding 服务只处理 `chunk_type=text`、`status=ready`、`is_enabled=true` 的切片，跳过 `parent_text`。
## 切分配置修复

新增 `chunker_config_service` 和 `/api/v1/chunker-config`。`chunk_service.rebuild_document_chunks` 会从数据库读取切分配置，支持运行时调整 chunk size、overlap、分隔符和父子分块开关。

父子分块默认关闭。开启后重新解析文档会生成 `parent_text` 和 `text`，embedding 与检索仍只处理 `chunk_type=text`。
## WeKnora 对齐 Phase D

hybrid-search 默认使用 RRF 融合：

```text
rrf_score = vector_weight / (rrf_k + vector_rank) + keyword_weight / (rrf_k + keyword_rank)
```

仅命中一路时只计算对应项。`weighted` 模式仍保留，用于兼容旧的加权分数融合。

向量召回仍基于 `chunk_embeddings.embedding`，过滤 `chunks.chunk_type=text`、`chunks.status=ready`、`chunks.is_enabled=true`、`chunk_embeddings.source_type=chunk`、`chunk_embeddings.is_enabled=true`。关键词召回优先使用 `chunk_embeddings.content`，为空时回退到 `chunks.content`。

`include_context=true` 时，返回 parent、previous、next 的短预览，不组装正式 context pack。
