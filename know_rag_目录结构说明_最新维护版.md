# Know_RAG 项目目录结构与文件功能说明

> 来源：原始项目目录说明，并结合当前最新 `Old Structure First` 后端代码维护更新。
> 说明：以下目录树排除了 `.git/`、`cache/`、`backend/.venv/`、`frontend/node_modules/`、`frontend/dist/`、`__pycache__/`、`.pytest_cache/` 等生成物或依赖目录，聚焦项目源码、配置、脚本、文档和测试文件。

## 总体结构概览

- `backend/`：FastAPI 后端，当前父子切分采用 `Old Structure First`：保留 Old two-pass Parent/Child 结构骨架，在 Canonical Markdown + Asset Sidecar 上完成标题上下文传播、结构化 materialization、精确 overlap 去重和 Parent 完整性校验。
- `frontend/`：Vue3/Vite 前端，包含知识库、文档、chunk、检索调试、配置等页面。
- `docker/`：MinerU 与 Nginx Gateway 的容器配置。
- `docs/`：架构、部署、数据库、检索、MinIO、MinerU、MCP 等文档。
- `backend/ragflow_vendor/`：DeepDoc/RAGFlow 兼容代码，主要为解析能力提供本地依赖。

## 详细目录树

```text
know_rag/ — 项目根目录，包含 Docker 编排、后端、前端、文档与辅助说明。
├── .claude/ — Claude Code / AI 辅助开发相关本地配置与技能。
│   ├── skills/ — 本地技能目录。
│   │   └── karpathy-guidelines/ — 用于 Claude Code 的编码风格/工程规范技能。
│   │       └── SKILL.md — Claude Code 技能说明，用于约束 AI 辅助编码风格。
│   └── settings.local.json — Claude Code 本地设置。
├── backend/ — FastAPI 后端工程，负责认证、知识库、文档解析、chunk 构建、向量化、检索、MCP 对接。
│   ├── app/ — 后端应用主包。
│   │   ├── api/ — FastAPI 路由层，负责 HTTP API 入参校验与服务调用。
│   │   │   ├── __init__.py — API 路由包初始化。
│   │   │   ├── routes_auth.py — 认证接口，处理登录、注册、验证码/令牌校验与当前用户信息。
│   │   │   ├── routes_chat.py — 聊天接口，调用 chat_service 完成基于知识库的对话请求。
│   │   │   ├── routes_chunker_config.py — 切分配置接口，读取/更新父子块、chunk size、overlap 等运行时切分参数。
│   │   │   ├── routes_chunks.py — chunk 管理接口，支持列表、详情、树形结构、启用/禁用、删除和向量化状态展示。
│   │   │   ├── routes_documents.py — 文档接口，支持上传、列表、详情、解析、删除、批量向量化和批量状态查询。
│   │   │   ├── routes_faq.py — FAQ 管理接口。
│   │   │   ├── routes_files.py — 文件/媒体资源访问接口，用于回显 MinIO 或本地缓存中的图片资源。
│   │   │   ├── routes_initialization.py — 系统初始化接口，用于初始化管理员或基础配置。
│   │   │   ├── routes_knowledge_base.py — 知识库 CRUD 接口。
│   │   │   ├── routes_mcp_placeholder.py — MCP 预留/占位接口。
│   │   │   ├── routes_model_config.py — 模型配置接口，维护 embedding、LLM、VLM 等模型提供商与参数。
│   │   │   ├── routes_parse_tasks.py — 解析任务接口，查询解析任务状态、事件流和进度。
│   │   │   ├── routes_retrieval.py — 检索接口，提供 hybrid search、LLM context/flat chunks 等召回能力。
│   │   │   └── routes_vector_store.py — 向量库配置接口，维护 pgvector/qdrant/milvus 等向量存储配置。
│   │   ├── core/ — 后端核心基础设施，包括配置、日志、响应结构、安全认证。
│   │   │   ├── __init__.py — core 包初始化。
│   │   │   ├── config.py — 集中读取环境变量和运行时配置，包括数据库、模型、存储、公网 URL 等。
│   │   │   ├── logging.py — 日志格式和日志级别配置。
│   │   │   ├── response.py — 统一 API 响应结构 success_response/error_response。
│   │   │   └── security.py — JWT、密码哈希、当前用户依赖和鉴权工具。
│   │   ├── db/ — 数据库模型、连接会话与 Alembic 迁移。
│   │   │   ├── migrations/ — Alembic 数据库迁移目录。
│   │   │   │   ├── versions/ — 按阶段维护的数据库版本迁移脚本。
│   │   │   │   │   ├── 0001_initial.py — Alembic 迁移脚本，用于创建或升级数据库表结构。
│   │   │   │   │   ├── 0002_documents_chunks_phase3.py — Alembic 迁移脚本，用于创建或升级数据库表结构。
│   │   │   │   │   ├── 0003_embedding_retrieval_phase4.py — Alembic 迁移脚本，用于创建或升级数据库表结构。
│   │   │   │   │   ├── 0004_ensure_chunk_embeddings.py — Alembic 迁移脚本，用于创建或升级数据库表结构。
│   │   │   │   │   ├── 0005_weknora_alignment_phase_a.py — Alembic 迁移脚本，用于创建或升级数据库表结构。
│   │   │   │   │   ├── 0006_chunker_config.py — Alembic 迁移脚本，用于创建或升级数据库表结构。
│   │   │   │   │   ├── 0007_retrieval_rrf_phase_d.py — Alembic 迁移脚本，用于创建或升级数据库表结构。
│   │   │   │   │   ├── 0008_parse_workbench_phase_z.py — Alembic 迁移脚本，用于创建或升级数据库表结构。
│   │   │   │   │   └── 0009_parse_eval_plugin_phase_aa.py — Alembic 迁移脚本，用于创建或升级数据库表结构。
│   │   │   │   ├── env.py — Alembic 迁移运行环境，加载模型和数据库连接。
│   │   │   │   ├── README.md — Alembic 迁移目录说明。
│   │   │   │   └── script.py.mako — Alembic 新迁移脚本模板。
│   │   │   ├── __init__.py — db 包初始化。
│   │   │   ├── models.py — SQLAlchemy ORM 模型，定义 users、knowledge_bases、documents、chunks、chunk_embeddings、配置表等。
│   │   │   └── session.py — 数据库 engine、SessionLocal 和 get_db 依赖。
│   │   ├── integrations/ — 外部集成模块。
│   │   │   ├── mcp/ — MCP Server 集成，向云端 Agent 暴露 Know_RAG 检索工具。
│   │   │   │   ├── __init__.py — MCP 包初始化。
│   │   │   │   ├── http_client.py — MCP 服务调用 backend API 的 HTTP Client。
│   │   │   │   ├── http_server.py — MCP HTTP/Streamable HTTP 服务入口。
│   │   │   │   ├── README.md — MCP 集成说明。
│   │   │   │   ├── schemas.py — MCP 工具入参/出参 schema。
│   │   │   │   ├── server.py — MCP Server 注册、生命周期与工具暴露。
│   │   │   │   └── tools.py — MCP 工具实现，主要封装 Know_RAG 检索、flat chunks 返回和接口调用。
│   │   │   └── __init__.py — Python 源文件。 
│   │   ├── plugins/ — 插件式扩展功能目录。
│   │   │   └── parse_eval/ — 解析质量评估插件，用于对解析结果进行诊断、报告和前端展示。
│   │   │       ├── __init__.py — parse_eval 插件包初始化。
│   │   │       ├── diagnostics.py — 解析结果诊断规则，发现空块、短块、图片/表格问题等。
│   │   │       ├── models.py — 解析评估插件数据模型。
│   │   │       ├── README.md — 解析评估插件说明。
│   │   │       ├── report_builder.py — 解析质量评估报告构建。
│   │   │       ├── router.py — parse_eval 插件 API 路由。
│   │   │       ├── service.py — 解析评估任务服务逻辑。
│   │   │       └── templates.py — 评估报告模板和输出格式。
│   │   ├── schemas/ — Pydantic 请求/响应数据结构定义。
│   │   │   ├── __init__.py — schemas 包初始化。
│   │   │   ├── auth.py — 认证相关请求/响应模型。
│   │   │   ├── chat.py — 聊天请求/响应模型。
│   │   │   ├── chunk.py — chunk 列表、详情、树形结构和向量状态响应模型。
│   │   │   ├── chunker_config.py — 切分配置请求/响应模型。
│   │   │   ├── document.py — 文档上传、解析、状态和详情响应模型。
│   │   │   ├── faq.py — FAQ 请求/响应模型。
│   │   │   ├── knowledge_base.py — 知识库 CRUD 请求/响应模型。
│   │   │   ├── mcp_placeholder.py — MCP 占位功能请求/响应模型。
│   │   │   ├── model_config.py — 模型配置请求/响应模型。
│   │   │   ├── retrieval.py — 检索、父子上下文、LLM context、flat chunks 等响应模型。
│   │   │   └── vector_store.py — 向量库配置请求/响应模型。
│   │   ├── scripts/ — 后端启动或维护辅助脚本。
│   │   │   ├── __init__.py — 脚本包初始化。
│   │   │   └── bootstrap_deepdoc_models.py — 启动时准备/下载 DeepDoc 模型资源。
│   │   ├── services/ — 后端核心业务服务层。
│   │   │   ├── chunker/ — 通用文本切分器，负责标题、递归、Markdown、父子块等基础切分算法。
│   │   │   │   ├── __init__.py — chunker 包初始化，暴露 split_document 等统一入口。
│   │   │   │   ├── boundary_utils.py — 切分边界工具，处理标点开头、非法边界和边界修正。
│   │   │   │   ├── canonical_parent_child.py — 当前父子切分计划核心：基于 Canonical Markdown 调用 Old two-pass Parent/Child Skeleton，传播全局标题上下文，完成 Text/Table/Image materialization、精确 overlap 影子去重和 0/1 Child Parent 完整性校验；生产主链路不再自动执行 Short Sibling Merge。
│   │   │   │   ├── chunk_postprocess.py — 切分后处理，合并短块、清理 skip 标记、修复最终 chunk metadata。
│   │   │   │   ├── heading_splitter.py — 标题感知切分器，按标题层级构造 heading_path 和章节边界。
│   │   │   │   ├── heading_utils.py — 标题、caption、页眉噪声、章节路径、continuation header 等识别工具。
│   │   │   │   ├── heuristic_splitter.py — 启发式切分器，按段落、句子和长度进行基础分割。
│   │   │   │   ├── markdown_splitter.py — Markdown 切分器，识别标题、列表、代码块、孤立短块并做结构保持。
│   │   │   │   ├── models.py — 切分器配置与中间结构数据模型。
│   │   │   │   ├── parent_child_splitter.py — Old two-pass Parent/Child Skeleton 入口：先生成 Parent Candidate，再在 Parent 内切 Child，决定稳定的父子结构骨架。
│   │   │   │   ├── protected_patterns.py — 保护 fenced code、Markdown/HTML table、HTML pre/code、inline HTML、Markdown link/image、LaTeX 与 HTML entity；当前已增强 `<sup>/<sub>` 和 inline/block LaTeX 保护。
│   │   │   │   ├── recursive_splitter.py — 递归文本切分器，按分隔符优先级和安全边界生成子块。
│   │   │   │   ├── service.py — 统一切分器入口，根据配置选择 heading/recursive/legacy 等模式。
│   │   │   │   ├── text_unit.py — 文本单元抽象和句段级处理工具。
│   │   │   │   └── weknora_splitter.py — 底层父子候选切分能力与历史参考实现，由父子切分层间接使用；当前不是独立主链路。
│   │   │   ├── chunking/ — 结构化 chunk 构建流水线，负责 section、parent、child、图片/表格、持久化与短块后处理。
│   │   │   │   ├── __init__.py — chunking 包初始化。
│   │   │   │   ├── child_builder.py — 构建 child chunk 草稿，处理 text/table/image/equation 等子块内容、短块与 skip 规则。
│   │   │   │   ├── chunk_draft.py — ParentDraft、ChildDraft、ChunkDraft 等 chunk 草稿数据结构。
│   │   │   │   ├── chunk_linker.py — 为同一文档内 chunk 建立前后关系、parent-child 关系和顺序索引。
│   │   │   │   ├── chunk_persistence.py — 将 chunk 草稿写入数据库，负责 metadata 清理、skip 标记兜底和 chunk_embeddings 清理。
│   │   │   │   ├── code_context_merger.py — 代码块与上下文短说明合并逻辑，避免代码说明被拆成孤立 chunk。
│   │   │   │   ├── image_chunk_builder.py — 图片 chunk 构建逻辑，绑定 image_url、caption/OCR/VLM 描述和相邻短说明。
│   │   │   │   ├── metadata_builder.py — chunk metadata 构造器，统一写入 page、heading、block_type、content_kind 等字段。
│   │   │   │   ├── parent_builder.py — 父块构造逻辑，生成 parent_text 内容和 parent metadata。
│   │   │   │   ├── rebuild_service.py — 结构化父子 chunk 重建流水线，协调 section、parent、child、link、persist。
│   │   │   │   ├── section_builder.py — 从 ParsedBlock 构建结构化 section，识别标题、噪声、图片、表格、公式和上下文路径。
│   │   │   │   └── table_chunk_builder.py — 表格 chunk 构建逻辑，保留 table_html/table_markdown 并生成可检索文本。
│   │   │   ├── context/ — 知识上下文/引用上下文构建服务。
│   │   │   │   ├── __init__.py — context 包初始化。
│   │   │   │   ├── knowledge_context_service.py — 知识上下文服务，构建基于检索结果的上下文对象。
│   │   │   │   └── schemas.py — 上下文构建相关数据结构。
│   │   │   ├── document_parser/ — 文档解析适配层，接入 MinerU、DeepDoc/RAGFlow、本地 DOCX/通用解析等。
│   │   │   │   ├── __init__.py — document_parser 包初始化。
│   │   │   │   ├── block_reflow.py — 解析后 block 重排与结构修复，优化标题、正文、图表说明等顺序。
│   │   │   │   ├── canonical_document.py — 父子切分前统一中间层，将 ParsedDocument/ParsedBlock 转换为 Canonical Markdown + Asset Sidecar；负责标题/表格/图片引用规范化、Parser Noise Gate、连续页码噪声过滤及图片 OCR/VLM/page/bbox 资产保存。
│   │   │   │   ├── deepdoc_local_ocr.py — DeepDoc 本地 OCR 调用适配。
│   │   │   │   ├── deepdoc_runtime.py — DeepDoc 模型目录、运行时准备与健康检查。
│   │   │   │   ├── docx_multimodal.py — DOCX 多模态解析，提取文本、图片和表格。
│   │   │   │   ├── generic_parsers.py — 通用文件解析器，处理纯文本、CSV 等基础格式。
│   │   │   ├── markdown_ordered.py — 原生保序 Markdown 解析器，按原始顺序识别 heading、text、table、code 和 HTML table，支持 EOF 无换行表格并避免表格被移动到文档末尾。
│   │   │   │   ├── media_storage.py — 解析过程中的图片/媒体裁剪文件存储与 URL 生成。
│   │   │   │   ├── mineru_adapter.py — MinerU HTTP/本地输出适配器，将 MinerU 结果转换为 ParsedDocument/ParsedBlock。
│   │   │   │   ├── pdf_ragflow_advanced.py — RAGFlow/DeepDoc 高级 PDF 解析适配。
│   │   │   │   ├── pdf_ragflow_lite.py — 轻量 PDF 解析适配。
│   │   │   │   ├── ragflow_deepdoc_adapter.py — RAGFlow DeepDoc 解析结果适配为项目内部 block 结构。
│   │   │   │   ├── ragflow_format_adapter.py — RAGFlow 格式化结果转换适配；Markdown 入口改为调用原生保序解析器，并修正 ragflow_vendor 目录定位。
│   │   │   │   └── types.py — ParsedDocument、ParsedBlock、ImageInfo 等解析结果类型定义。
│   │   │   ├── embedding/ — embedding 输入文本构造等向量化前置处理。
│   │   │   │   ├── __init__.py — embedding 子包初始化。
│   │   │   │   └── embedding_content_builder.py — 根据 chunk 类型和 metadata 构造最终送入 embedding 模型的文本，过滤低信息内容。
│   │   │   ├── ocr/ — OCR 文本修复、标识符规范化与模板化修复模块。
│   │   │   │   ├── templates/ — 面向特定文档类型的 OCR 修复模板。
│   │   │   │   │   ├── __init__.py — OCR 模板包初始化。
│   │   │   │   │   ├── base.py — OCR 文本修复模板基类和通用接口。
│   │   │   │   │   └── scanned_contract_ocr.py — 扫描合同类 OCR 修复模板。
│   │   │   │   ├── __init__.py — OCR 包初始化。
│   │   │   │   ├── identifier.py — OCR 文本中的编号、标识符、公式编号等识别和规范化。
│   │   │   │   └── text_repair.py — OCR 文本修复，包括错字、空格、换行和识别噪声处理。
│   │   │   ├── storage/ — 对象存储/文件存储适配层。
│   │   │   │   ├── __init__.py — storage 包初始化。
│   │   │   │   └── minio_storage.py — MinIO 对象存储封装，负责文件上传、删除和 public URL/relative path 生成。
│   │   │   ├── __init__.py — 后端业务服务模块。 
│   │   │   ├── audit_service.py — 审计日志服务，记录用户操作和系统事件。
│   │   │   ├── auth_service.py — 认证业务逻辑，处理用户注册、登录、密码校验和 token 生成。
│   │   │   ├── batch_vectorize_service.py — 批量向量化任务服务，支持批量处理、进度统计和状态查询。
│   │   │   ├── captcha_service.py — 验证码生成、缓存与校验服务。
│   │   │   ├── chat_service.py — 聊天业务服务，调用检索与模型生成回答。
│   │   │   ├── chunk_service.py — Chunk rebuild/persist 编排入口；父子模式调用 Canonical Document → Old Structure First 计划，非父子模式仍走兼容 Block Path，最终统一完成邻接关系、skip flags、数据库写入和索引。
│   │   │   ├── chunker_config_service.py — 切分配置服务，从数据库读取并转换为运行时 ChunkerConfig。
│   │   │   ├── content_cleaning.py — 内容清洗与低信息判断规则，负责 URL/HTML/页眉/短块/占位内容过滤与 skip metadata 清理。
│   │   │   ├── document_service.py — 文档上传、解析、删除、重新解析和文档状态维护核心服务。
│   │   │   ├── embedding_service.py — embedding 运行时选择、文本向量生成、chunk_embeddings 写入和外部向量库同步。
│   │   │   ├── faq_service.py — FAQ 业务逻辑。
│   │   │   ├── keyword_index_service.py — Elasticsearch/BM25 关键词索引写入、删除和检索服务。
│   │   │   ├── knowledge_base_service.py — 知识库 CRUD、权限校验和统计维护。
│   │   │   ├── llm_context_builder.py — 把 retrieval 结果转换为云端可消费的 flat chunks 或 legacy LLM context。
│   │   │   ├── mcp_placeholder_service.py — MCP 预留功能业务逻辑。
│   │   │   ├── model_config_service.py — 模型配置业务逻辑，维护 embedding/LLM/VLM 供应商、模型名、base_url 等。
│   │   │   ├── ollama_service.py — Ollama 本地模型服务封装。
│   │   │   ├── parse_task_service.py — 解析任务事件、状态、进度和日志维护服务。
│   │   │   ├── parser_service.py — 解析器选择与统一调用入口；Markdown 固定路由到 native_markdown，其他格式按配置选择 MinerU、DeepDoc/RAGFlow 等引擎。
│   │   │   ├── retrieval_service.py — 混合检索核心服务，执行 pgvector 向量检索、ES 关键词检索、RRF/加权融合和父子上下文组织。
│   │   │   ├── vector_store_adapters.py — 外部向量库适配器，封装 Qdrant/Milvus 等外部 provider 的 upsert/search。
│   │   │   ├── vector_store_service.py — 向量库配置业务服务。
│   │   │   └── vision_service.py — 视觉语言模型/VLM 调用封装，用于图片描述或图像理解。
│   │   ├── utils/ — 通用工具函数。
│   │   │   ├── __init__.py — utils 包初始化。
│   │   │   ├── files.py — 文件名、扩展名、目录创建和文件校验工具。
│   │   │   ├── ids.py — 统一 ID 生成工具。
│   │   │   ├── text.py — 文本清洗、JSON 安全化、Postgres NUL 字符清理等工具。
│   │   │   └── tokens.py — token 估算与文本长度预算工具。
│   │   ├── __init__.py — 后端 app 包初始化文件。
│   │   └── main.py — FastAPI 应用入口，注册中间件、异常处理、API 路由和 parse_eval 插件。
│   ├── ragflow_vendor/ — 从 RAGFlow/DeepDoc 引入的解析依赖适配代码。
│   │   ├── api/ — RAGFlow API 兼容桩代码。
│   │   │   ├── db/ — RAGFlow DB 服务兼容包。
│   │   │   │   ├── joint_services/ — 目录。
│   │   │   │   │   ├── __init__.py — RAGFlow 租户模型服务兼容桩。
│   │   │   │   │   └── tenant_model_service.py — RAGFlow 租户模型服务兼容桩。
│   │   │   │   ├── services/ — 目录。
│   │   │   │   │   ├── __init__.py — RAGFlow LLM 服务兼容桩。
│   │   │   │   │   └── llm_service.py — RAGFlow LLM 服务兼容桩。
│   │   │   │   └── __init__.py — RAGFlow API 兼容包初始化或占位。
│   │   │   └── __init__.py — RAGFlow API 兼容包初始化或占位。
│   │   ├── common/ — RAGFlow 公共配置、常量与工具。
│   │   │   ├── __init__.py — common 包初始化。
│   │   │   ├── config_utils.py — RAGFlow 配置工具兼容。
│   │   │   ├── connection_utils.py — 连接工具兼容。
│   │   │   ├── constants.py — RAGFlow 常量定义。
│   │   │   ├── file_utils.py — 文件工具兼容。
│   │   │   ├── misc_utils.py — 杂项工具兼容。
│   │   │   ├── settings.py — RAGFlow 设置兼容。
│   │   │   └── token_utils.py — token 工具兼容。
│   │   ├── deepdoc/ — DeepDoc 文档解析与视觉模型代码。
│   │   │   ├── parser/ — DeepDoc 各类文件解析器。
│   │   │   │   ├── resume/ — DeepDoc 简历解析相关模块。
│   │   │   │   │   ├── entities/ — 简历解析实体词表和识别规则。
│   │   │   │   │   │   ├── res/ — 简历实体识别资源文件。
│   │   │   │   │   │   │   ├── corp.tks.freq.json — DeepDoc 简历实体识别资源数据文件。
│   │   │   │   │   │   │   ├── corp_baike_len.csv — DeepDoc 简历实体识别资源数据文件。
│   │   │   │   │   │   │   ├── corp_tag.json — DeepDoc 简历实体识别资源数据文件。
│   │   │   │   │   │   │   ├── good_corp.json — DeepDoc 简历实体识别资源数据文件。
│   │   │   │   │   │   │   ├── good_sch.json — DeepDoc 简历实体识别资源数据文件。
│   │   │   │   │   │   │   ├── school.rank.csv — DeepDoc 简历实体识别资源数据文件。
│   │   │   │   │   │   │   └── schools.csv — DeepDoc 简历实体识别资源数据文件。
│   │   │   │   │   │   ├── __init__.py — 简历实体包初始化。
│   │   │   │   │   │   ├── corporations.py — 公司实体词典与识别规则。
│   │   │   │   │   │   ├── degrees.py — 学历/学位实体词典。
│   │   │   │   │   │   ├── industries.py — 行业实体词典。
│   │   │   │   │   │   ├── regions.py — 地区实体词典。
│   │   │   │   │   │   └── schools.py — 学校实体词典。
│   │   │   │   │   ├── __init__.py — 简历解析包初始化。
│   │   │   │   │   ├── step_one.py — 简历解析第一阶段规则。
│   │   │   │   │   └── step_two.py — 简历解析第二阶段规则。
│   │   │   │   ├── __init__.py — DeepDoc parser 包初始化。
│   │   │   │   ├── docling_parser.py — Docling 解析器适配。
│   │   │   │   ├── docx_parser.py — DOCX 解析器。
│   │   │   │   ├── epub_parser.py — EPUB 解析器。
│   │   │   │   ├── excel_parser.py — Excel 解析器。
│   │   │   │   ├── figure_parser.py — 图片/图表解析器。
│   │   │   │   ├── html_parser.py — HTML 解析器。
│   │   │   │   ├── json_parser.py — JSON 解析器。
│   │   │   │   ├── markdown_parser.py — Markdown 解析器。
│   │   │   │   ├── mineru_parser.py — MinerU 解析器适配。
│   │   │   │   ├── opendataloader_parser.py — OpenDataLoader 解析器适配。
│   │   │   │   ├── paddleocr_parser.py — PaddleOCR 解析器适配。
│   │   │   │   ├── pdf_parser.py — PDF 解析器。
│   │   │   │   ├── ppt_parser.py — PPT 解析器。
│   │   │   │   ├── tcadp_parser.py — TCADP 解析器。
│   │   │   │   ├── txt_parser.py — TXT 解析器。
│   │   │   │   └── utils.py — DeepDoc parser 工具函数。
│   │   │   ├── vision/ — DeepDoc OCR、版面识别、表格结构识别等视觉模块。
│   │   │   │   ├── __init__.py — DeepDoc vision 包初始化。
│   │   │   │   ├── dla_cli.py — 版面分析 CLI。
│   │   │   │   ├── layout_recognizer.py — 版面布局识别模型封装。
│   │   │   │   ├── ocr.py — OCR 调用与文本识别。
│   │   │   │   ├── operators.py — 视觉预处理/后处理算子。
│   │   │   │   ├── postprocess.py — 视觉识别后处理。
│   │   │   │   ├── recognizer.py — 通用视觉识别器基类。
│   │   │   │   ├── seeit.py — 可视化调试工具。
│   │   │   │   ├── t_ocr.py — OCR 测试入口。
│   │   │   │   ├── t_recognizer.py — recognizer 测试入口。
│   │   │   │   └── table_structure_recognizer.py — 表格结构识别模型封装。
│   │   │   ├── __init__.py — DeepDoc 包初始化。
│   │   │   ├── README.md — DeepDoc 说明文档。
│   │   │   ├── README_tr.md — DeepDoc 说明文档。
│   │   │   └── README_zh.md — DeepDoc 说明文档。
│   │   └── rag/ — RAGFlow RAG 相关兼容代码。
│   │       ├── app/ — RAGFlow 图片处理兼容模块。
│   │       │   ├── __init__.py — RAGFlow 图片处理兼容模块。
│   │       │   └── picture.py — RAGFlow 图片处理兼容模块。
│   │       ├── nlp/ — RAGFlow NLP 包占位。
│   │       │   └── __init__.py — RAGFlow RAG 兼容包初始化或占位。
│   │       ├── prompts/ — RAGFlow prompt 生成兼容模块。
│   │       │   ├── __init__.py — RAGFlow prompt 生成兼容模块。
│   │       │   └── generator.py — RAGFlow prompt 生成兼容模块。
│   │       ├── utils/ — RAGFlow RAG 工具函数。
│   │       │   ├── __init__.py — RAGFlow RAG 工具函数。
│   │       │   └── lazy_image.py — RAGFlow RAG 工具函数。
│   │       └── __init__.py — RAGFlow RAG 兼容包初始化或占位。
│   ├── scripts/ — 项目维护脚本。
│   │   ├── check_parent_child_chunks.py — 检查父子 chunk 结构和数据质量的脚本。
│   │   └── rebuild_keyword_index.py — 重建 Elasticsearch/BM25 关键词索引脚本。
│   ├── tests/ — 后端单元测试和回归测试。
│   │   ├── test_block_reflow_structure.py — 后端回归测试：验证 block reflow structure 相关逻辑。
│   │   ├── test_canonical_overlap_shadow_dedup.py — Canonical overlap 影子重复去重回归测试。
│   │   ├── test_canonical_parent_child_integration.py — Canonical 父子切分与 chunk_service 集成测试。
│   │   ├── test_canonical_parent_child_unified.py — Canonical 父子切分统一链路测试。
│   │   ├── test_canonical_pipeline_convergence.py — Canonical 噪声、Parent 收敛等历史问题回归测试。
│   │   ├── test_canonical_short_sibling_merge.py — Short Merge helper 的历史兼容/实验测试；生产主链路当前不调用该 helper。
│   │   ├── test_chunk_boundary_quality.py — 后端回归测试：验证 chunk boundary quality 相关逻辑。
│   │   ├── test_chunk_persistence_not_drop_all.py — 后端回归测试：验证 chunk persistence not drop all 相关逻辑。
│   │   ├── test_chunk_rebuild_fallback.py — 后端回归测试：验证 chunk rebuild fallback 相关逻辑。
│   │   ├── test_chunk_rebuild_not_empty.py — 后端回归测试：验证 chunk rebuild not empty 相关逻辑。
│   │   ├── test_chunker.py — 后端回归测试：验证 chunker 相关逻辑。
│   │   ├── test_chunker_structure.py — 后端回归测试：验证 chunker structure 相关逻辑。
│   │   ├── test_chunking_child_builder.py — 后端回归测试：验证 chunking child builder 相关逻辑。
│   │   ├── test_chunking_drafts.py — 后端回归测试：验证 chunking drafts 相关逻辑。
│   │   ├── test_chunking_linker.py — 后端回归测试：验证 chunking linker 相关逻辑。
│   │   ├── test_chunking_metadata_builder.py — 后端回归测试：验证 chunking metadata builder 相关逻辑。
│   │   ├── test_chunking_rebuild_service_smoke.py — 后端回归测试：验证 chunking rebuild service smoke 相关逻辑。
│   │   ├── test_context_header_continuation.py — 后端回归测试：验证 context header continuation 相关逻辑。
│   │   ├── test_document_chunk_store_metadata.py — 后端回归测试：验证 document chunk store metadata 相关逻辑。
│   │   ├── test_embedding_content.py — 后端回归测试：验证 embedding content 相关逻辑。
│   │   ├── test_embedding_structure_content.py — 后端回归测试：验证 embedding structure content 相关逻辑。
│   │   ├── test_hybrid_result_mapping.py — 后端回归测试：验证 hybrid result mapping 相关逻辑。
│   │   ├── test_markdown_ordered_tables.py — Markdown 保序表格解析回归测试，覆盖 EOF 无换行、多表格、代码块伪表格、无首尾竖线、HTML table、escaped pipe 等场景。
│   │   ├── test_markdown_table_pipeline.py — Markdown 表格端到端链路回归测试，验证 ParsedBlock 顺序、章节上下文以及 table chunk 生成。
│   │   ├── test_markdown_code_context_merge.py — 后端回归测试：验证 markdown code context merge 相关逻辑。
│   │   ├── test_mineru_ocr.py — 后端回归测试：验证 mineru ocr 相关逻辑。
│   │   ├── test_mineru_parser_engine.py — 后端回归测试：验证 mineru parser engine 相关逻辑。
│   │   ├── test_old_structure_first_optimization.py — Old Structure First 专项测试，验证完整短块不被生产链路强制合并、HTML/LaTeX 原子保护、超长代码单行不字符硬切及 Parent 完整性。
│   │   ├── test_orphan_short_chunks.py — 后端回归测试：验证 orphan short chunks 相关逻辑。
│   │   ├── test_parse_quality_fix.py — 后端回归测试：验证 parse quality fix 相关逻辑。
│   │   ├── test_retrieval_mapping.py — 后端回归测试：验证 retrieval mapping 相关逻辑。
│   │   ├── test_rrf_fusion.py — 后端回归测试：验证 rrf fusion 相关逻辑。
│   │   └── test_weknora_parent_child_reuse.py — WeKnora/父子切分参考链路回归测试。
│   ├── weknora_chunker_py/ — WeKnora 切分策略参考实现和对照代码。
│   │   ├── CODE_MAP.md — WeKnora 切分参考代码映射说明。
│   │   ├── example_usage.py — WeKnora 切分器参考用法示例。
│   │   ├── README.md — WeKnora 切分器参考实现说明。
│   │   └── weknora_chunker_reference.py — WeKnora 切分策略参考实现。
│   ├── alembic.ini — Alembic 数据库迁移配置。
│   ├── Dockerfile — 后端 Docker 镜像构建文件。
│   ├── entrypoint.sh — 后端容器启动入口，负责模型准备、迁移或服务启动前置动作。
│   ├── OLD_STRUCTURE_FIRST_OPTIMIZATION.md — 当前 Old Structure First 优化说明，记录生产主链路撤出 Short Merge、保留精确重复去重及增强结构保护的设计决策。
│   ├── pyproject.toml — Python 项目配置、依赖和工具配置。
│   ├── requirements.txt — 后端 pip 依赖列表。
│   └── uv.lock — uv 依赖锁定文件。
├── docker/ — Docker 相关构建配置。
│   ├── mineru/ — MinerU CPU 服务镜像构建与启动脚本。
│   │   ├── Dockerfile — MinerU CPU 解析服务 Docker 镜像构建文件。
│   │   └── entrypoint.sh — MinerU 容器启动脚本。
│   └── nginx/ — Nginx Gateway 配置。
│       └── know_rag_gateway.conf — Nginx Gateway 反向代理配置，统一代理 /api、/files、/mcp 等路径。
├── docs/ — 项目设计、部署、接口、数据库、检索和阶段文档。
│   ├── api.md — 后端 API 总览文档。
│   ├── architecture.md — 系统总体架构文档。
│   ├── backend.md — 后端模块说明。
│   ├── backend_module_map.md — 后端模块映射说明。
│   ├── backend_refactor_plan.md — 后端重构计划。
│   ├── database.md — 数据库表结构和设计说明。
│   ├── deployment.md — 部署说明。
│   ├── elasticsearch_hybrid_search.md — Elasticsearch BM25 与 hybrid search 方案说明。
│   ├── frontend.md — 前端结构说明。
│   ├── mcp_reserved.md — MCP 预留能力说明。
│   ├── mineru_docker.md — MinerU Docker 部署与使用说明。
│   ├── minio_storage.md — MinIO 存储方案说明。
│   ├── parse_quality_fix_readme.md — 解析质量修复说明。
│   ├── parse_quality_optimization_notes.md — 解析质量优化笔记。
│   ├── phase_g_multimodal_vector.md — 多模态向量阶段设计文档。
│   ├── phase_h_deepdoc_api_test.md — DeepDoc API 测试阶段文档。
│   ├── phase_n_full_format_upload.md — 多格式上传阶段文档。
│   ├── security.md — 安全设计说明。
│   └── weknora_alignment_report.md — 与 WeKnora 能力和链路对齐分析报告。
├── frontend/ — Vue3/Vite 前端工程。
│   ├── src/ — 前端源码目录。
│   │   ├── api/ — 前端 API 请求封装。
│   │   │   ├── auth.ts — 认证相关 API 封装。
│   │   │   ├── chunks.ts — chunk 列表、树、详情、启用/禁用、删除和向量状态 API 封装。
│   │   │   ├── client.ts — Axios/Fetch 基础客户端、token 注入和错误处理。
│   │   │   ├── config.ts — 前端 API base URL 和运行时配置。
│   │   │   ├── documents.ts — 文档上传、解析、删除、批量向量化和状态查询 API。
│   │   │   ├── knowledgeBases.ts — 知识库 CRUD、详情统计和配置相关 API。
│   │   │   ├── parseTasks.ts — 解析任务事件、进度和状态 API。
│   │   │   └── retrieval.ts — 检索调试、搜索、LLM context/flat chunks API。
│   │   ├── components/ — 前端通用组件。
│   │   │   ├── common/ — 通用 UI 小组件。
│   │   │   │   ├── EmptyState.vue — 空状态组件。
│   │   │   │   ├── PageHeader.vue — 页面头部组件。
│   │   │   │   ├── StatCard.vue — 统计卡片组件。
│   │   │   │   └── StatusBadge.vue — 状态标签组件。
│   │   │   └── README.md — 前端组件目录说明。
│   │   ├── layouts/ — 页面布局组件。
│   │   │   └── MainLayout.vue — 主布局组件，承载侧边栏/顶部导航/内容区域。
│   │   ├── plugins/ — 前端插件模块。
│   │   │   └── parse-eval/ — 解析质量评估插件前端页面和 API。
│   │   │       ├── api.ts — 解析评估插件 API 封装。
│   │   │       ├── eval.css — 解析评估插件样式。
│   │   │       ├── ParseEvalDocumentView.vue — 解析评估文档详情页。
│   │   │       ├── ParseEvalHomeView.vue — 解析评估首页。
│   │   │       ├── ParseEvalRunView.vue — 解析评估运行详情页。
│   │   │       └── routes.ts — 解析评估插件路由定义。
│   │   ├── router/ — Vue Router 路由配置。
│   │   │   └── index.ts — Vue Router 路由表，注册知识库、文档、chunk、配置、解析评估等页面。
│   │   ├── styles/ — 前端基础样式、组件样式和设计变量。
│   │   │   ├── base.css — 基础 reset 和全局布局样式。
│   │   │   ├── components.css — 通用组件样式。
│   │   │   └── tokens.css — 颜色、间距、字体等设计变量。
│   │   ├── views/ — 业务页面视图。
│   │   │   ├── ChunkDetailView.vue — chunk 详情页，展示内容、metadata、图片预览和向量状态。
│   │   │   ├── ChunkerConfigView.vue — 切分配置页，配置父子块大小、overlap、模式、保护规则等。
│   │   │   ├── ChunkListView.vue — chunk 列表/树形页，支持类型筛选、向量化状态筛选、规则跳过原因和图片预览。
│   │   │   ├── DashboardView.vue — 系统首页/仪表盘。
│   │   │   ├── DocumentDetailView.vue — 文档详情页，展示解析状态、chunk/向量统计、重新解析和向量化操作。
│   │   │   ├── DocumentListView.vue — 文档列表页。
│   │   │   ├── DocumentUploadView.vue — 文档上传页，支持解析模式、OCR/VLM、MinerU profile、批量上传等配置。
│   │   │   ├── KnowledgeBaseDetailView.vue — 知识库详情页，展示文档、chunk、向量化统计和操作入口。
│   │   │   ├── KnowledgeBaseFormView.vue — 知识库创建/编辑表单页。
│   │   │   ├── KnowledgeBaseListView.vue — 知识库列表页。
│   │   │   ├── LoginView.vue — 登录页，包含账号密码和验证码交互。
│   │   │   ├── ModelConfigView.vue — 模型配置页，维护 embedding/LLM/VLM 模型供应商与参数。
│   │   │   ├── ParseWorkbenchView.vue — 解析工作台，测试和对比文档解析质量。
│   │   │   ├── PlaceholderView.vue — 占位页面，用于未实现或预留路由。
│   │   │   ├── RetrievalConfigView.vue — 检索配置页，配置向量/关键词召回、RRF、top_k、阈值等。
│   │   │   ├── SearchDebugView.vue — 检索调试页，展示 hybrid search、命中 chunk、父子上下文和返回结构。
│   │   │   └── VectorStoreConfigView.vue — 向量库配置页，预留 pgvector/qdrant/milvus 等配置入口。
│   │   ├── App.vue — 前端根组件。
│   │   ├── main.ts — Vue 应用入口，挂载 App、Router 和全局样式。
│   │   └── style.css — 全局样式入口。
│   ├── Dockerfile — 前端 Docker 镜像构建文件。
│   ├── index.html — Vite 前端 HTML 入口。
│   ├── package-lock.json — 前端 npm 依赖锁定文件。
│   ├── package.json — 前端 npm 依赖、脚本和项目信息。
│   ├── tsconfig.json — TypeScript 编译配置。
│   └── vite.config.ts — Vite 构建与开发服务器配置。
├── .env — 本地运行环境变量，包含数据库、模型、对象存储、公网地址等配置。
├── .env.example — 环境变量模板，用于复制生成本地 .env。
├── docker-compose.override.yml.example — Compose override 示例，用于本地覆盖服务端口或环境变量。
├── docker-compose.yml — 项目 Docker Compose 编排，定义 backend、frontend、postgres、redis、es、minio、mcp、gateway、mineru 等服务。
├── know_rag_architecture_analysis.md — Know_RAG 架构分析文档。
├── know_rag_parse_quality_optimization_plan.md — 解析质量优化方案文档。
├── mcp接口.md — MCP 接口说明文档。
├── README.md — 项目总览与启动说明。
├── repair.md — 修复记录或问题处理说明。
└── TODO.md — 待办事项记录。
```

## 当前最新主链路：Old Structure First

当前父子切分不再以“消灭所有短块”为目标，而是优先保证内容、结构和标题上下文稳定。真实运行路径为：

```text
Document API
→ document_service.py
→ parser_service.py
→ ParsedDocument / ParsedBlock
→ OCR / VLM Enhancement
→ Block Reflow
→ rebuild_document_chunks()
→ build_canonical_document()
→ Canonical Markdown + Asset Sidecar
→ build_canonical_parent_child_plan()
→ Old Two-pass Parent/Child Skeleton
→ Global Heading Context
→ Text / Table / Image Materialization
→ Exact Overlap Shadow Dedup
→ Parent 0/1 Integrity Validation
→ Link Neighbors
→ Persist
→ Elasticsearch BM25 / Embedding
→ Hybrid Retrieval
→ LLM Context / MCP
```

### 1. Canonical Document

```text
ParsedDocument / ParsedBlock
→ canonical_document.py
→ Canonical Markdown + Asset Sidecar
```

Canonical Markdown 保存标题、正文、表格、代码和 `asset://...` 图片位置；Asset Sidecar 保存图片 URL、OCR、VLM、page、bbox 等多模态信息。

同时执行：

```text
Parser Noise Gate
header / footer / page_number 过滤
解析器连续数字噪声过滤
```

因此 `526 / 524 / 117` 等页面噪声不应进入有效 Child；原生 Markdown 中合法数字仍保留。

### 2. Old Two-pass Parent/Child Skeleton

```text
Canonical Markdown
→ parent_child_splitter.py
→ Parent Candidate
→ Parent 内 Child Split
```

结构骨架先确定，再做局部 materialization 和完整性处理。当前不再使用 Text Window Rebalance、Safe Unit Rebalance、Recursive Semantic Split、Tail Rebalance 或 Hybrid Pipeline 重写整份文档结构。

### 3. Global Heading Context

```text
_apply_heading_context_to_source_children()
```

在完整 source children 上传播 heading context，避免章节跨 Parent 后丢失标题路径。

### 4. Materialization

Source Child 按内容转为：

```text
text
table
image
code-like text
```

图片通过：

```text
asset://image-id
→ Asset Sidecar
→ caption + OCR + VLM + image_url + page + bbox
```

构建 Image Child。

### 5. 精确重复去重

当前保留：

```text
deduplicate_overlap_shadow_children()
```

只删除满足真实 source span 重叠、相邻、边界严格包含且无独立语义的 overlap 影子块。

当前生产主链路明确不执行：

```text
merge_short_text_siblings()
```

因此：

```text
重复块 → 删除
噪声块 → 删除
结构损坏 → 修复
有意义短块 → 保留
```

### 6. Parent 完整性

最终：

```text
Parent 0 Child → 删除 Parent
Parent 1 Child → 删除 Parent，Child standalone
Parent >= 2 Child → 保留
```

该阶段只调整 Parent 引用，不重新切 Child 内容。

### 7. Markdown 专项链路

Markdown 继续使用原生保序解析：

```text
.md / .markdown
→ native_markdown
→ markdown_ordered.py
→ heading / text / table / code / HTML table 原位输出
→ Block Reflow
→ Canonical Document
→ Old Structure First Parent/Child
```

已保留以下修复：

- EOF 无换行表格识别；
- 多表格不再移动到文末；
- 标题、正文、表格保持原始顺序；
- 代码块伪表格不误识别；
- 表格与章节上下文保持对应。

### 8. 当前非父子兼容链路

当：

```text
enable_parent_child = false
```

仍走：

```text
ParsedBlock
→ block_type 分支
→ split_document()
→ text/table/image/equation build
→ orphan short-text postprocess
→ Persist
```

因此 `app/services/chunking/` 和部分旧 `chunker/` helper 当前仍有运行时引用，不能仅因为父子主链路不用就直接删除。

## 关键链路文件速查

### 文档解析到 Chunk 入库

- `backend/app/services/document_service.py`：上传、解析、重解析、删除和文档状态管理；负责串联 Parser、OCR/VLM、Reflow 与 Chunk rebuild。
- `backend/app/services/parser_service.py`：Parser 路由入口；Markdown 固定走 `native_markdown`，其他格式按配置选择 MinerU、DeepDoc/RAGFlow 等。
- `backend/app/services/document_parser/markdown_ordered.py`：Markdown 保序解析，保证 heading/text/table/code/HTML table 原位输出。
- `backend/app/services/document_parser/canonical_document.py`：将 ParsedDocument/ParsedBlock 转为 Canonical Markdown + Asset Sidecar，并执行 Parser Noise Gate。
- `backend/app/services/chunker/canonical_parent_child.py`：当前父子切分计划核心；串联 Old two-pass Skeleton、全局标题上下文、materialization、精确 overlap 去重和 Parent 完整性校验。
- `backend/app/services/chunker/parent_child_splitter.py`：Old two-pass Parent/Child Skeleton。
- `backend/app/services/chunker/protected_patterns.py`：保护 code/table/HTML/LaTeX/link/image/entity 等结构。
- `backend/app/services/chunk_service.py`：Chunk rebuild 与 persist 编排入口；父子模式走 Canonical/Old Structure First，非父子模式走兼容 Block Path。

### 当前生产父子链路

```text
build_canonical_document()
→ build_canonical_parent_child_plan()
→ split_parent_child_result()
→ _apply_heading_context_to_source_children()
→ _materialize_source_child()
→ deduplicate_overlap_shadow_children()
→ _flatten_underfilled_parent_links()
→ Persist
```

当前生产链路不调用：

```text
merge_short_text_siblings()
```

### 向量化与检索

- `backend/app/services/embedding/embedding_content_builder.py`：根据 Chunk 类型和 metadata 构造 embedding 输入。
- `backend/app/services/embedding_service.py`：生成向量、写入 `chunk_embeddings`，并同步可选外部向量库。
- `backend/app/services/keyword_index_service.py`：Elasticsearch/BM25 索引写入、删除和检索。
- `backend/app/services/retrieval_service.py`：向量检索 + BM25 + RRF/加权融合 + Parent 上下文组织。
- `backend/app/services/context/knowledge_context_service.py`：构建检索后的知识上下文对象。
- `backend/app/services/llm_context_builder.py`：将检索结果转换为 flat chunks 或 legacy LLM context。

### MCP 与公网 Agent 对接

- `backend/app/integrations/mcp/server.py`：MCP Server 注册和生命周期。
- `backend/app/integrations/mcp/http_server.py`：MCP HTTP/Streamable HTTP 服务入口。
- `backend/app/integrations/mcp/tools.py`：检索类 MCP 工具实现。
- `backend/app/integrations/mcp/http_client.py`：MCP 服务调用 Know_RAG Backend Retrieval API 的客户端。
- `docker/nginx/know_rag_gateway.conf`：统一代理 `/api`、`/files`、`/mcp`。

### 前端主要页面

- `frontend/src/views/KnowledgeBaseDetailView.vue`：知识库详情、文档/Chunk/向量统计。
- `frontend/src/views/DocumentDetailView.vue`：文档状态、重新解析、向量化。
- `frontend/src/views/ChunkListView.vue`：Chunk 列表/树形展示与类型筛选。
- `frontend/src/views/DocumentUploadView.vue`：上传与 Parser/OCR/VLM 配置。
- `frontend/src/views/SearchDebugView.vue`：Hybrid Search 和 Parent Context 调试。
- `frontend/src/views/ChunkerConfigView.vue`：Chunk size、overlap、父子模式等配置。
- `frontend/src/views/ModelConfigView.vue`：Embedding/LLM/VLM 配置。
- `frontend/src/views/VectorStoreConfigView.vue`：pgvector/Qdrant/Milvus 配置入口。

## 当前技术债务与维护说明

### 1. 历史 WeKnora 命名仍存在

当前函数和 metadata 中仍可能存在：

```text
_rebuild_canonical_weknora_parent_child()
canonical_weknora_parent_child
canonical_markdown_weknora_two_pass
```

但当前真实策略已经是：

```text
Old Structure First
```

建议后续单独做纯命名重构，不与切分逻辑修改同时进行。

### 2. `app/services/chunking/` 当前不可直接删除

虽然它不是 `enable_parent_child=true` 时的最终主链路，但 `chunk_service.py` 仍引用其中部分 helper，且非父子模式仍依赖旧 Block Path。

删除前必须完成：

```text
runtime import audit
非父子模式验证
测试迁移
```

### 3. `backend/weknora_chunker_py/` 是清理候选

当前更接近参考实现/历史对照代码。若确认无文档、开发调试和运行时依赖，可后续删除。

### 4. Short Merge Helper 仍保留但生产不调用

```text
merge_short_text_siblings()
```

目前仅用于历史兼容、实验或旧测试。后续可选择：

```text
保留为实验工具
```

或：

```text
连同过时测试一起删除
```

但当前不影响生产结果。

## 当前维护原则

以后调整切分链路时，优先顺序固定为：

```text
1. 是否丢内容
2. 是否破坏标题结构
3. 是否破坏图片/表格/代码/公式
4. 是否产生确定性重复
5. 是否产生非法 Parent
6. 是否影响检索
7. 最后才检查短块比例
```

不再因为：

```text
Chunk < 120
```

就默认增加自动合并规则。
