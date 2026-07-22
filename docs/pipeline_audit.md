# Know_RAG 文档解析与切片流水线 — 健壮性及可用性审计报告

> 审计日期：2026-07-21
> 审计范围：文档上传 → 解析 → 切片 → 嵌入 → 索引 → 检索 全链路
> 审计方法：代码静态分析，所有结论均引用具体文件路径和行号

---

## 1. 执行摘要

Know_RAG 的文档处理管线总体设计合理，具有多层次回退机制和较完善的错误处理。但存在几个关键短板：**单点故障缺乏监控**、**解析回退链尾部硬失败**、**存储层零冗余**。

| 子系统 | 健壮性评分 | 可用性评分 | 综合 | 说明 |
|--------|-----------|-----------|------|------|
| 文档解析 | 7/10 | 6/10 | 6.5/10 | 三层回退但 RAGFlow 失败后无补救 |
| 切片分块 | 8/10 | 7/10 | 7.5/10 | 多策略分发 + 验证，但 WeKnora 端口复杂度高 |
| 存储 | 5/10 | 5/10 | 5.0/10 | 无冗余、无健康检查、ES 索引 0 副本 |
| 检索 | 7/10 | 7/10 | 7.0/10 | ES 优雅降级 + RRF 融合，成熟可靠 |
| **整体** | **7/10** | **6/10** | **6.5/10** | — |

**核心优势：**

- MinerU → RAGFlow DeepDoc 双引擎回退，MinerU 失败不中断整体解析
- Elasticsearch 查询复杂度超限自动压缩重试，混合检索向量失败可降级为纯关键词
- Canonical Markdown 切片管线具有三次回退链 + 父子块完整性校验
- 内容清洗贯穿全链路（PostgreSQL 编码清理、null byte 剥离、Orphan 短文本合并）

**关键风险：**

1. **PostgreSQL 单点故障** — 无只读副本，故障即全局不可用
2. **RAGFlow 解析失败为硬失败** — MinerU 回退到 RAGFlow 后如再失败，文档永久标记 `failed`，无自动重试
3. **无综合健康检查端点** — `/health` 仅返回应用存活状态，不检查 DB/MinIO/ES/Ollama 连通性

---

## 2. 流水线架构概览

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           DOCUMENT LIFECYCLE                              │
│                                                                            │
│  Upload ──▶ MinIO ──▶ resolve_parser_engine() ──▶ parse_local_document() │
│              storage           │                        │                  │
│                                │              ┌─────────┴─────────┐       │
│                                │              │  MinerU  │ RAGFlow │       │
│                                │              │ /file_parse│DeepDoc │       │
│                                │              └─────┬─────┴───┬─────┘       │
│                                │                    │ fallback│             │
│                                │              ┌─────┘         │             │
│                                │              ▼               ▼             │
│                                │        ParsedDocument ◀──────┘             │
│                                │              │                             │
│                                │              ▼                             │
│                                │   rebuild_document_chunks()                │
│                                │     ├─ build_canonical_markdown()          │
│                                │     ├─ split_parent_child() (WeKnora)      │
│                                │     └─ fallback_flat_text_from_blocks()    │
│                                │              │                             │
│                                │    ┌─────────┴──────────┐                  │
│                                │    ▼                    ▼                  │
│                                │  embed()         ES index_chunks()         │
│                                │ (Ollama)         (best-effort)             │
│                                │    │                    │                  │
│                                │    ▼                    ▼                  │
│                                │ pgvector           Elasticsearch           │
│                                │ chunk_embeddings   know_rag_chunks         │
│                                └──────────────────────────────────────────┘

  Retrieval:
    hybrid_search()
      ├─ vector_search() ──▶ pgvector <=> cosine distance
      │   └─ fail ──▶ keyword only (if enabled)
      ├─ keyword_search() ──▶ ES BM25
      │   ├─ too_many_clauses ──▶ compressed retry
      │   ├─ ES unavailable ──▶ SQL ILIKE (if ELASTICSEARCH_FALLBACK_TO_SQL)
      │   └─ fail ──▶ RuntimeError
      └─ merge_results_rrf() ──▶ ranked hits
```

### 依赖拓扑

```
                    ┌──────────────┐
                    │  PostgreSQL  │ ◀── 致命 SPOF（无备库）
                    └──────┬───────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌──────────┐   ┌──────────────┐  ┌──────────┐
    │  MinIO   │   │ Elasticsearch│  │  Ollama  │
    │ (无HA)   │   │ (0副本,1分片)│  │ (单实例) │
    └──────────┘   └──────────────┘  └──────────┘
         │                                   │
         ▼                                   ▼
   文件上传/下载                       嵌入向量生成
   (SPOF, 无本地回退)                 (SPOF, 检索可降级)
```

---

## 3. 健壮性分析（分组件）

### 3.1 文档解析

#### 3.1.1 优势

**三层回退链**（[parser_service.py:80-212](backend/app/services/parser_service.py#L80-L212)）

`parse_local_document()` 的引擎选择逻辑：
1. Markdown 文件直接走 `native_markdown`
2. MinerU 格式支持且配置存在 → MinerU HTTP `/file_parse`
3. MinerU 失败 + `mineru_fallback_to_deepdoc=True`（默认） → 自动回退 RAGFlow DeepDoc
4. 不支持的格式 → `parse_binary_fallback()` 生成附件占位块

MinerU 回退时完整记录上下文（[parser_service.py:136-144](backend/app/services/parser_service.py#L136-L144)）：
```python
"parser_requested": "mineru",
"fallback_used": True,
"fallback_reason": str(exc)[:1000]
```

**GPU/CUDA 错误检测并给出可操作提示**（[mineru_adapter.py:175-248](backend/app/services/document_parser/mineru_adapter.py#L175-L248)）

`_mineru_error_message()` 和 `_extract_mineru_error()` 检测 CUDA/GPU 关键字，返回人类可读的修复指引，例如 "This machine has no GPU. Switch MINERU_BACKEND=pipeline"。

**细粒度解析任务进度追踪**（[parse_task_service.py:15-25](backend/app/services/parse_task_service.py#L15-L25)）

7 个阶段：queued → prepare → parse → ocr → vlm → chunk → embedding → done，每阶段有明确的百分比进度（5%→100%）。

**40+ 文件格式支持**（[ragflow_format_adapter.py:940-970](backend/app/services/document_parser/ragflow_format_adapter.py#L940-L970)）

涵盖 PDF/DOCX/XLSX/PPTX/EPUB/HTML/Markdown/TXT/CSV/JSON/XML/图片/归档等几乎所有常见文档格式。

**超时控制**

| 组件 | 超时 | 位置 |
|------|------|------|
| MinerU HTTP | 300s（可配） | [mineru_adapter.py:268](backend/app/services/document_parser/mineru_adapter.py#L268) |
| Legacy .doc (antiword/catdoc) | 60s | [generic_parsers.py:303](backend/app/services/document_parser/generic_parsers.py#L303) |
| DeepDoc OCR 运行时 | 180s | deepdoc_runtime.py:403 |
| VLM/Vision chat | 180s | vision_service.py:146 |

**VLM/OCR 失败不阻塞整体解析**（[vision_service.py:414-534](backend/app/services/vision_service.py#L414-L534)）

单块 OCR/VLM 失败仅标记 `ocr_status="failed"` 和 `ocr_error` 在块 metadata 中，继续处理下一个块。

#### 3.1.2 劣势

**RAGFlow 解析失败为硬失败**（[parser_service.py:172-178](backend/app/services/parser_service.py#L172-L178)）

```python
except Exception as exc:
    reason = sanitize_text_for_postgres(str(exc))[:1000]
    raise ValueError(f"文档解析器执行失败：{reason}") from exc
```

MinerU → RAGFlow 回退后如果 RAGFlow 也失败，直接抛出 `ValueError`，文档标记为 `failed`，无进一步回退（如 binary fallback）。受损文档除非用户手动触发 `reparse`，否则永远不会恢复。

**无上传文件大小限制**（[document_service.py:28](backend/app/services/document_service.py#L28)）

```python
SUPPORTED_FILE_TYPES = None  # Phase N: accept all file suffixes
```

无文件大小检查。大 PDF（数百 MB）在 MinerU 300s 超时内无法完成解析，导致永久失败。

**Markdown 文件无回退路径**（[parser_service.py:63-64](backend/app/services/parser_service.py#L63-L64)）

```python
if file_type.lower().lstrip(".") in {"md", "markdown"}:
    return "native_markdown"
```

硬编码路由到 `native_markdown`，如果该解析器异常，无替代引擎。

**Windows/Docker 路径翻译脆弱**（[mineru_adapter.py:669-683](backend/app/services/document_parser/mineru_adapter.py#L669-L683)）

`_translate_mineru_path_to_backend_path()` 使用硬编码路径前缀映射处理 Docker 容器内后端与 Windows 宿主机 MinerU 之间的输出目录差异，环境变化时易断裂。

**解析缓存清理不可逆**（[document_service.py:1021-1026](backend/app/services/document_service.py#L1021-L1026)）

`CLEANUP_PARSE_CACHE_AFTER_PARSE=true` 时解析完成后立即删除临时文件。如果后续需要查看原始解析结果或调试，数据不可恢复。

**`storage_backend` 重复定义**（[config.py:55](backend/app/core/config.py#L55) 和 [config.py:66](backend/app/core/config.py#L66)）

同一字段在两个位置定义，值相同但易造成混淆。

---

### 3.2 切片分块

#### 3.2.1 优势

**多策略自动分发**（[markdown_splitter.py:144-170](backend/app/services/chunker/markdown_splitter.py#L144-L170)）

`auto` 策略的探测链：检测 Markdown 标题 → `split_by_headings()` → 失败回退 `split_recursive()`，检测启发式章节边界 → `split_by_heuristics()` → 失败回退 `split_recursive()`，否则直接 `split_recursive()`。

**后分割验证与修复**（[weknora_splitter.py:433-460](backend/app/services/chunker/weknora_splitter.py#L433-L460)）

`validate_chunks()` 拒绝以下结果：
- 零切片产出
- 大文档仅 1 个切片（>2x chunk_size）
- 过多微小切片（>25% 块 <50 字符）
- 最大切片超过 2x 目标大小
- 所有切片远小于目标

**保护模式识别**（[chunk_service.py:793-821](backend/app/services/chunk_service.py#L793-L821)）

`_is_pure_markdown_table()`、`_is_pure_fenced_code()` 在切分时保护表格和代码块结构不被破坏。

**Canonical Markdown 管线 + 起源追踪**（[chunk_service.py:1059-1307](backend/app/services/chunk_service.py#L1059-L1307)）

`_rebuild_canonical_weknora_parent_child()` 将解析器输出统一转换为 Canonical Markdown，通过 `build_canonical_parent_child_plan()` 生成包含图引用、起源、资产的切分计划，经规范化 → 起源映射 → 图引用链接 → WeKnora 双通切分 → 标题上下文携带 → 子块物化 → 去重 → 尾块合并 → 父块扁平化 的完整流程。

**三次回退链**（[chunk_service.py:1092-1300](backend/app/services/chunk_service.py#L1092-L1300)）

1. Canonical Markdown 为空 → `_rebuild_fallback_flat_text_from_blocks()`（从解析块直接构建文本块）
2. Canonical 非空但零子块 → 同上回退
3. 结构化重建零草稿 → `_build_fallback_text_drafts()` in rebuild_service.py

**父子块完整性强制**（[chunk_service.py:1310-1348](backend/app/services/chunk_service.py#L1310-L1348)）

`_enforce_parent_child_integrity()` 移除不足 2 个子块的父块，并将子块的 `parent_chunk_id` 置空。

**Skip-Embedding 逻辑加白名单**（[chunk_service.py:168-173](backend/app/services/chunk_service.py#L168-L173)）

`_embedding_skip_info()` 仅信任已知理由（`placeholder_table`、`placeholder_image`、`orphan_short_heading` 等），未知 skip 理由会被重新评估。

**切片边界修复**（[chunk_postprocess.py:69-179](backend/app/services/chunker/chunk_postprocess.py#L69-L179)）

按优先级修复：首字不完整合并、前导标点剥离/合并、短孤立文本合并到相邻块。

#### 3.2.2 劣势

**WeKnora 端口复杂度高**（[weknora_splitter.py](backend/app/services/chunker/weknora_splitter.py)）

整文件 1063 行 Go→Python 运行时移植。文档分析（`profile_document()`）、多级策略链（`resolve_chain_with_profile()`）、保护式递归切分（`split_text_legacy()`）全部集中在该文件中，边缘情况 bug 的表面积很大。

**`chunk_size=512` 配置存在误导**（[config.py:43](backend/app/core/config.py#L43) vs [chunk_service.py:1118-1132](backend/app/services/chunk_service.py#L1118-L1132)）

`DEFAULT_CHUNK_SIZE=512` 是 `split_document()` 非 parent-child 模式的设置，但生产路径 `_rebuild_canonical_weknora_parent_child()` 始终内部启用 parent-child（`enable_parent_child=True` hardcode），实际使用的是 `PARENT_CHUNK_SIZE=4096` 和 `CHILD_CHUNK_SIZE=384`。用户修改 `chunk_size` 不影响生产管线的实际切分行为。

**零切片错误消息歧义**（[chunk_service.py:719-796](backend/app/services/chunk_service.py#L719-L796)）

切片结果为 0 时抛出 `ValueError`，消息为 "文档切分结果为空，已保留原有切片"，但实际是否保留了旧切片取决于调用方 `rebuild_document_chunks()` 中先删后建的顺序。

**无切片数量上限**

恶意构造的超大文档（如 10,000 页 PDF）可生成无界数量的切片，耗尽数据库空间。

---

### 3.3 存储

#### 3.3.1 优势

**MinIO 流式上传/下载**（[minio_storage.py:108](backend/app/services/storage/minio_storage.py#L108)）

1MB 块级流式读写，避免大文件占满内存。

**路径穿越防护**（[minio_storage.py:90](backend/app/services/storage/minio_storage.py#L90)）

```python
any(part in {".", ".."} for part in parts)
```

拒绝包含 `.` 或 `..` 段的路径。

**ES 查询自动压缩重试**（[keyword_index_service.py:702-713](backend/app/services/keyword_index_service.py#L702-L713)）

`search_bm25()` 遇到 `too_many_nested_clauses` 错误时自动以 `force_safe=True` 压缩查询重试一次，再失败才抛出 `ElasticsearchQueryComplexityError`。

**对象键名安全化**（[minio_storage.py:54](backend/app/services/storage/minio_storage.py#L54)）

`_safe_segment()` 将文件名截断为 180 字符，仅保留字母数字和 `-`、`_`、`.`。

#### 3.3.2 劣势

**无综合健康检查端点**（[main.py:85-93](backend/app/main.py#L85-L93)）

`GET /health` 仅返回 `{"status": "ok", "service": "know_rag", "version": "0.1.0"}`。不检查：
- PostgreSQL 连通性
- MinIO 可达性
- Elasticsearch 集群状态
- Ollama 模型可用性
- Redis 连通性

运维人员无法通过健康检查感知服务降级。

**ES 索引 1 分片 0 副本**（[keyword_index_service.py:360-361](backend/app/services/keyword_index_service.py#L360-L361)）

```python
"number_of_shards": 1,
"number_of_replicas": 0
```

ES 节点宕机 → 全部关键词检索功能丧失（只能依赖 SQL ILIKE 回退，且需手动开启 `ELASTICSEARCH_FALLBACK_TO_SQL`）。

**IVFFlat 索引在大数据量下退化**（[migrations/0003_*.py](backend/app/db/migrations/versions/0003_embedding_retrieval_phase4.py#L61-L62)）

```sql
CREATE INDEX ... USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
```

IVFFlat `lists=100` 在 >100K 向量时召回率和速度显著下降。未规划 HNSW 迁移。

**ES 索引为尽力而为**（[keyword_index_service.py:586-590](backend/app/services/keyword_index_service.py#L586-L590)）

`strict=False` 默认，ES 索引失败被静默吞掉。文档解析成功但切片落入关键词搜索盲区，用户无法感知。

**`parse_task_events` 任务完成后销毁**（[parse_task_service.py:174](backend/app/services/parse_task_service.py#L174)）

解析任务完成/失败时删除所有事件记录，无法回溯排查历史解析问题。

**MinIO Bucket 创建无重试**（[minio_storage.py:100-101](backend/app/services/storage/minio_storage.py#L100-L101)）

首次写入时懒创建 Bucket，若网络抖动导致创建失败，上传直接报错，无重试。

---

### 3.4 检索

#### 3.4.1 优势

**Elasticsearch 优雅降级**（[retrieval_service.py:1405-1438](backend/app/services/retrieval_service.py#L1405-L1438)）

- ES 查询复杂度超限 → 压缩重试
- ES 不可用 + `ELASTICSEARCH_FALLBACK_TO_SQL=true` → SQL ILIKE 回退
- ES 异常 + 向量结果可用 → 仅关键词失败降级为 degradation message
- ES 异常 + 向量结果不可用 → 返回错误

**混合检索 RRF 融合**（[retrieval_service.py:552-580](backend/app/services/retrieval_service.py#L552-L580)）

`merge_results_rrf()` 对向量和关键词结果做 Reciprocal Rank Fusion：
```
rrf_score = vector_weight/(k + vector_rank) + keyword_weight/(k + keyword_rank)
```

**向量搜索失败降级**（[retrieval_service.py:1414-1418](backend/app/services/retrieval_service.py#L1414-L1418)）

向量搜索异常被捕获，如果关键词搜索仍可用则继续，否则返回错误。

**父子块上下文 5 级渐进匹配**（[retrieval_service.py:723-853](backend/app/services/retrieval_service.py#L723-L853)）

`_locate_child_in_parent()` 使用 5 种渐进式宽松匹配策略在父块中定位子块，窗口边界对齐到句子/段落边界。

**三档上下文模式**（[retrieval_service.py:37-41](backend/app/services/retrieval_service.py#L37-L41)）

| 模式 | Parent 最大 | Neighbor 最大 | Children | Parent Contexts |
|------|-------------|---------------|----------|-----------------|
| compact | 500 | 150 | 5 | 3 |
| standard | 2000 | 300 | 30 | 5 |
| full | 4000 | 500 | 30 | 8 |

#### 3.4.2 劣势

**`AUTO_EMBED_AFTER_PARSE` 默认 false**（[config.py:21](backend/app/core/config.py#L21)）

新用户解析文档后不会自动生成向量，检索结果为空，但无法从界面上直观判断问题根因。

**7 种文档状态转换复杂**（[document_service.py:234-830](backend/app/services/document_service.py#L234-L830)）

`pending` → `parsing` → `parsed` / `parsed_with_fallback` / `failed` / `chunk_failed` / `pending_retry`

`parsed_with_fallback` 和 `chunk_failed` 的区别对用户不直观，监控告警阈值难以设定。

---

## 4. 可用性分析

### 4.1 单点故障 (SPOF)

| 组件 | 故障影响 | 降级策略 | 严重度 |
|------|---------|---------|--------|
| **PostgreSQL** | 全部功能不可用 | 无备库，无只读副本 | **致命** |
| **MinIO** | 文件上传/下载全部失败 | 无本地存储回退 | **严重** |
| **Ollama** | 嵌入生成失败，向量检索不可用 | 关键词检索仅存（需 ES 正常） | **高** |
| **Elasticsearch** | 关键词检索不可用 | SQL ILIKE 回退（需手动配置 `ELASTICSEARCH_FALLBACK_TO_SQL`） | **中** |
| **MinerU 服务** | 复杂文档解析失败 | RAGFlow 回退；如 RAGFlow 也失败 → 硬失败 | **中** |
| **Redis** | 缓存/会话丢失 | 影响有限（主要在验证码等场景） | **低** |

### 4.2 优雅降级路径

```
检索请求
  ├─ 向量搜索成功 ──────────────┐
  │   失败 ──▶ 降级提示 + 继续   │
  ├─ 关键词搜索成功 ────────────┤
  │   查询复杂度超限 ──▶ 压缩重试 ├──▶ RRF 融合 ──▶ 返回结果
  │   ES 不可用 ──▶ SQL ILIKE    │
  │   全部失败 ──▶ 错误响应       │
  └──────────────────────────────┘

解析请求
  ├─ MinerU 成功 ──▶ ParsedDocument ──▶ 切片
  │   失败 ──▶ RAGFlow DeepDoc
  │              ├─ 成功 ──▶ ParsedDocument（标记 fallback_used=true）
  │              └─ 失败 ──▶ ValueError ──▶ 文档标记 failed
  └─ 未知格式 ──▶ binary_fallback ──▶ 附件占位块
```

---

## 5. 风险矩阵

| # | 风险 | 严重度 | 可能性 | 风险值 | 当前缓解 |
|---|------|--------|--------|--------|---------|
| R1 | PostgreSQL 宕机 | 5 | 2 | **10** | 无 |
| R2 | MinIO 宕机 | 4 | 2 | **8** | 无 |
| R3 | Ollama 不可用 | 4 | 3 | **12** | 关键词检索回退 |
| R4 | MinerU + RAGFlow 双重失败 | 3 | 3 | **9** | 单层回退（不足） |
| R5 | 大文件超时（MinerU 300s） | 2 | 4 | **8** | 无文件大小限制 |
| R6 | ES 索引数据丢失（0 副本） | 3 | 2 | **6** | SQL ILIKE（需手动开启） |
| R7 | IVFFlat 性能退化（>100K 向量） | 3 | 2 | **6** | 无 HNSW 迁移计划 |
| R8 | 零切片状态消息歧义 | 2 | 2 | **4** | 无 |
| R9 | parse_task_events 无审计追踪 | 1 | 4 | **4** | 无 |
| R10 | chunk_size 配置误导 | 1 | 4 | **4** | 无 |

---

## 6. 建议（按优先级）

### P0 — 关键（1-2 天/项）

**6.1 增加综合健康检查端点**
- 文件：[main.py](backend/app/main.py#L85-L93)
- 检查 DB、MinIO、ES、Ollama 连通性，返回各组件状态
- 工作量：1 天
- 应对风险：R1, R2, R3

**6.2 添加上传文件大小限制**
- 文件：[document_service.py](backend/app/services/document_service.py#L177-L286)
- 新增 `MAX_UPLOAD_SIZE_BYTES` 配置（建议 100MB），在 `upload_document()` 中校验
- 工作量：0.5 天
- 应对风险：R5

**6.3 ES 索引副本数改为 1**
- 文件：[keyword_index_service.py:360-361](backend/app/services/keyword_index_service.py#L360-L361)
- `"number_of_replicas": 0` → `1`
- 工作量：0.5 天（需至少 2 个 ES 节点）
- 应对风险：R6

### P1 — 高（3-5 天/项）

**6.4 RAGFlow 失败后增加 binary fallback 兜底**
- 文件：[parser_service.py:172-178](backend/app/services/parser_service.py#L172-L178)
- 将 `raise ValueError` 改为调用 `parse_binary_fallback()`，生成附件占位块，文档标记 `parsed_with_fallback`
- 工作量：1 天
- 应对风险：R4

**6.5 修复 chunk_size 配置误导**
- 文件：[chunk_service.py:1118-1132](backend/app/services/chunk_service.py#L1118-L1132)
- 方案 A：让 Canonical 管线尊重 `enable_parent_child_chunking` 设置（为 false 时用 flat 模式）
- 方案 B：重命名配置项为 `CANONICAL_CHILD_CHUNK_SIZE` 等，使语义与行为一致
- 工作量：2 天
- 应对风险：R10

**6.6 规划 HNSW 索引迁移路径**
- 文件：[migrations/](backend/app/db/migrations/versions/)
- 新增迁移：创建 HNSW 索引替代 IVFFlat（PGVector 0.5+ 支持 `USING hnsw`）
- 工作量：3 天（需性能基准测试）
- 应对风险：R7

### P2 — 中（1-2 天/项）

**6.7 增加切片数量上限**
- 文件：[chunker/service.py](backend/app/services/chunker/service.py)
- 单文档最多 5000 切片，超出截断并告警
- 工作量：1 天

**6.8 修复 `storage_backend` 重复定义**
- 文件：[config.py:55](backend/app/core/config.py#L55) — 删除第一个定义（保留第 66 行的）
- 工作量：0.1 天

**6.9 保留 parse_task_events 作为审计日志**
- 文件：[parse_task_service.py:174](backend/app/services/parse_task_service.py#L174)
- 方案：保留最近 30 天事件，或成功后标记 `archived=true` 而非物理删除
- 工作量：1 天
- 应对风险：R9

**6.10 明确零切片错误消息**
- 文件：[chunk_service.py:719-796](backend/app/services/chunk_service.py#L719-L796)
- 消息中区分"已保留旧切片"和"无旧切片可保留"两种情况
- 工作量：0.5 天
- 应对风险：R8

### P3 — 低（锦上添花）

**6.11 Markdown 解析器增加回退路径**
- 文件：[parser_service.py:63-64](backend/app/services/parser_service.py#L63-L64)
- native_markdown 失败时回退到 `parse_txt()`

**6.12 MinIO Bucket 创建增加重试**
- 文件：[minio_storage.py:47-49](backend/app/services/storage/minio_storage.py#L47-L49)
- `ensure_bucket()` 添加 3 次指数退避重试

**6.13 扩展被阻止的文件扩展名列表**
- 文件：[document_service.py:129-132](backend/app/services/document_service.py#L129-L132)
- 当前阻止列表仅 10 个扩展名（`.exe`, `.dll`, `.bat`, `.cmd`, `.msi`, `.ps1`, `.sh`, `.com`, `.scr`），建议添加更多可执行格式和归档炸弹格式

---

## 附录 A：文件索引

| 文件 | 关键内容 |
|------|---------|
| [parser_service.py:80-212](backend/app/services/parser_service.py#L80-L212) | 解析引擎选择与 MinerU→RAGFlow 回退 |
| [parser_service.py:172-178](backend/app/services/parser_service.py#L172-L178) | RAGFlow 硬失败点 |
| [mineru_adapter.py:175-248](backend/app/services/document_parser/mineru_adapter.py#L175-L248) | GPU/CUDA 错误检测 |
| [mineru_adapter.py:669-683](backend/app/services/document_parser/mineru_adapter.py#L669-L683) | Windows/Docker 路径翻译 |
| [mineru_adapter.py:1249-1421](backend/app/services/document_parser/mineru_adapter.py#L1249-L1421) | `parse_document_with_mineru()` 主逻辑 |
| [ragflow_format_adapter.py:940-970](backend/app/services/document_parser/ragflow_format_adapter.py#L940-L970) | RAGFlow 格式路由 |
| [document_service.py:177-286](backend/app/services/document_service.py#L177-L286) | 文档上传 + 解析触发 |
| [document_service.py:420-1026](backend/app/services/document_service.py#L420-L1026) | `parse_document()` 主流程 + 错误处理 |
| [chunk_service.py:1059-1307](backend/app/services/chunk_service.py#L1059-L1307) | Canonical WeKnora 重建 |
| [chunk_service.py:1310-1348](backend/app/services/chunk_service.py#L1310-L1348) | 父子块完整性校验 |
| [chunker/weknora_splitter.py](backend/app/services/chunker/weknora_splitter.py) | WeKnora 运行时端口（1063 行） |
| [chunker/markdown_splitter.py:144-170](backend/app/services/chunker/markdown_splitter.py#L144-L170) | 多策略自动分发 |
| [config.py:43-50](backend/app/core/config.py#L43-L50) | 切片配置默认值 |
| [config.py:55,66](backend/app/core/config.py#L55) | `storage_backend` 重复定义 |
| [retrieval_service.py:1392-1474](backend/app/services/retrieval_service.py#L1392-L1474) | 混合检索 + 降级路径 |
| [retrieval_service.py:552-580](backend/app/services/retrieval_service.py#L552-L580) | RRF 融合算法 |
| [keyword_index_service.py:360-361](backend/app/services/keyword_index_service.py#L360-L361) | ES 索引 1 分片 0 副本 |
| [keyword_index_service.py:702-713](backend/app/services/keyword_index_service.py#L702-L713) | ES 查询压缩重试 |
| [minio_storage.py:47-101](backend/app/services/storage/minio_storage.py#L47-L101) | Bucket 创建 + 文件上传 |
| [minio_storage.py:90](backend/app/services/storage/minio_storage.py#L90) | 路径穿越防护 |
| [main.py:85-93](backend/app/main.py#L85-L93) | `/health` 端点（仅存活检查） |
| [parse_task_service.py:174](backend/app/services/parse_task_service.py#L174) | 任务完成后事件删除 |
| [0003_embedding_retrieval_phase4.py](backend/app/db/migrations/versions/0003_embedding_retrieval_phase4.py) | IVFFlat 索引创建（lists=100） |

## 附录 B：配置清单

| 配置项 | 默认值 | 影响 |
|--------|--------|------|
| `MINERU_TIMEOUT_SECONDS` | 300 | 大文件可能超时 |
| `MINERU_FALLBACK_TO_DEEPDOC` | true | 关闭后 MinerU 失败 = 硬失败 |
| `AUTO_EMBED_AFTER_PARSE` | false | 关闭后需手动触发嵌入 |
| `DEFAULT_CHUNK_SIZE` | 512 | 仅影响非 parent-child 模式 |
| `ENABLE_PARENT_CHILD_CHUNKING` | false | 仅控制 parent 块物化，不影响内部切分逻辑 |
| `ELASTICSEARCH_FALLBACK_TO_SQL` | false | 关闭后 ES 宕机 = 关键词检索不可用 |
| `KEYWORD_SEARCH_BACKEND` | elasticsearch | 设为空字符串可完全禁用 ES |
| `CLEANUP_PARSE_CACHE_AFTER_PARSE` | true | 关闭会导致磁盘占用持续增长 |
| `EMBEDDING_CONTENT_MAX_CHARS` | 6000 | 嵌入输入截断上限 |
| `LLM_CONTEXT_MAX_SOURCES` | 8 | 送给 LLM 的源数量上限 |
