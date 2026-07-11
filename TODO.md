# TODO

## Phase 1 - 可运行骨架

- [x] FastAPI 后端、Vue + Vite 前端、Docker Compose。
- [x] `/health` 和统一响应结构。
- [x] MCP placeholder 和基础文档。

## Phase 2 - 认证与持久化

- [x] Alembic migration。
- [x] 管理员登录、JWT、`/auth/me`。
- [x] 知识库 CRUD 持久化。
- [x] retrieval/model/vector-store 配置持久化。

## Phase 3 - 文档与切片

- [x] 文档上传、本地落盘、documents 入库。
- [x] txt/md/pdf/docx 基础解析。
- [x] parse_status 状态流转。
- [x] chunks 表真实读写。
- [x] 前端文档和切片页面接入真实接口。

## Phase 4 - Embedding 与真实检索

- [x] 调用本地 Ollama embedding。
- [x] 使用 pgvector 保存 chunk 向量。
- [x] 文档级向量生成与状态查询。
- [x] 知识库级批量向量生成与状态查询。
- [x] embedding test 真实实现。
- [x] hybrid search 真实实现：向量召回 + 关键词召回。
- [x] 本地检索调试页面接入真实接口。
- [x] 修复 PDF 解析文本 NUL 字符清洗和 chunk 入库事务回滚。

## WeKnora 对齐

- [x] Phase A：数据模型兼容字段补齐。
- [x] Phase B：结构感知 chunker。
- [ ] Phase C：embedding 内容与索引表进一步对齐。
- [ ] Phase D：hybrid-search RRF 与返回结构进一步对齐。

## Phase 5 - 本地 RAG 问答

- [ ] 会话持久化。
- [ ] 消息持久化。
- [ ] 根据检索结果构造 context pack。
- [ ] 调用本地 Ollama LLM。
- [ ] 返回答案和引用来源。
- [ ] 本地问答调试页面接入真实接口。
## WeKnora 对齐 Phase C

- [x] embedding 内容改为文档标题 + 上下文标题 + 切片正文。
- [x] `chunk_embeddings.content` 保存实际索引文本。
- [x] `source_id/source_type/knowledge_id/is_enabled` 参与向量生成和检索过滤。
- [x] embedding status 增加 `has_context_header_count` 和 `indexed_content_count`。
- [x] hybrid-search 返回补充 WeKnora-compatible 字段。
- [x] 本地检索调试页、模型配置页、文档详情页和切片详情页完成 Phase C 展示增强。

下一步 Phase D：hybrid-search RRF 与返回结构进一步对齐。
## Phase B/C 修复项

- [x] 前端切片列表显示切片向量化状态。
- [x] 新增切分配置页面，支持父子分块开关。
- [x] 新增切分配置接口，支持动态调整 chunk size / overlap / separators。
- [x] 文档上传和重新解析时读取数据库中的切分配置。
## WeKnora 对齐 Phase D

- [x] hybrid-search 默认改为 RRF 融合。
- [x] 保留 weighted 加权分数兼容模式。
- [x] 支持 WeKnora 风格检索参数。
- [x] 返回 `rrf_score`、`vector_rank`、`keyword_rank`。
- [x] 返回 parent / previous / next 上下文预览。
- [x] 本地检索调试页支持 RRF 参数和上下文预览。

下一步 Phase 5：本地 RAG 问答，会话、消息、context pack、调用本地 LLM、引用来源。

## Phase E - 文档处理增强

- [x] 新增 ParsedDocument / ParsedBlock，统一解析输出结构。
- [x] DOCX 支持按原始顺序解析 text / image / table。
- [x] DOCX 内嵌图片保存到本地 storage 并生成 image chunk。
- [x] DOCX 表格转 HTML/文本并生成 table chunk。
- [x] PDF 新增 RAGFlow-lite 解析链路：pdfplumber 文本/表格、pypdf 回退、内嵌图片提取、扫描页图片兜底。
- [x] chunk 入库支持 text/table/image。
- [x] embedding / hybrid search 扩展支持 table/image chunk。
- [x] 新增本地 `/files` 路由预览解析图片。
- [x] 模型配置页增加 Image2Text / OCR API 配置。
- [ ] 接入真实 OCR API，对扫描页 image chunk 生成文本。
- [ ] 接入更完整的 PDF layout/table/figure 检测模型。
- [ ] 前端文档详情页增加解析块统计和图片列表。

## Phase F/G/H/I/J/K/L/M/N - 多模态、DeepDoc、模型配置与全格式支持

- [x] PDF 处理升级为 RAGFlow-style：LayoutRecognizer、TSR、XGBoost 纵向合并、图表裁剪、Caption 绑定、图文上下文绑定。
- [x] Docker 启动自动准备 DeepDoc 模型包，默认加载 `/data/models/deepdoc`。
- [x] 接入 DeepDoc ONNX Layout、TSR、XGBoost 模型文件。
- [x] 接入 DeepDoc 本地 OCR 模型资源 `det.onnx`、`rec.onnx`、`ocr.res`。
- [x] OCR / Image2Text / Embedding / LLM 配置改为 WeKnora-style 供应商、Base URL、模型名、API Key 管理。
- [x] 模型配置页增加真实 API 测试、模型列表刷新、本地 Ollama 模型检测和配置保存提示。
- [x] 向量库支持 PostgreSQL/pgvector、Qdrant、Milvus/Zilliz 切换。
- [x] 文档上传扩展为 PDF、Word、PPT、Excel、CSV、Markdown、HTML、JSON、EPUB、图片、ZIP 等常见格式。
- [x] 图片文件可直接上传入库，生成 image chunk，并支持 OCR/VLM 增强。
- [x] 修复 XGBoost 特征维度不一致导致的报错刷屏。

## Phase O - Chunk 内容、向量展示与前端管理体验修复

- [x] 修复 image chunk 只把 OCR/VLM 信息放入 metadata、没有成为主索引内容的问题。
- [x] table chunk 生成索引内容时合并 caption、上下文、表格文本和 HTML 纯文本。
- [x] image chunk 生成索引内容时合并 caption、VLM 描述、OCR 文本、上下文和文件名。
- [x] embedding 内容构造时统一读取 text/table/image 的语义内容，旧 image/table chunk 即使未重新解析，也能在重新向量化时把 OCR/VLM/table 信息纳入 embedding。
- [x] chunk API 列表默认只返回预览，不再把全文和完整向量塞进列表响应。
- [x] chunk API 详情返回向量维度、向量范数、向量预览和完整向量，便于排查向量化结果。
- [x] 切片列表页增加分页、每页 20/50/100、类型筛选、向量化状态筛选。
- [x] 切片列表页改成卡片式管理界面，避免大段内容撑满表格。
- [x] 切片详情页增加图片预览、表格 HTML 预览、索引文本、向量预览/完整向量展示。
- [x] 前端样式向 WeKnora 管理界面靠齐：卡片、标签、工具栏、分页、紧凑操作区、弹窗预览。

## 下一步建议

- [ ] 对已上传的历史文档执行“重新解析”或至少“重新生成向量”，让新的 table/image 语义索引内容生效。
- [ ] 在检索结果页增加图片缩略图、OCR/VLM 文本折叠区、引用来源卡片，继续向 WeKnora 召回结果展示靠齐。
- [ ] 增加文档级“解析块浏览器”，按 text/table/image/page 分组查看解析块，而不仅是 chunk。
- [ ] 增加批量任务队列和进度条，统一显示解析、OCR、VLM、向量化、外部向量库同步状态。
- [ ] 增加 OCR/VLM 失败重试按钮和失败原因聚合视图。
- [ ] 对 DeepDoc Layout/TSR/OCR 结果增加小样本回归测试，避免后续重构破坏 PDF 图表解析。

## Phase R：前端配置恢复 + RAGFlow 全格式识别适配修复

### 已完成
- 修复 Phase Q 回退到早期简版模型配置页的问题，保留 Phase M/O 的“模型、API 与 DeepDoc 配置”完整页面。
- 保留模型配置保存、供应商联动、模型列表刷新、真实 API 测试、DeepDoc 状态展示。
- 保留 Phase O 的 chunk 列表分页、类型筛选、向量状态筛选、切片详情向量预览、图片/表格展示。
- 在不改变 know_rag 入库、chunk、embedding、retrieval、前端管理结构的前提下，接入 RAGFlow 全格式 parser adapter。
- 后端新增并启用 `ragflow_format_adapter.py`，PDF/DOCX/Excel/CSV/HTML/JSON/TXT/Markdown/PPT/EPUB/Image 等格式统一走 RAGFlow parser/DeepDoc 识别清洗后，再转换成 know_rag ParsedDocument/ParsedBlock 入库。
- Dockerfile 增加 `ragflow_vendor` 拷贝，requirements 增加 RAGFlow parser 所需的 pandas/openpyxl/python-pptx/ebooklib/bs4/markdown/chardet/scikit-learn 等依赖。
- media 保存结果同时返回 relative_path/url/filename/file_name，避免前端图片预览和 image chunk 展示丢路径。

### 下一步
- 建立全格式回归测试集，对比 RAGFlow 原输出与 know_rag adapter 输出差异。
- 对齐 RAGFlow parser_config，例如 chunk_token_num、delimiter、table_context_size、image_context_size。
- 增加解析任务队列和进度条，避免大 PDF / PPT / Excel 上传时前端等待过久。
- 增加“解析块浏览器”，按 RAGFlow block 顺序查看 text/table/image/caption。
- 优化 OCR/VLM 失败重试、错误聚合和批量重跑。

## Phase S：统一 Image2Text/VLM 后处理修复

### 已完成
- 修复 Image2Text 测试接口“假成功”问题：外部视觉模型返回空文本时会明确失败提示。
- 修复 DashScope、SiliconFlow、智谱、腾讯混元、火山 Ark、xAI、StepFun、DeepSeek、自定义 OpenAI-Compatible 等供应商在真实解析流程中未被 `vision_service` 识别的问题。
- Image2Text/VLM 统一使用 OpenAI-compatible `chat/completions + image_url` 形式，Ollama 继续使用 `/api/chat + images`。
- 任何来源的 image block 都会尝试进入统一 VLM 后处理，包括单独上传图片、PDF 裁剪图、DOCX 图片、PPT 图片、Excel 图片等。
- 对缺少 `image_base64` 但有 `image_path` / `image_info.relative_path` 的 image/table block，OCR/VLM 后处理会自动从本地文件回读图片并补齐 base64。
- VLM 返回空内容时会写入 `vlm_status=empty`，调用失败时写入 `vlm_status=failed` 和错误信息，避免静默跳过。

### 下一步
- 在文档详情页增加 VLM empty/skipped 统计，让“未调用、返回空、失败、成功”四类状态更清晰。
- 检索结果页展示 image chunk 的 VLM 描述和 OCR 文本折叠面板。
- 增加单独“重新执行 OCR/VLM 增强”按钮，避免每次都完整重新解析文件。

## Phase T：RAGFlow Vendor 依赖补齐与稳定化（已完成）

### 已完成
- 补齐 `ragflow_vendor/rag/nlp` 的 RAGFlow parser 兼容函数：`find_codec`、`concat_img`、`append_context2table_image4pdf`、`surname`。
- 新增 `ragflow_vendor/rag/app/picture.py`，避免 RAGFlow `figure_parser` 直接依赖完整 RAGFlow 应用层；VLM 仍由 know_rag 自己的 `vision_service` 统一处理。
- 新增 `common.connection_utils`、`common.config_utils` 兼容层，避免 DeepDoc/TCADP/figure parser 的轻量导入失败。
- 新增 `api.db.services.llm_service`、`api.db.joint_services.tenant_model_service` stub，避免 RAGFlow 租户模型服务依赖污染 know_rag 入库体系。
- 新增 `deepdoc.vision.dla_cli` stub，明确当前默认使用本地 ONNX DeepDoc 模型，不自动接远程 DLA 服务。
- `requirements.txt` 补齐 DeepDoc parser/vision 所需核心依赖：`beartype`、`six`、`shapely`、`pyclipper`。
- `docker-compose.yml` 与 `entrypoint.sh` 增加 `PYTHONPATH=/app:/app/ragflow_vendor`，保证容器运行时能稳定 import `deepdoc`、`rag`、`common` 兼容包。

### 下一步
- 对 PDF、DOCX、Excel、PPT、HTML、JSON、TXT、EPUB、图片建立回归测试集，逐个比较 RAGFlow 原 parser 输出与 know_rag adapter 输出差异。
- 将 `ragflow_vendor` import 检查加入后端启动健康检查或测试脚本，避免后续版本再次出现 `ModuleNotFoundError`。
- 继续优化 RAGFlow 表格结构输出到 know_rag `table chunk` 的 HTML/文本双通道质量。


## Phase U - RAGFlow DeepDoc asyncio runtime fix

已完成：
- 修复 RAGFlow 原版 `pdf_parser.py` 在 FastAPI 已运行事件循环中调用 `asyncio.run()` 报错的问题。
- 新增 `common.misc_utils.run_async_compat()`，当当前线程已有事件循环时，将 DeepDoc OCR 协程放入独立线程事件循环执行。
- 同步修复 `deepdoc/vision/t_ocr.py` 的 `asyncio.run()` 兼容问题。

下一步：
- 继续用 PDF / 扫描 PDF / DOCX / Excel / PPT / 图片回归测试 RAGFlow vendor 解析链路。
- 如继续出现 RAGFlow vendor 兼容层缺失，优先补兼容层，不改 know_rag 入库结构。


### Phase V：RAGFlow PDF 图表输出适配修复
- 修复 RAGFlow 原版 `RAGFlowPdfParser.__call__` 返回 `(image, rows)` 时，know_rag adapter 误按 `((image, rows), positions)` 解包，导致 PDF 图/表裁剪结果被静默丢弃的问题。
- 现在同时兼容 `(image, rows)` 与 `((image, rows), positions)` 两种返回结构。
- 重新解析 PDF 后，文档详情中的“图片块 / 表格块 / PDF 图表裁剪”应能正常统计。

下一步：继续对比 RAGFlow 原系统输出和 know_rag adapter 输出，补齐 caption 上下文、跨页表格和复杂公式区域的展示。


## Phase W：RAGFlow PDF 媒体分类与模型路径修复

已完成：
- 将 RAGFlow 原版 PDF parser 返回的 `(PIL.Image, rows)` 正确区分为 figure/image 与 table。
- 修复之前把所有 `rows=list` 的媒体项都当作 table，导致论文图和公式图不生成 image chunk 的问题。
- 保存 RAGFlow PDF 媒体裁剪图到 `/data/files/.../media/`，并写入 `image_path/image_url/image_info`。
- 文档统计补充 `figure_crop_count/table_crop_count/caption_bound_count/pdf_media_item_count`。
- entrypoint 自动把 `/app/ragflow_vendor/rag/res/deepdoc` 软链接到 `${DEEPDOC_MODEL_DIR:-/data/models/deepdoc}`，避免 RAGFlow 原版 parser 重复从 HuggingFace 下载模型。

下一步：
- 增加 RAGFlow parser 输出与 know_rag ParsedBlock 输出的 diff 工具。
- 对 PDF 图、表、公式分别建立回归测试样例。
- 进一步优化 PDF media 的表格/图片分类规则，减少公式被误判为普通图片时的展示噪声。


## Phase X：切片类型筛选与图片预览修复

已完成：
- 修复切片管理页类型/向量化筛选只在当前页本地过滤的问题，改为后端查询前过滤再分页。
- 后端 `/documents/{doc_id}/chunks` 新增 `type` 与 `index_status` 查询参数。
- 修复 PDF 图/表裁剪图片 URL 可能变成 `/files//files/...` 的问题。
- 前端图片预览兼容 `image_info.url`、嵌套 `image_info.image_info.url`、`relative_path` 与本地 `/data/files` 路径。

下一步：
- 继续细化 RAGFlow PDF media 分类，避免公式截图被计为普通图片、复杂表格被拆成多个表格块。
- 增加图/表/equation 三类媒体的独立筛选和统计。


## Phase Y：切片筛选参数兼容修复

- 修复前端切片列表仍按当前页本地过滤的问题。
- `listChunks` 请求同时携带 `type` 与 `chunk_type`，兼容两版后端参数名。
- 后端 `/api/v1/documents/{doc_id}/chunks` 同时接受 `type` 和 `chunk_type`，统一转换为服务端筛选条件。
- 预期选择图片筛选后请求 URL 包含 `type=image&chunk_type=image`，总数显示为 `1-6 / 6`。

## Phase Z2 - 解析工作台 UI 与任务化解析（已完成）

### 已完成
- 基于当前版本新增解析任务模型 `parse_tasks` 与事件表 `parse_task_events`，支持记录解析阶段、block 事件、chunk 事件和失败原因。
- 新增后端 API：`/api/v1/parse-tasks/{task_id}`、`/api/v1/parse-tasks/{task_id}/events`、`/api/v1/parse-tasks/documents/{doc_id}/latest`。
- 上传页升级为解析任务创建器：支持展示解析模式、OCR 策略、图/表裁剪、VLM 图片理解等选项，并在上传后跳转解析工作台。
- 新增 `ParseWorkbenchView.vue` 三栏式解析工作台：左侧任务进度，中间解析事件流，右侧 block/chunk 详情预览。
- 文档详情页新增“解析工作台”入口；点击“解析/重新解析”会创建后台解析任务并跳转工作台。
- 后台解析过程中会记录阶段事件、ParsedBlock 事件和 chunk_created 事件，用于前端动态展示。

### 下一步
- 将 PDF adapter 进一步改造成 generator/callback 模式，让 DeepDoc 在“每页/每块”解析完成时立刻推送事件，而不是 parser 完成后批量写入 block 事件。
- 增加 SSE 接口，替代当前轮询 `/events` 的方式。
- 优化 equation / figure / table 细分类型，避免公式、算法框和表格统计混淆。
- 增加解析任务列表页，支持按文档、状态、格式查看历史任务。

## Phase Z3：解析结果优先与工作台默认不向量化

已完成：
- 解析工作台任务默认只执行解析、OCR/VLM 增强和切片入库，不再自动执行向量化；确认解析结果后可在文档详情页手动生成向量。
- 新增 JSON/metadata 递归清洗，避免 PDF/OCR/parser metadata 中的 NUL 或控制字符导致 PostgreSQL JSON/TEXT 入库失败。
- 切片入库失败不再把整篇文档直接标为解析失败：解析统计和 parsed_block 事件会保留，工作台仍可查看当前解析结果。
- 工作台 UI 增加“默认不向量化”和“切片失败也可查看解析块”的提示，左侧统计优先展示解析块数量。

下一步：
- 将 DeepDoc PDF adapter 进一步改造成 page/block callback 模式，实现真正逐页/逐块实时推送，而不是 parser 返回后批量写事件。
- 增加解析块持久化表 parsed_blocks，使切片失败后仍能在独立页面分页查看所有解析块。
- 细分 figure / equation / table / algorithm 类型，减少公式或算法区域被误算成图片/表格的情况。

## Phase AA：Parse Evaluation Plugin（解析质量评测插件）

已完成：
- 新增低耦合后端插件目录 `backend/app/plugins/parse_eval/`。
- 新增低耦合前端插件目录 `frontend/src/plugins/parse-eval/`。
- 新增 `parse_eval_runs`、`parse_eval_items` 两张评测表。
- 新增 `/api/v1/parse-eval/*` API。
- 新增左侧菜单“解析评测”。
- 文档详情页新增“解析评测”按钮。
- 支持无 GT 通用规则诊断。
- 支持扫描合同 OCR 模板评测。
- 支持评测问题列表、问题详情、图片预览、Markdown 报告。

后续：
- 增加人工抽样复核。
- 增加多解析器对比。
- 增加检索可用性评测。
- 增加学术论文 PDF / DOCX / Excel / PPT 专用评测模板。
