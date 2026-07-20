# Know_RAG 项目目录结构与主链路维护手册（最新代码基线）

> **代码基线**：当前工作区
> **维护日期**：2026-07-17
> **用途**：用于后续项目维护、功能定位、主链路排查、代码重构和新人接手。  
> **分析原则**：以本次上传代码的**真实调用关系**为准，不以历史设计文档或曾经存在的重构方案代替当前运行时事实。

---

## 0. 文档使用约定

### 0.1 文件状态标记

| 标记 | 含义 |
|---|---|
| **[主链路]** | 当前真实请求或后台任务会经过的生产代码 |
| **[核心算法]** | 决定解析、切分、检索或上下文效果的关键代码 |
| **[支撑]** | 被主链路调用的公共服务、配置、存储或模型代码 |
| **[兼容]** | 为旧接口、旧返回结构或旧实现保留，不是新功能优先修改位置 |
| **[非当前主链路]** | 文件存在，但当前 dispatcher/路由没有直接走到 |
| **[预留]** | 占位接口或未来扩展代码 |
| **[Vendor]** | 运行时依赖的外部项目代码，原则上不直接修改 |

### 0.2 当前运行时最重要的事实

```text
Document 解析完成
└─ chunk_service.rebuild_document_chunks()
   └─ Canonical Markdown + Old Structure First  【统一核心维护链路】
      ├─ enable_parent_child = true：持久化 parent_text
      └─ enable_parent_child = false：只持久化 child，仍使用同一 Canonical 计划
```

**必须注意：**

1. `runtime_config_from_db(db)` 读取数据库 `chunker_config`；`enable_parent_child` 当前只控制
   parent context 是否物化，不切换解析/Canonical/child 算法。
2. 因此，不能只看 `.env` 判断当前环境是否实际持久化 parent_text。
4. 当前项目重点维护的父子切分方案是：

```text
Canonical Markdown
→ 原 Old 两阶段 Parent/Child 骨架
→ 全局 heading context 继承
→ Text / Table / Image 物化
→ 同 asset_id 图片 overlap 副本去重
→ 精确 overlap shadow 去重
→ 精确 overlap 短尾合并
→ Parent 0/1 完整性校验
→ 同事务持久化 Chunk + Canonical provenance artifact
```

5. `merge_short_text_siblings()` 仍保留在代码中，但**当前生产 `build_canonical_parent_child_plan()` 不调用它**；生产只运行有来源范围和精确文本重叠双重证据的 `merge_overlap_tail_text_children()`。
6. 默认关键词检索后端是 Elasticsearch BM25，PostgreSQL ILIKE 仅是显式配置的故障回退。
7. 真实只读 MCP 服务位于 `backend/app/integrations/mcp/`；`routes_mcp_placeholder.py` 只是兼容占位路由。
8. 普通 chunk API 不返回完整 embedding，只返回索引状态、维度、范数和最多 16 维预览。
9. Current 文档入口是 `docs/index.md`；历史变更通过 Git 记录追溯，不保留重复 Phase/repair 报告。
10. 上传链路不设置应用层大小上限；容量边界由临时磁盘、MinIO 和外部代理决定。
11. `ParseTaskEvent` 仅是运行期临时进度，任务结束即清空，不保存 block/chunk 预览。

---

# 1. 项目总体目录树

> 以下目录树省略 `.git/`、`frontend/node_modules/`、运行缓存等不需要维护的生成目录。

```text
know_rag/
├─ .github/workflows/check.yml            # 后端 Ruff/pytest + 前端 check 持续集成
├─ .editorconfig                          # 跨编辑器 UTF-8、LF、缩进约定
├─ .gitignore                             # 凭据、依赖、缓存、构建产物忽略规则
├─ .agents/                              # Agent/自动化辅助配置目录
├─ .claude/                              # Claude Code 本地配置与 skills
│  ├─ settings.local.json                # 本地 Claude Code 设置
│  └─ skills/                            # 项目辅助技能
│
├─ backend/                              # 后端：FastAPI + SQLAlchemy + PostgreSQL + ES + 向量库
│  ├─ app/                               # 后端业务源码
│  ├─ scripts/                           # 后端维护脚本
│  ├─ tests/                             # 后端测试
│  ├─ ragflow_vendor/                    # RAGFlow/DeepDoc 运行时 Vendor 代码 [Vendor]
│  ├─ alembic.ini                        # Alembic 配置
│  ├─ Dockerfile                         # 后端镜像
│  ├─ .dockerignore                      # 排除后端缓存和本地虚拟环境
│  ├─ entrypoint.sh                      # 容器启动脚本
│  ├─ pyproject.toml                     # Python 工程配置
│  ├─ requirements.txt                   # pip 依赖
│  ├─ requirements-dev.txt               # pytest / pytest-asyncio / Ruff 开发依赖
│  └─ uv.lock                            # uv 锁定依赖
│
├─ frontend/                             # 前端：Vue 3 + Vite + TypeScript
│  ├─ src/                               # 前端源码
│  ├─ .dockerignore                      # 排除 node_modules/dist/coverage
│  ├─ Dockerfile                         # 前端镜像
│  ├─ eslint.config.js                   # Vue/TypeScript ESLint flat config
│  ├─ index.html                         # Vite 入口 HTML
│  ├─ package.json                       # npm 依赖与命令
│  ├─ package-lock.json                  # npm 锁文件
│  ├─ tsconfig.json                      # TypeScript 配置
│  └─ vite.config.ts                     # Vite 配置
│
├─ docker/                               # 基础设施容器配置
│  ├─ mineru/                            # MinerU 服务相关配置
│  └─ nginx/                             # Nginx 反向代理配置
│
├─ docs/                                 # Current 专项文档
│  └─ index.md                           # 文档权威顺序与维护入口
│
├─ .env                                  # 当前本地环境变量（敏感，不应纳入版本控制）
├─ .env.example                          # 环境变量模板
├─ docker-compose.yml                    # 主编排文件
├─ docker-compose.override.yml.example   # 本地覆盖模板
└─ README.md                             # 项目入口说明
```

---

# 2. 后端源码目录树与文件作用

## 2.1 `backend/app/` 总览

```text
backend/app/
├─ main.py                               # FastAPI 应用入口、路由注册、启动初始化 [主链路]
├─ api/                                  # HTTP API 路由层
├─ core/                                 # 配置、安全、统一响应、日志
├─ db/                                   # SQLAlchemy 模型、Session、Alembic migration
├─ schemas/                              # Pydantic 请求/响应结构
├─ services/                             # 核心业务服务
├─ integrations/mcp/                     # 真正的 MCP Server 与 HTTP 适配层
├─ plugins/parse_eval/                   # 解析效果评估插件
├─ scripts/                              # 应用内辅助脚本
└─ utils/                                # 通用工具
```

## 2.2 应用入口与 API 路由

```text
backend/app/
├─ main.py                               # 创建 FastAPI；挂载全部 router；启动时初始化 ES 索引
│
└─ api/
   ├─ routes_auth.py                     # 注册、登录、当前用户等认证接口
   ├─ routes_chat.py                     # Chat 接口
   ├─ routes_chunker_config.py           # 切分配置读取/更新；影响运行时切分分支
   ├─ routes_chunks.py                   # Chunk 列表、树、详情、启停、删除
   ├─ routes_documents.py                # 文档上传、解析、重解析、向量化 [主链路]
   ├─ routes_faq.py                      # FAQ 管理接口
   ├─ routes_files.py                    # 文件访问接口
   ├─ routes_initialization.py           # 系统初始化接口
   ├─ routes_knowledge_base.py           # 知识库 CRUD
   ├─ routes_mcp_placeholder.py          # 旧 MCP 占位 API [预留/非真实 MCP]
   ├─ routes_model_config.py             # 模型配置管理
   ├─ routes_parse_tasks.py              # 解析任务与事件查询
   ├─ routes_retrieval.py                # 混合检索与 LLM Context [主链路]
   └─ routes_vector_store.py             # 外部向量库配置与连通性测试
```

### `main.py` 的职责

```text
main.py
├─ 创建 FastAPI app
├─ 注册异常处理器
├─ 注册 /api/v1 下的业务路由
├─ 注册 parse_eval 插件路由
├─ startup_initialize_services()
│  └─ 在启用关键词检索时初始化 Elasticsearch index
└─ /health 健康检查
```

## 2.3 核心基础层

```text
backend/app/core/
├─ config.py                              # Settings；读取环境变量；集中管理模型/存储/解析配置
├─ logging.py                             # 日志配置
├─ response.py                            # success_response / error_response 等统一响应
└─ security.py                            # 密码哈希、JWT、当前用户认证

backend/app/db/
├─ models.py                              # 全部核心数据库表 ORM [支撑]
├─ session.py                             # Engine、SessionLocal、get_db
└─ migrations/
   ├─ env.py                              # Alembic 运行环境
   └─ versions/
      ├─ 0001_initial.py                  # 初始用户/知识库等表
      ├─ 0002_documents_chunks_phase3.py  # 文档与 Chunk
      ├─ 0003_embedding_retrieval_phase4.py# Embedding/检索配置
      ├─ 0004_ensure_chunk_embeddings.py  # ChunkEmbedding 修正
      ├─ 0005_weknora_alignment_phase_a.py# WeKnora 对齐字段
      ├─ 0006_chunker_config.py           # ChunkerConfig
      ├─ 0007_retrieval_rrf_phase_d.py    # RRF 检索配置
      ├─ 0008_parse_workbench_phase_z.py  # Parse Workbench
      ├─ 0009_parse_eval_plugin_phase_aa.py# 解析评估插件
      ├─ 0010_parse_event_retention.py    # 解析事件保留策略
      └─ 0011_canonical_provenance_p25.py # Canonical provenance 当前 revision
```

### `models.py` 中的核心实体

| ORM 类 | 作用 |
|---|---|
| `User` | 用户与认证主体 |
| `KnowledgeBase` | 知识库 |
| `RetrievalConfig` | 检索模式、权重、阈值、父上下文等配置 |
| `ModelConfig` | OCR/VLM/Embedding 等模型配置 |
| `VectorStoreConfig` | PostgreSQL / Qdrant / Milvus 等向量存储配置 |
| `ChunkerConfig` | 切分运行时配置；`enable_parent_child` 在这里决定主分支 |
| `AuditLog` | 操作审计 |
| `Document` | 上传文档、解析状态、解析元数据 |
| `DocumentCanonicalArtifact` | 当前规范化 Canonical、source nodes、assets 与引用证据账本 |
| `Chunk` | 子块、父块、表格、图片、OCR 文本等统一存储 |
| `ParseTask` | 解析任务状态 |
| `ParseTaskEvent` | 运行期临时阶段事件；终态清空，不保存 block/chunk 预览 |
| `ChunkEmbedding` | Chunk 向量；PostgreSQL 侧的 embedding 真值记录 |

## 2.4 Schema 层

```text
backend/app/schemas/
├─ auth.py                                # 认证请求/响应
├─ chat.py                                # Chat 请求/响应
├─ chunk.py                               # Chunk 列表、详情、树结构
├─ chunker_config.py                      # 切分配置 DTO
├─ document.py                            # 上传、解析、文档 DTO
├─ faq.py                                 # FAQ DTO
├─ knowledge_base.py                      # 知识库 DTO
├─ mcp_placeholder.py                     # 旧 MCP 占位 DTO
├─ model_config.py                        # 模型配置 DTO
├─ retrieval.py                           # 混合检索/LLM Context DTO
└─ vector_store.py                        # 向量库配置 DTO
```

---

# 3. `services/` 核心业务目录与文件作用

## 3.1 一级业务服务

```text
backend/app/services/
├─ auth_service.py                        # 用户注册、登录、认证业务
├─ audit_service.py                       # 审计日志
├─ batch_vectorize_service.py             # 批量向量化任务
├─ captcha_service.py                     # 验证码
├─ chat_service.py                        # Chat 业务
│
├─ document_service.py                    # 文档上传、解析编排、删除 [主链路]
├─ parse_task_service.py                  # 解析任务状态与事件 [主链路]
├─ parser_service.py                      # 解析器总调度 [主链路]
├─ vision_service.py                      # OCR/VLM 增强 [主链路]
├─ chunk_service.py                       # Chunk 重建总 dispatcher 与持久化 [主链路]
├─ chunker_config_service.py              # 读取 DB 切分配置 [主链路]
│
├─ embedding_service.py                   # Embedding 生成与写入 [主链路]
├─ keyword_index_service.py               # Elasticsearch BM25 索引与查询 [主链路]
├─ vector_store_adapters.py               # Qdrant/Milvus 外部向量适配 [主链路支撑]
├─ vector_store_service.py                # 向量库配置管理
│
├─ retrieval_service.py                   # 向量+BM25+融合+父上下文 [主链路]
├─ llm_context_builder.py                 # 云端 Flat Chunks / Legacy Context 构建 [主链路]
│
├─ faq_service.py                         # FAQ 业务
├─ knowledge_base_service.py              # 知识库业务
├─ model_config_service.py                # 模型配置业务
├─ ollama_service.py                      # Ollama 连通/模型辅助
├─ content_cleaning.py                    # 文本清洗公共逻辑
└─ mcp_placeholder_service.py             # 旧占位服务 [预留/非真实 MCP]
```

## 3.2 文档解析子系统 `document_parser/`

```text
backend/app/services/document_parser/
├─ types.py                               # ParsedDocument / ParsedBlock 统一中间模型 [核心]
├─ markdown_ordered.py                    # Markdown 保序解析；标题/表格/代码结构保留
├─ mineru_adapter.py                      # MinerU HTTP 调用、结果归一化、媒体落盘 [主链路]
├─ ragflow_format_adapter.py              # 多格式 RAGFlow/DeepDoc 总适配 [主链路]
├─ ragflow_deepdoc_adapter.py             # DeepDoc 调用适配
├─ deepdoc_runtime.py                     # DeepDoc 运行时加载
├─ deepdoc_local_ocr.py                   # DeepDoc 本地 OCR 支撑
├─ pdf_ragflow_advanced.py                # PDF 高级解析
├─ pdf_ragflow_lite.py                    # PDF 轻量解析
├─ docx_multimodal.py                     # DOCX 多模态解析
├─ generic_parsers.py                     # 通用格式解析器
├─ media_storage.py                       # 解析媒体文件保存
├─ block_reflow.py                        # ParsedBlock 重排、OCR 文本修复、标题路径继承 [主链路]
├─ image_relation_resolver.py             # 图片 caption 质量、图号歧义、bbox 空间候选与 binding_score [主链路]
├─ figure_linker.py                       # normalize 后正文图号引用 → asset 候选证据（P2/P2.5）[主链路]
└─ canonical_document.py                  # Canonical Markdown + asset/source-node provenance [核心算法]
```

### 统一解析中间层

```text
各种文件格式
→ Parser Adapter
→ ParsedDocument
   ├─ content
   └─ blocks[]
      ├─ block_type: text / table / image / ...
      ├─ text
      ├─ order_index
      ├─ bbox / page
      ├─ context_header
      └─ metadata
```

**维护原则：** Parser Adapter 的差异应尽量收敛到 `ParsedDocument / ParsedBlock`，不要让下游 Chunker 针对每个解析器写大量专用分支。

## 3.3 当前切分算法目录 `chunker/`

```text
backend/app/services/chunker/
├─ service.py                             # 通用 split_document 入口；兼容非父子模式
├─ models.py                              # ParsedChunk / ChunkerConfig 等切分模型
├─ text_unit.py                           # 文本单元模型
│
├─ canonical_parent_child.py              # Canonical → 父子切分计划 [当前核心算法]
├─ parent_child_splitter.py               # Know_RAG ↔ WeKnora Python 算法适配
├─ weknora_splitter.py                    # Old 两阶段 Parent/Child 实际算法实现
│
├─ protected_patterns.py                  # 代码/表格/LaTeX/HTML/目录树等保护范围 [关键]
├─ boundary_utils.py                      # 安全边界辅助
├─ heading_utils.py                       # Heading 工具
├─ heading_splitter.py                    # 标题优先切分策略
├─ heuristic_splitter.py                  # 启发式边界策略
├─ recursive_splitter.py                  # 递归切分策略
├─ markdown_splitter.py                   # Markdown 切分辅助
└─ chunk_postprocess.py                   # 后处理辅助
```

### 当前生产父子切分的核心文件优先级

```text
1. canonical_parent_child.py   # 当前最优先看：整体计划和物化策略
2. weknora_splitter.py         # 真正决定 Old 父/子边界
3. parent_child_splitter.py    # 两套模型之间的适配
4. protected_patterns.py       # 保护代码、表格、公式、结构文本
5. canonical_document.py       # 切分前的统一文档结构
6. chunk_service.py            # runtime dispatcher + ORM 物化 + 持久化
```

## 3.4 `chunking/` 模块化重构目录

```text
backend/app/services/chunking/
├─ rebuild_service.py                     # ChunkRebuildService 重构入口
├─ chunk_draft.py                         # Chunk draft 中间模型
├─ section_builder.py                     # Section 构建
├─ parent_builder.py                      # Parent 构建
├─ child_builder.py                       # Child 构建
├─ image_chunk_builder.py                 # Image Chunk 构建
├─ table_chunk_builder.py                 # Table Chunk 构建
├─ code_context_merger.py                 # 代码上下文合并
├─ metadata_builder.py                    # Metadata 构建
├─ chunk_linker.py                        # Chunk 链接
└─ chunk_persistence.py                   # Chunk 持久化
```

> **状态：`[非当前主链路]`**
>
> 这组代码代表一次模块化重构方向，并有相应测试，但静态调用关系显示：
>
> - 当前 `rebuild_document_chunks()` dispatcher **没有调用** `ChunkRebuildService`；
> - 因此维护当前实际切分效果时，不应优先修改这里；
> - `chunk_service.py` 仍保留部分对 `section_builder`、`parent_builder`、`code_context_merger` 的引用，但它们服务于保留的旧结构化函数/兼容函数，不等于 `ChunkRebuildService` 已接管当前主流程。

## 3.5 Embedding 子系统

```text
backend/app/services/embedding/
└─ embedding_content_builder.py           # 将不同 Chunk 转成真正用于向量化的语义文本 [核心]

backend/app/services/
├─ embedding_service.py                   # 模型调用、批量向量化、状态统计、写库
├─ vector_store_adapters.py               # Qdrant / Milvus upsert/search
└─ vector_store_service.py                # 向量存储配置
```

`embedding_content_builder.py` 的作用不是简单返回 `chunk.content`，而是按类型构造语义输入：

```text
Document Title
+ Context Header
+ Type-aware Body
```

并针对文本、结构树、表格、图片 OCR/VLM、公式等内容分别处理。`parent_text` 不进入 Embedding。

## 3.6 Context 统一服务层

```text
backend/app/services/context/
├─ knowledge_context_service.py           # 检索 → 上下文返回模式的统一 Facade [主链路]
└─ schemas.py                             # Context 服务内部结构
```

该层是当前 FastAPI、MCP 以及未来 Chat/Agent 的统一上下文入口，目的是避免每个调用方自行拼检索结果。

---

# 4. 真正的 MCP 目录

```text
backend/app/integrations/mcp/
├─ server.py                              # FastMCP Server；注册 MCP Tools [主链路]
├─ tools.py                               # MCP 参数 → Know_RAG HTTP API 参数的薄适配层
├─ http_client.py                         # 登录并调用 FastAPI；401 时重试一次
├─ http_server.py                         # MCP HTTP 服务相关入口/支撑
├─ schemas.py                             # MCP 内部 schema
└─ README.md                              # MCP 使用说明
```

**重要边界：**

```text
MCP Client
→ MCP Server
→ tools.py
→ http_client.py
→ Know_RAG FastAPI HTTP API
→ retrieval/context service
→ DB / ES / Vector Store
```

MCP **不直接连接 PostgreSQL**。这保证了鉴权、检索策略和返回结构仍由后端统一维护。

### 当前真实 MCP Tools

| Tool | 作用 |
|---|---|
| `know_rag_list_knowledge_bases` | 查询可用知识库 |
| `know_rag_search_context` | 调用 `/knowledge-bases/{kb_id}/llm-context` 获取云端可用 `chunks[]` |

`routes_mcp_placeholder.py` 和 `mcp_placeholder_service.py` 不是这里的真实 MCP Server，不要混淆。

---

# 5. 前端目录树与文件作用

```text
frontend/src/
├─ main.ts                                # Vue 应用入口
├─ App.vue                                # 根组件
├─ router/index.ts                        # 页面路由注册
│
├─ api/
│  ├─ client.ts                           # HTTP Client、Token/错误处理
│  ├─ config.ts                           # 配置 API
│  ├─ auth.ts                             # 认证 API
│  ├─ knowledgeBases.ts                   # 知识库 API
│  ├─ documents.ts                        # 文档上传/解析/向量化 API
│  ├─ chunks.ts                           # Chunk API
│  ├─ parseTasks.ts                       # 解析任务 API
│  └─ retrieval.ts                        # 检索/LLM Context 调试 API
│
├─ layouts/
│  └─ MainLayout.vue                      # 主后台布局
│
├─ components/common/
│  ├─ EmptyState.vue                      # 空状态
│  ├─ PageHeader.vue                      # 页面标题区
│  ├─ StatCard.vue                        # 统计卡片
│  └─ StatusBadge.vue                     # 状态标签
│
├─ views/
│  ├─ LoginView.vue                       # 登录
│  ├─ DashboardView.vue                   # 首页仪表盘
│  ├─ KnowledgeBaseListView.vue           # 知识库列表
│  ├─ KnowledgeBaseFormView.vue           # 新建/编辑知识库
│  ├─ KnowledgeBaseDetailView.vue         # 知识库详情
│  ├─ DocumentListView.vue                # 文档列表
│  ├─ DocumentUploadView.vue              # 上传文档
│  ├─ DocumentDetailView.vue              # 文档详情/解析状态
│  ├─ ParseWorkbenchView.vue              # 解析工作台
│  ├─ ChunkListView.vue                   # Chunk 列表
│  ├─ ChunkDetailView.vue                 # Chunk 详情
│  ├─ ChunkerConfigView.vue               # 切分配置；影响 enable_parent_child
│  ├─ ModelConfigView.vue                 # 模型配置
│  ├─ VectorStoreConfigView.vue           # 向量存储配置
│  ├─ RetrievalConfigView.vue             # 检索策略配置
│  ├─ SearchDebugView.vue                 # 混合检索调试
│  └─ PlaceholderView.vue                 # 占位页
│
├─ plugins/parse-eval/
│  ├─ ParseEvalHomeView.vue               # 解析评估首页
│  ├─ ParseEvalDocumentView.vue           # 文档评估详情
│  ├─ ParseEvalRunView.vue                # 评估运行页
│  ├─ api.ts                              # 插件 API
│  ├─ routes.ts                           # 插件路由
│  └─ eval.css                            # 插件样式
│
└─ styles/
   ├─ tokens.css                          # 设计变量
   ├─ base.css                            # 基础样式
   └─ components.css                      # 组件样式
```

---

# 6. 主链路一：文档上传 → 解析 → Chunk 入库

## 6.1 总链路树

```text
POST /api/v1/knowledge-bases/{kb_id}/documents
│
├─ api/routes_documents.py
│  └─ upload_document()
│
├─ services/document_service.py
│  └─ upload_document()
│     ├─ 校验知识库/文件类型
│     ├─ 生成源文件 object_key
│     ├─ storage/minio_storage.py 保存原文件
│     ├─ 保存 parse options 到 document.metadata_json
│     ├─ 创建 Document
│     └─ parse_immediately=true 时创建后台解析任务
│
├─ services/parse_task_service.py
│  ├─ create_parse_task()
│  ├─ update_task()                       # 更新终态时清空临时事件
│  └─ add_event()                         # 跳过 parsed_block/chunk_created
│
├─ services/document_service.py
│  └─ run_parse_document_task()
│     └─ 新建 SessionLocal 后调用 parse_document()
│
└─ services/document_service.py
   └─ parse_document()
      ├─ parser_service.parse_local_document()
      ├─ vision_service.enhance_ocr_blocks()
      ├─ vision_service.enhance_image_blocks()
      ├─ document_parser.block_reflow.reflow_parsed_document()
      ├─ 保存解析 metadata / preview
      ├─ chunk_service.rebuild_document_chunks()
      ├─ 可选 auto embedding
      ├─ 更新任务完成状态
      └─ finally 清理 parse cache
```

## 6.2 主要 Python 文件与作用

| 顺序 | Python 文件 | 核心函数/类 | 具体作用 | 修改风险 |
|---|---|---|---|---|
| 1 | `api/routes_documents.py` | `upload_document()` | HTTP 入口；接收上传和解析参数 | 中 |
| 2 | `services/document_service.py` | `upload_document()` | 保存源文件、创建 Document、决定是否立即解析 | 高 |
| 3 | `services/storage/minio_storage.py` | 存储相关函数 | 无应用层大小上限；复用 UploadFile 临时流，避免第二份完整副本 | 高 |
| 4 | `services/parse_task_service.py` | `create_parse_task()` 等 | 持久化任务状态；阶段事件仅运行期临时保留 | 中 |
| 5 | `services/document_service.py` | `run_parse_document_task()` | 后台任务线程/进程中的 DB Session 隔离 | 高 |
| 6 | `services/document_service.py` | `parse_document()` | 解析总编排；整个 ingest 的业务主控 | **极高** |
| 7 | `services/parser_service.py` | `parse_local_document()` | 选择 MinerU/RAGFlow/Markdown 等解析器 | **极高** |
| 8 | `services/vision_service.py` | `enhance_ocr_blocks()` / `enhance_image_blocks()` | 对图片块补 OCR/VLM 信息 | 高 |
| 9 | `document_parser/block_reflow.py` | `reflow_parsed_document()` | 修复 OCR 行、重排 block、传递标题结构 | 高 |
| 10 | `services/chunk_service.py` | `rebuild_document_chunks()` | 清旧 Chunk/向量/ES，并重建新 Chunk | **极高** |

## 6.3 `parse_document()` 的阶段职责

```text
原始文件
→ Parse
→ OCR Enhance
→ VLM/Image Enhance
→ Block Reflow
→ 保存 Parse Result Metadata
→ Rebuild Chunks
→ Optional Auto Embedding
→ Complete
```

维护时应保持这一层只负责编排，具体格式解析、切分算法、Embedding 算法分别放在各自模块。

---

# 7. 主链路二：解析器选择与统一中间结构

## 7.1 Parser Router

```text
parser_service.parse_local_document(document, db)
│
├─ Markdown 原生路径
│  └─ document_parser/markdown_ordered.py
│
├─ MinerU 路径
│  └─ document_parser/mineru_adapter.py
│
├─ RAGFlow / DeepDoc 路径
│  └─ document_parser/ragflow_format_adapter.py
│     ├─ PDF
│     ├─ DOCX
│     ├─ Excel/CSV
│     ├─ HTML/JSON/TXT/Markdown
│     ├─ EPUB/PPT/Image/Archive
│     └─ binary fallback
│
└─ 统一输出 ParsedDocument
```

## 7.2 主要 Python 文件

| 文件 | 作用 |
|---|---|
| `parser_service.py` | 解析器总路由；记录 requested/actual parser、fallback 是否发生及原因 |
| `document_parser/types.py` | 定义 `ParsedDocument` / `ParsedBlock`，是下游统一契约 |
| `document_parser/markdown_ordered.py` | Markdown 保序结构解析，避免标题、表格、代码顺序丢失 |
| `document_parser/mineru_adapter.py` | 调 MinerU API；定位返回内容；构建 block；保存图片；清理 MinerU 输出 |
| `document_parser/ragflow_format_adapter.py` | 多格式 dispatcher；将 Vendor 解析结果统一到 ParsedBlock |
| `document_parser/block_reflow.py` | 重排和修复解析块，为 Canonical/Chunker 提供更稳定输入 |

### 解析层维护边界

```text
解析器差异
→ 在 Adapter 内消化
→ 统一成 ParsedDocument / ParsedBlock
→ 下游不感知 MinerU 或 RAGFlow 的原始返回结构
```

---

# 8. 主链路三：Old Structure First 父子切分

## 8.1 当前核心调用树

```text
chunk_service.rebuild_document_chunks()
│
├─ runtime_config_from_db(db)
│  └─ 读取 DB ChunkerConfig
│
└─ enable_parent_child = true
   │
   └─ _rebuild_canonical_weknora_parent_child()
      │
      ├─ canonical_document.build_canonical_document()
      │  ├─ ParsedBlock 按原顺序统一成 Canonical Markdown
      │  ├─ Table 转 Markdown/HTML 可用表达
      │  ├─ Image 转 asset:// 引用
      │  └─ CanonicalAsset 放 sidecar
      │     └─ MinerU caption / footnote / OCR / VLM / page / bbox 进入图片语义
      │
      ├─ canonical_parent_child.build_canonical_parent_child_plan()
      │  │
      │  ├─ normalize_canonical_markdown()
      │  │  ├─ 超长 fenced code 安全切分
      │  │  └─ 超长 Markdown table 保表头切分
      │  │
      │  ├─ figure_linker.link_figure_references()
      │  │  ├─ 排除 asset marker、围栏/内联代码和裸 caption
      │  │  ├─ 唯一图号 / page 唯一 / section 唯一时确定绑定
      │  │  ├─ 重复图号无法唯一确定时保留 ambiguous 候选
      │  │  └─ 保存 page/section/距离/P1 依据与 heuristic_rule_score
      │  │
      │  ├─ parent_child_splitter.split_parent_child_result()
      │  │  └─ weknora_splitter.split_parent_child()
      │  │     ├─ 第一阶段：Parent 级切分
      │  │     ├─ 第二阶段：Child 级切分
      │  │     ├─ heading / heuristic / recursive 策略选择
      │  │     └─ overlap / resequence / validation
      │  │
      │  ├─ _apply_heading_context_to_source_children()
      │  ├─ _materialize_source_child()
      │  │  ├─ text
      │  │  ├─ table
      │  │  └─ image / asset
      │  ├─ deduplicate_overlap_shadow_children()
      │  └─ _flatten_underfilled_parent_links()
      │     └─ 只做 Parent 0/1 最终完整性约束
      │
      ├─ ORM Materialization
      │  ├─ parent_text
      │  └─ child chunks
      │
      ├─ _enforce_parent_child_integrity()
      ├─ _link_neighbors()
      ├─ _finalize_chunk_skip_flags()
      ├─ _replace_canonical_artifact()
      │  └─ Canonical Markdown / source nodes / assets / figure evidence
      ├─ 与 Chunk 在调用方同一事务 DB flush
      └─ keyword_index_service.index_document_chunks()
```

## 8.2 Old Structure First 的核心原则

### 原则 1：先保结构，再优化长度

当前生产计划**不做通用短兄弟块合并**。只有来源范围确实重叠、且右块由左块精确后缀加少量新尾句组成时，才合并 overlap 短尾。其他短块可能是：

- 标题后的定义；
- 列表项；
- 代码前导语；
- 图表说明；
- 有独立语义的结论句。

因此不再为了“块长更好看”而主动破坏文档边界。

### 原则 2：Parent 是上下文容器，Child 是召回单元

```text
Parent
├─ Child A   ← Embedding / BM25 / Retrieval
├─ Child B   ← Embedding / BM25 / Retrieval
└─ Child C   ← Embedding / BM25 / Retrieval
```

- `parent_text` 不做 Embedding；
- 检索先召回 Child；
- 需要上下文时再回到 Parent/兄弟块扩展。

### 原则 3：保护不可随意断开的结构

`protected_patterns.py` 负责保护或识别：

- fenced code；
- Markdown table；
- HTML；
- LaTeX；
- Markdown link/image；
- entity；
- 目录树/文件列表等结构型文本。

## 8.3 父子切分主要 Python 文件与职责

| 顺序 | Python 文件 | 核心函数 | 作用 | 修改风险 |
|---|---|---|---|---|
| 1 | `chunk_service.py` | `rebuild_document_chunks()` | runtime dispatcher；决定走父子还是兼容分支 | **极高** |
| 2 | `chunker_config_service.py` | `runtime_config_from_db()` | DB 配置 → 运行时 ChunkerConfig | **极高** |
| 3 | `document_parser/canonical_document.py` | `build_canonical_document()` | 统一结构源文档，避免解析器差异污染切分器 | **极高** |
| 4 | `chunker/canonical_parent_child.py` | `build_canonical_parent_child_plan()` | 当前生产父子计划总入口 | **极高** |
| 5 | `chunker/parent_child_splitter.py` | `split_parent_child_result()` | 配置与结果模型适配 | 高 |
| 6 | `chunker/weknora_splitter.py` | `split_parent_child()` | Old 两阶段边界算法核心 | **极高** |
| 7 | `chunker/protected_patterns.py` | `find_protected_spans()` 等 | 防止代码/表格/公式/结构文本被错误切断 | **极高** |
| 8 | `chunk_service.py` | `_rebuild_canonical_weknora_parent_child()` | Plan → ORM Chunk | **极高** |
| 9 | `chunk_service.py` | `_enforce_parent_child_integrity()` | 保证 Parent 链接合法 | 高 |
| 10 | `keyword_index_service.py` | `index_document_chunks()` | 将最终 Child 同步到 ES | 高 |

## 8.4 当前保留但不应误认成生产逻辑的函数

| 代码 | 当前状态 |
|---|---|
| `chunk_service._rebuild_markdown_weknora_parent_child()` | 保留实现；当前 dispatcher 不调用 |
| `chunk_service._rebuild_document_chunks_structured_parent_child()` | 保留的旧结构化父子实现；当前 dispatcher 不调用 |
| `chunking.rebuild_service.ChunkRebuildService` | 模块化重构实现；当前 dispatcher 不调用 |
| `canonical_parent_child.merge_short_text_siblings()` | Helper 保留；生产 plan 有意不调用 |

---

# 9. 主链路四：非父子兼容切分

```text
rebuild_document_chunks()
└─ enable_parent_child = false
   │
   ├─ 遍历 ParsedBlock
   │
   ├─ text
   │  └─ chunker.service.split_document()
   │
   ├─ table
   │  └─ 直接物化 table Chunk
   │
   └─ image
      ├─ OCR 文本足够长 → image_ocr_text Chunk
      └─ 图片本身 → image Chunk

→ _post_process_orphan_short_text_chunks()
→ _link_neighbors()
→ _finalize_chunk_skip_flags()
→ DB
→ ES BM25
```

这条链路目前仍是有效 runtime 分支，但不是当前 Old Structure First 优化的核心对象。

---

# 10. 主链路五：Embedding 与向量存储

## 10.1 调用树

```text
POST /documents/{document_id}/embedding
│
├─ api/routes_documents.py
│  └─ embed_document()
│
└─ services/embedding_service.py
   └─ embed_document()
      │
      ├─ 查询 eligible chunks
      │  ├─ text
      │  ├─ table
      │  ├─ image
      │  ├─ image_ocr_text
      │  └─ equation
      │
      ├─ embedding/embedding_content_builder.py
      │  └─ build_embedding_content()
      │
      ├─ embed_text()
      │  ├─ Ollama
      │  └─ OpenAI-compatible API
      │
      ├─ _upsert_embedding()
      │  └─ PostgreSQL chunk_embeddings  【真值记录】
      │
      └─ optional external mirror
         └─ vector_store_adapters.upsert_external_vector()
            ├─ Qdrant
            └─ Milvus
```

## 10.2 关键规则

1. `parent_text` 不向量化。
2. 不是所有存在的 Chunk 都必须向量化；低信息块、占位块可跳过。
3. 真正送给 Embedding 模型的内容由 `build_embedding_content()` 决定，而不是简单使用 `chunk.content`。
4. 普通正文向量化前移除展示型 HTML 标签并保留内文；`script/style/noscript/svg` 内容丢弃；围栏代码和 `content_kind=code_block` 的 XML/配置标签保留。
5. PostgreSQL `chunk_embeddings` 是本地状态判断和回退检索的重要依据。
6. 外部向量库是 mirror/search provider，不应破坏 PostgreSQL 侧文档与 Chunk 的关系真值。

## 10.3 主要 Python 文件

| 文件 | 作用 |
|---|---|
| `api/routes_documents.py` | 单文档和批量向量化 HTTP 入口 |
| `embedding_service.py` | Embedding provider 调用、批量处理、状态统计、写入 |
| `embedding/embedding_content_builder.py` | 构造语义 Embedding 输入；这是效果调优核心 |
| `vector_store_adapters.py` | Qdrant/Milvus upsert 和 search |
| `vector_store_service.py` | 向量库配置 CRUD/测试 |

---

# 11. 主链路六：Elasticsearch BM25 索引

## 11.1 入库链路

```text
Chunk rebuild 完成
→ DB add / flush
→ keyword_index_service.index_document_chunks()
→ 只索引可检索的非 parent_text Chunk
→ Elasticsearch
```

## 11.2 搜索链路

```text
retrieval_service.keyword_search()
→ keyword_index_service.search_bm25()
→ prepare_bm25_query() 清理并判断短/长查询
→ build_bm25_search_body() 选择普通 + ngram 或仅普通字段
→ too_many_nested_clauses 时仅一次安全查询重试
→ Elasticsearch BM25
→ Keyword Hit
→ score normalize
→ retrieval result
```

短查询保留 ngram 召回，长查询压缩为有限词项且禁止使用 ngram。安全重试仍超过子句限制时，
Hybrid 已成功的向量结果继续返回；关键词单检索返回明确错误。如果 ES 连接、认证等非复杂度
错误发生，只有在配置显式允许时才走 SQL `ILIKE` fallback。

## 11.3 关键文件

| 文件 | 作用 |
|---|---|
| `keyword_index_service.py` | ES index schema、Chunk → ES doc、批量索引、删除、BM25 查询、重建索引 |
| `main.py` | FastAPI startup 时可初始化 ES index |
| `chunk_service.py` | Chunk rebuild 后触发 ES 同步 |
| `retrieval_service.py` | 调用 BM25 并融合结果 |
| `tests/test_keyword_index_query_safety.py` | 长查询压缩、DSL、安全重试、映射和 Hybrid 降级回归 |
| `backend/scripts/rebuild_keyword_index.py` | 运维时重建关键词索引 |

---

# 12. 主链路七：Hybrid Retrieval

## 12.1 总链路树

```text
GET/POST /api/v1/knowledge-bases/{kb_id}/search
│
├─ api/routes_retrieval.py
│  └─ hybrid_search_get() / hybrid_search_post()
│
└─ services/retrieval_service.py
   └─ hybrid_search()
      │
      ├─ normalize_options()
      │
      ├─ Vector branch
      │  └─ vector_search()
      │     ├─ query embedding
      │     ├─ external vector store 优先（如配置）
      │     └─ PostgreSQL pgvector fallback/本地搜索
      │
      ├─ Keyword branch
      │  └─ keyword_search()
      │     └─ Elasticsearch BM25
      │
      ├─ merge_results()
      │  ├─ merge_results_rrf()      【默认重点】
      │  └─ merge_results_weighted()
      │
      ├─ attach_context()            【可选】
      │  └─ Parent preview / sibling summaries
      │
      ├─ build_parent_groups()       【可选】
      │  └─ 按 parent 聚合 Child hits
      │
      └─ return results + parent_groups + meta
```

## 12.2 检索的核心数据模型

```text
Child Chunk
├─ 是精确召回单元
├─ 有 embedding
└─ 有 BM25 文档

Parent Text
├─ 是上下文容器
├─ 不直接 embedding
└─ 由命中的 Child 反查和扩展
```

## 12.3 主要 Python 文件与作用

| 顺序 | Python 文件 | 核心函数 | 作用 |
|---|---|---|---|
| 1 | `api/routes_retrieval.py` | `hybrid_search_*()` | HTTP 入口 |
| 2 | `retrieval_service.py` | `normalize_options()` | 合并请求参数与 DB 检索配置 |
| 3 | `retrieval_service.py` | `vector_search()` | 向量召回 |
| 4 | `embedding_service.py` | `embed_text()` | Query 向量化 |
| 5 | `vector_store_adapters.py` | `search_external_vector()` | 外部向量库召回 |
| 6 | `retrieval_service.py` | `keyword_search()` | BM25 召回 |
| 7 | `keyword_index_service.py` | `search_bm25()` | Elasticsearch 查询 |
| 8 | `retrieval_service.py` | `merge_results_rrf()` | 按排名进行融合 |
| 9 | `retrieval_service.py` | `attach_context()` | 回填 Parent/邻居上下文 |
| 10 | `retrieval_service.py` | `build_parent_groups()` | 以 Parent 为单位聚合证据 |
| 11 | `retrieval_service.py` | `hybrid_search()` | 整体 orchestration |

---

# 13. 主链路八：统一 LLM Context

## 13.1 调用树

```text
POST /api/v1/knowledge-bases/{kb_id}/llm-context
│
├─ api/routes_retrieval.py
│  └─ llm_context_post()
│
├─ retrieval_service.py
│  └─ build_llm_context()              # 兼容壳
│
└─ context/knowledge_context_service.py
   └─ KnowledgeContextService.build_for_knowledge_base()
      │
      ├─ _normalize_payload()
      │  ├─ include_context = true
      │  ├─ include_parent_context = true
      │  ├─ merge_hits_by_parent = true
      │  └─ return_mode = chunks_with_selective_context
      │
      ├─ retrieval_service.hybrid_search()
      │
      └─ 按 return_mode 构建结果
         ├─ chunks
         │  └─ llm_context_builder.build_cloud_flat_chunks_payload()
         ├─ chunks_with_selective_context   【默认】
         │  └─ build_cloud_flat_chunks_payload()
         ├─ debug
         │  ├─ cloud flat chunks
         │  └─ legacy builder 附加调试字段
         └─ legacy / fallback
            └─ build_llm_context_payload()
```

## 13.2 默认返回模式

默认：`chunks_with_selective_context`

目标不是直接拼成一整段 Prompt，而是返回云端 Agent 更容易控制的扁平证据：

```text
chunks[]
├─ 直接命中的 hit chunks
└─ 按规则补充的 context chunks
   ├─ parent 内相邻文本
   └─ 关联 media chunks
```

当前 schema 标识为：

```text
cloud_flat_chunks.v4.selective_context
```

## 13.3 主要 Python 文件

| 文件 | 作用 |
|---|---|
| `api/routes_retrieval.py` | `/llm-context` HTTP 入口 |
| `retrieval_service.py` | `build_llm_context()` 兼容调用壳；底层仍负责真正检索 |
| `context/knowledge_context_service.py` | FastAPI/MCP/未来 Chat 的统一 Context Facade |
| `llm_context_builder.py` | 将检索结果变成 flat chunks 或 legacy context |

### 为什么 `KnowledgeContextService` 重要

旧结构容易变成：

```text
FastAPI 自己调 retrieval
MCP 自己调 retrieval
Chat 自己调 retrieval
```

当前目标是：

```text
FastAPI ─┐
MCP ─────┼─→ KnowledgeContextService → Retrieval → Context Builder
Chat ────┘
```

后续新的调用方应优先复用该服务，而不是复制一套检索拼接逻辑。

---

# 14. 主链路九：MCP → Know_RAG Context

## 14.1 总链路

```text
Claude / Codex / 其他 MCP Client
│
└─ integrations/mcp/server.py
   └─ know_rag_search_context
      │
      └─ integrations/mcp/tools.py
         └─ search_context()
            │
            └─ integrations/mcp/http_client.py
               └─ POST /api/v1/knowledge-bases/{kb_id}/llm-context
                  │
                  └─ KnowledgeContextService
                     └─ Hybrid Retrieval
                        └─ Cloud Flat Chunks
```

## 14.2 主要 Python 文件

| 文件 | 作用 |
|---|---|
| `integrations/mcp/server.py` | 创建 FastMCP；声明暴露给 MCP Client 的工具 |
| `integrations/mcp/tools.py` | 参数校验和映射；保持薄适配层 |
| `integrations/mcp/http_client.py` | 登录、Token 管理、后端 HTTP 调用、401 重试 |
| `context/knowledge_context_service.py` | MCP 最终复用的统一上下文业务 |
| `retrieval_service.py` | 实际检索 |
| `llm_context_builder.py` | MCP 返回的 `chunks[]` 结构生成 |

### MCP 维护原则

1. 检索策略改动：改 `retrieval_service.py`，不要在 MCP 里复制。
2. Context 返回结构改动：改 `knowledge_context_service.py` / `llm_context_builder.py`。
3. MCP 只负责协议适配和参数暴露。
4. MCP 不直接查 DB。

---

# 15. 文档删除与数据清理链路

```text
DELETE document
→ document_service.delete_document()
├─ delete_document_objects()
│  └─ 删除 MinIO 中该文档的全部对象
├─ delete_document_from_index()          # best-effort
│  └─ 删除 Elasticsearch BM25 文档数据
├─ DB 删除 Chunk + Document
│  └─ ChunkEmbedding 依赖 ORM/数据库级联删除
├─ 更新 KnowledgeBase document/chunk 统计
└─ delete_document_cache()               # best-effort
   └─ 清理本地解析缓存
```

### 当前需要特别关注的清理边界

当前 `delete_document()` 中**没有看到显式删除 Qdrant/Milvus 外部向量记录的调用**。因此如果部署环境启用了外部向量库，后续应重点验证：

```text
删除文档 / 重解析文档
→ PostgreSQL 已删除
→ ES 已删除
→ MinIO 已删除
→ 外部向量库是否仍残留旧 chunk id ？
```

维护删除逻辑时必须同时考虑：

```text
PostgreSQL
MinIO
Elasticsearch
外部向量库
本地解析缓存
```

新增一种存储后，应同步检查删除和重解析逻辑是否需要补清理。

---

# 16. 主要链路 Python 文件总表

> 后续排障可先从这张表定位。

| 领域 | 入口文件 | 核心文件 | 最关键职责 |
|---|---|---|---|
| 应用启动 | `main.py` | `core/config.py` | 路由、启动初始化、全局配置 |
| 上传 | `routes_documents.py` | `document_service.py` | 文件入库、Document 创建、解析任务 |
| 对象存储 | `document_service.py` | `storage/minio_storage.py` | 源文件和媒体对象生命周期 |
| 解析任务 | `document_service.py` | `parse_task_service.py` | 后台任务状态、阶段事件 |
| 解析路由 | `document_service.py` | `parser_service.py` | 选择 MinerU/RAGFlow/Markdown |
| MinerU | `parser_service.py` | `document_parser/mineru_adapter.py` | MinerU API 和结果归一化 |
| 多格式解析 | `parser_service.py` | `document_parser/ragflow_format_adapter.py` | 格式 dispatcher |
| OCR/VLM | `document_service.py` | `vision_service.py` | 图片块语义增强 |
| Block 重排 | `document_service.py` | `document_parser/block_reflow.py` | OCR 修复、结构重排、标题上下文 |
| Canonical | `chunk_service.py` | `document_parser/canonical_document.py` | 统一 Markdown + asset sidecar |
| 切分 dispatcher | `document_service.py` | `chunk_service.py` | 清旧数据、选择分支、物化、持久化 |
| Old Structure First | `chunk_service.py` | `chunker/canonical_parent_child.py` | 生产父子计划 |
| Parent/Child 算法 | `canonical_parent_child.py` | `chunker/weknora_splitter.py` | 两阶段边界选择 |
| 结构保护 | `canonical_parent_child.py` | `chunker/protected_patterns.py` | 防止结构内容断裂 |
| Embedding | `routes_documents.py` | `embedding_service.py` | 生成和存储向量 |
| Embedding 内容 | `embedding_service.py` | `embedding/embedding_content_builder.py` | 决定模型真正看到的语义文本 |
| 外部向量库 | `retrieval_service.py` | `vector_store_adapters.py` | Qdrant/Milvus |
| BM25 | `chunk_service.py` / `retrieval_service.py` | `keyword_index_service.py` | ES 建索引和查询 |
| Hybrid Retrieval | `routes_retrieval.py` | `retrieval_service.py` | 向量+BM25+RRF+上下文 |
| Unified Context | `routes_retrieval.py` | `context/knowledge_context_service.py` | 调用方统一 Facade |
| Cloud Chunks | `knowledge_context_service.py` | `llm_context_builder.py` | selective context flat chunks |
| MCP | `integrations/mcp/server.py` | `tools.py` + `http_client.py` | MCP → HTTP API 适配 |

---

# 17. 按“要改什么”快速定位文件

| 需求/问题 | 第一修改位置 | 第二检查位置 |
|---|---|---|
| 上传参数不对 | `api/routes_documents.py` | `schemas/document.py` |
| 上传后没启动解析 | `document_service.upload_document()` | `parse_task_service.py` |
| 解析器选错 | `parser_service.py` | Document metadata / ModelConfig |
| MinerU 返回适配错误 | `document_parser/mineru_adapter.py` | MinerU 实际 payload |
| Markdown 顺序/表格错 | `document_parser/markdown_ordered.py` | `block_reflow.py` |
| OCR 行碎裂 | `document_parser/block_reflow.py` | `vision_service.py` |
| 图片没有描述 | `vision_service.py` | `model_config_service.py` |
| Parent/Child 根本没启用 | DB `ChunkerConfig` | `chunker_config_service.py` |
| 普通文本切坏 | `chunker/canonical_parent_child.py` | `weknora_splitter.py` |
| 英文 token/URL/长行被切断 | `protected_patterns.py` / `boundary_utils.py` | `canonical_parent_child.py` |
| 代码块切坏 | `canonical_parent_child.py` | `protected_patterns.py` |
| 表格切坏 | `canonical_parent_child.py` | `canonical_document.py` |
| 标题上下文丢失 | `_apply_heading_context_to_source_children()` | `block_reflow.py` |
| 短块太多 | 先确认是否真是语义问题 | **不要直接恢复自动 merge_short_text_siblings** |
| Parent 关系异常 | `_enforce_parent_child_integrity()` | `_flatten_underfilled_parent_links()` |
| Embedding 效果差 | `embedding_content_builder.py` | `embedding_service.py` |
| 文档显示未向量化 | `embedding_service.py` 状态统计 | `ChunkEmbedding` 数据 |
| 外部向量库查不到 | `vector_store_adapters.py` | `VectorStoreConfig` |
| BM25 查不到 | `keyword_index_service.py` | ES index 是否同步 |
| Hybrid 排名不合理 | `merge_results_rrf()` | RetrievalConfig |
| Parent 上下文太长/太短 | `attach_context()` / `build_parent_groups()` | retrieval config |
| MCP 返回结构不对 | `llm_context_builder.py` | `knowledge_context_service.py` |
| MCP 能连但查不到数据 | `http_client.py` | 后端 `/llm-context` 接口 |
| 删除后残留文件 | `document_service.delete_document()` | MinIO/ES/外部向量库清理 |

---

# 18. 容易误判的代码与维护陷阱

## 18.1 `services/chunking/` 不是当前切分 dispatcher

存在测试 ≠ 已接管生产主链路。

当前真实入口仍是：

```text
chunk_service.rebuild_document_chunks()
```

而不是：

```text
ChunkRebuildService.rebuild()
```

后续若要正式迁移，必须显式修改 dispatcher，并做回归测试后再更新本文。

## 18.2 不要把旧父子函数当成当前 Canonical 主链路

当前父子分支：

```text
_rebuild_canonical_weknora_parent_child()
```

以下函数虽然存在，但当前 dispatcher 不调用：

```text
_rebuild_markdown_weknora_parent_child()
_rebuild_document_chunks_structured_parent_child()
```

## 18.3 不要看到短块就自动合并

`merge_short_text_siblings()` 当前保留但不进入生产 plan，原因是单纯追求“没有短块”会损害文档结构。生产只定向修复有来源范围重叠和精确文本重叠证据的短尾。

处理短块问题时先分类：

```text
真实语义短块        → 保留
结构噪声            → Canonical/quality gate 清理
错误断句            → 修边界算法
纯 overlap shadow   → 精确去重
overlap 加少量新尾句 → 定向合并回前块
重复 asset marker   → 按 asset_id 只物化一次
错误 Parent 关系    → integrity 修复
```

## 18.4 `mcp_placeholder` 不是实际 MCP

```text
api/routes_mcp_placeholder.py
services/mcp_placeholder_service.py
```

是历史占位。

真实 MCP：

```text
integrations/mcp/
```

## 18.5 `ragflow_vendor/` 不应随意修改

它是 DeepDoc 解析链路的运行时 Vendor 依赖。正常业务修复优先落在
`backend/app/services/` 内；只有上游同步或明确的兼容修复才修改 Vendor。

未接入运行时的 WeKnora Python 参考副本已经移除；算法差异通过上游仓库和 Git 历史追溯，
不再在生产仓库维护第二份实现。

---

# 19. 测试目录与回归范围

## 19.1 当前测试重点分组

```text
backend/tests/
├─ test_old_structure_first_optimization.py       # 当前策略总回归
├─ test_canonical_parent_child_unified.py         # Canonical 父子统一链路
├─ test_canonical_parent_child_integration.py     # 集成行为
├─ test_canonical_pipeline_convergence.py         # Pipeline 收敛
├─ test_canonical_overlap_shadow_dedup.py         # overlap shadow 去重
├─ test_figure_linker.py                           # P2 正文图号提取、消歧与生产计划接入
├─ test_canonical_short_sibling_merge.py          # 短兄弟 helper 行为（不代表生产调用）
├─ test_chunk_boundary_quality.py                 # 边界质量
├─ test_context_header_continuation.py            # 标题上下文继承
├─ test_markdown_code_context_merge.py            # 代码上下文
├─ test_markdown_ordered_tables.py                # Markdown 表格
├─ test_markdown_table_pipeline.py                # 表格主链路
├─ test_chunker.py                                # 基础切分
├─ test_chunker_structure.py                      # 结构切分
│
├─ test_embedding_content.py                      # Embedding 内容
├─ test_embedding_structure_content.py            # 结构文本 Embedding
│
├─ test_hybrid_result_mapping.py                  # Hybrid 结果映射
├─ test_retrieval_mapping.py                      # Retrieval 映射
├─ test_rrf_fusion.py                             # RRF 融合
├─ test_keyword_index_query_safety.py             # BM25 查询安全、单次重试与 Hybrid 异常隔离
│
├─ test_mineru_ocr.py                             # MinerU OCR
├─ test_mineru_parser_engine.py                   # MinerU parser routing
├─ test_parse_quality_fix.py                      # 解析质量修复
└─ test_block_reflow_structure.py                 # Block Reflow 结构
```

## 19.2 修改后最低回归建议

### 修改 Canonical/父子切分

至少关注：

```text
test_old_structure_first_optimization.py
test_canonical_parent_child_unified.py
test_canonical_parent_child_integration.py
test_canonical_overlap_shadow_dedup.py
test_chunk_boundary_quality.py
test_context_header_continuation.py
```

### 修改 Markdown/代码/表格

```text
test_markdown_ordered_tables.py
test_markdown_table_pipeline.py
test_markdown_code_context_merge.py
```

### 修改 Embedding

```text
test_embedding_content.py
test_embedding_structure_content.py
```

### 修改 Retrieval

```text
test_hybrid_result_mapping.py
test_retrieval_mapping.py
test_rrf_fusion.py
```

> 本手册基于静态代码调用关系整理，不代表本次生成文档时已经执行完整测试套件。

---

# 20. 运维与部署相关目录

```text
docker-compose.yml
├─ backend
├─ frontend
├─ mcp
├─ gateway
├─ postgres / pgvector
├─ qdrant
├─ redis
├─ elasticsearch
├─ minio
├─ mineru-cpu
└─ mineru-gpu


docker/
├─ mineru/                               # MinerU 镜像/启动配置
└─ nginx/                                # Nginx
```

### 后端维护脚本

```text
backend/scripts/
├─ check_parent_child_chunks.py          # 检查父子 Chunk 结果
└─ rebuild_keyword_index.py              # 重建 ES 关键词索引
```

---

# 21. 文档维护优先级

`docs/` 只保留 Current 文档和仍有效的专项运维说明。历史设计、Phase 进度和一次性修复报告
通过 Git 历史追溯，避免与运行时代码形成第二套事实来源。

推荐阅读优先级：

```text
1. README.md / docs/index.md             # 当前入口和权威顺序
2. AGENTS.md                             # AI/工程修改边界与验证命令
3. docs/architecture.md / security.md    # 当前架构与安全边界
4. 本手册                               # 当前代码结构和真实主链路
5. Git 历史                             # 追溯旧设计和已完成修复
```

历史文档与代码冲突时：

```text
真实 dispatcher 调用关系
> 当前测试
> 标记为 Current 的专项说明
> Git 历史
```

---

# 22. 推荐的后续维护规范

## 22.1 每次主链路修改后更新本手册

至少检查：

```text
[ ] 入口函数是否变化
[ ] runtime dispatcher 是否变化
[ ] 新增/删除 Python 文件
[ ] DB 配置是否新增
[ ] 真实生产分支是否改变
[ ] 返回 schema 是否改变
[ ] MCP 是否仍复用统一 Context API
[ ] 删除/重解析是否仍能清理所有存储
[ ] 对应测试是否新增或失效
[ ] `docs/index.md` 指向的 Current 文档是否同步
[ ] `ruff check app tests` 是否通过
[ ] `python -m pytest tests/` 是否通过
[ ] `npm run check` 是否通过
[ ] API 是否意外返回完整向量、密钥或本地绝对路径
```

### 22.1.1 仓库生成物边界

以下内容不作为源码维护：

```text
.env
frontend/node_modules/
frontend/dist/
cache/
__pycache__/
.pytest_cache/
coverage/
```

生成目录已从工作树清理并由 `.gitignore` 排除。`.env` 保留为本地运行配置；若历史提交包含
真实凭据，仍需停止跟踪并执行凭据轮换。

## 22.2 新模块接管旧主链路的判定标准

只有满足以下条件，本文才把模块标成 `[主链路]`：

```text
1. HTTP route / task entry 可以追到它；
2. dispatcher 真实调用它；
3. 不是仅有 import、测试或未调用 helper；
4. 生产配置下存在可达路径。
```

## 22.3 当前项目最应该保持清晰的五层边界

```text
解析层
ParsedDocument / ParsedBlock
        ↓
Canonical 层
Canonical Markdown + Asset Sidecar
        ↓
切分层
Parent/Child Plan
        ↓
存储/索引层
PostgreSQL + ES + Vector Store + MinIO
        ↓
检索/上下文层
Hybrid Retrieval + KnowledgeContextService + MCP
```

只要这五层边界不再次混在一个大文件中，项目后续维护成本会明显低很多。

---

# 23. 最终主链路总图

```text
【文档入库】
Frontend
→ routes_documents.py
→ document_service.py
→ MinIO
→ ParseTask
→ parser_service.py
→ MinerU / RAGFlow / Markdown
→ ParsedDocument / ParsedBlock
→ vision_service.py
→ block_reflow.py
→ chunk_service.rebuild_document_chunks()

    ├─ enable_parent_child=true
    │  → canonical_document.py
    │  → canonical_parent_child.py
    │  → parent_child_splitter.py
    │  → weknora_splitter.py
    │  → Parent/Child Chunk
    │
    └─ enable_parent_child=false
       → split_document / direct block materialization
       → Compatibility Chunks

→ PostgreSQL Chunks
→ Elasticsearch BM25
→ Optional Embedding
→ PostgreSQL ChunkEmbedding
→ Optional Qdrant / Milvus


【查询与 Agent 使用】
Client / MCP
→ routes_retrieval.py
→ KnowledgeContextService
→ retrieval_service.hybrid_search()
    ├─ Vector Search
    └─ BM25 Search
→ RRF / Weighted Fusion
→ Child Hits
→ Parent Context / Parent Groups
→ llm_context_builder.py
→ cloud_flat_chunks.v4.selective_context
→ FastAPI / MCP / Future Chat Agent
```

---

## 结论

当前 Know_RAG 已经形成了相对清晰的核心闭环：

```text
多格式解析
→ 统一 ParsedDocument
→ Canonical Markdown
→ Old Structure First Parent/Child
→ 多路索引
→ Hybrid Retrieval
→ Selective Context
→ MCP / Agent
```

后续维护最关键的是始终区分三件事：

1. **文件存在**不等于**当前运行时会调用**；
2. **历史重构方案**不等于**当前 dispatcher 已切换**；
3. **代码支持某分支**不等于**部署环境当前配置已启用**。

本手册应持续以真实调用关系维护，而不是仅根据目录名称或历史文档维护。
