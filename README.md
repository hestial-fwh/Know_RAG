# know_rag

`know_rag` 是轻量本地 RAG 知识库建立维护底座。它不复刻 WeKnora 全量能力，也不复制或翻译 WeKnora Go 后端。

> 状态：Current
> 维护日期：2026-07-15
> 文档入口：`docs/index.md`
> 说明：本文从“历史阶段记录”开始的内容仅用于追溯，不代表当前未实现能力。

当前系统由 Vue 3/Vite 前端、FastAPI 后端、PostgreSQL/pgvector、Elasticsearch、MinIO、
MinerU/DeepDoc 解析服务和只读 MCP 服务组成。默认采用本地 Ollama；管理员也可以显式配置
OpenAI-compatible 远程模型供应商，因此部署前应按 `docs/security.md` 评估数据边界。

## 当前核心能力

- 本地 Ollama embedding 调用，默认模型 `bge-m3`。
- PostgreSQL + pgvector 保存 chunk 向量。
- 文档级、知识库级向量生成与状态查询。
- 文档详情页可点击“生成向量 / 查看向量化状态”。
- 知识库详情页可点击“批量生成向量 / 查看向量化状态”。
- 真实 hybrid search：向量召回 + Elasticsearch BM25，支持 RRF 融合。
- 前端“本地检索调试”页面接入真实接口。
- 文档详情和知识库详情增加向量化入口。

## 启动

宿主机先准备 Ollama 模型：

```bash
ollama pull bge-m3
```

启动服务并执行迁移：

```bash
cd know_rag
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

常用质量检查：

```bash
docker compose exec backend ruff check app tests
docker compose exec backend python -m pytest tests/ -q
cd frontend && npm run check
```

## 历史阶段记录

以下 Phase 内容保留用于追溯实现过程。当前能力和约束以 `docs/index.md` 指向的 Current 文档、
运行代码和自动化测试为准。

访问：

```text
前端：http://localhost:5174
后端：http://localhost:8000/health
```

默认管理员：

```text
用户名：admin
密码：admin123
```

## Embedding

环境变量：

```text
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_EMBEDDING_MODEL=bge-m3
OLLAMA_EMBEDDING_DIMENSION=1024
AUTO_EMBED_AFTER_PARSE=false
HYBRID_VECTOR_WEIGHT=0.7
HYBRID_KEYWORD_WEIGHT=0.3
```

测试 embedding：

```bash
curl -X POST http://localhost:8000/api/v1/initialization/embedding/test ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer <token>" ^
  -d "{\"text\":\"这是一个本地知识库向量测试\"}"
```

为文档生成向量：

```bash
curl -X POST http://localhost:8000/api/v1/documents/<doc_id>/embedding ^
  -H "Authorization: Bearer <token>"
```

为知识库批量生成向量：

```bash
curl -X POST http://localhost:8000/api/v1/knowledge-bases/<kb_id>/embedding ^
  -H "Authorization: Bearer <token>"
```

查看文档向量状态：

```bash
curl http://localhost:8000/api/v1/documents/<doc_id>/embedding/status ^
  -H "Authorization: Bearer <token>"
```

向量保存在本地 `chunk_embeddings` 表中，不会返回给前端，也不会出校。

## Hybrid Search

```bash
curl -X POST http://localhost:8000/api/v1/knowledge-bases/<kb_id>/hybrid-search ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer <token>" ^
  -d "{\"query\":\"测试问题\",\"top_k\":5,\"use_vector\":true,\"use_keyword\":true}"
```

## 安全边界

- 只默认调用本地 Ollama。
- 不启用远程模型供应商、Web Search、Agent、外部 SaaS 数据源。
- 原始文件、解析文本、chunk、embedding、向量索引都保留本地。
- PostgreSQL 仅绑定 `127.0.0.1:5434`。
- Redis 仅绑定 `127.0.0.1:6380`。
- MCP Gateway 仍为 placeholder。

## 仍未实现

- 本地 RAG 问答生成。
- 会话和消息持久化。
- context pack 组装和引用来源回答。
- FAQ embedding / FAQ search。
- 完整 MCP Gateway。

## PDF 解析说明

PDF 解析文本会在入库前自动清洗 PostgreSQL `TEXT` 不支持的 NUL 字符和不可见控制字符。扫描件或图片型 PDF 当前可能无法抽取文本，需要后续 OCR 能力。

文档上传、解析、chunk 入库与 embedding 生成互相解耦；只有点击“生成向量”或调用 embedding API 时才会写入 `chunk_embeddings`。
 
## WeKnora 对齐 Phase A

Phase A 已完成数据模型兼容字段补齐，不重命名 `documents`、`chunks`、`chunk_embeddings`，也不删除旧字段。`knowledge_id` 当前等于 `document_id`，用于后续兼容 WeKnora 风格召回结果。

新增 chunk 对齐字段包括 `start_at`、`end_at`、`pre_chunk_id`、`next_chunk_id`、`chunk_type`、`parent_chunk_id`、`context_header`、`content_hash` 等。当前仍使用简单字符切分，`start_at/end_at` 来自字符窗口位置，`pre_chunk_id/next_chunk_id` 来自 flat chunk 相邻关系。

本阶段仍未实现结构感知 chunker、parent-child chunking、WeKnora RRF hybrid search、RAG 问答和 MCP Gateway。
 
## WeKnora 对齐 Phase B

Phase B 已实现结构感知 chunker。新上传或重新解析文档会使用 Markdown-aware / heading-aware / separator-aware / protected-pattern-aware 的切分策略，并写入 `start_at`、`end_at`、`context_header`、`chunk_type`、`content_hash`、`pre_chunk_id`、`next_chunk_id` 等字段。

相关配置：

```text
DEFAULT_CHUNK_SIZE=512
DEFAULT_CHUNK_OVERLAP=80
CHUNK_STRATEGY=auto
CHUNK_SEPARATORS=\n\n|\n|。|.|；|;|，|,| 
ENABLE_PARENT_CHILD_CHUNKING=false
PARENT_CHUNK_SIZE=4096
CHILD_CHUNK_SIZE=384
CHILD_CHUNK_OVERLAP=64
```

重新解析文档会删除该文档旧 chunks 和旧 `chunk_embeddings`，避免检索命中过期切片。parent-child chunking 已实现但默认关闭；Phase C 将继续对齐 embedding 内容和索引表。
## WeKnora 对齐 Phase C

Phase C 已完成 embedding 内容与索引表进一步对齐。生成向量时，索引文本不再只使用 `chunk.content`，而是使用“文档标题 + 上下文标题 + 切片正文”构造，并写入 `chunk_embeddings.content`。

`chunk_embeddings.source_id/source_type/knowledge_id/is_enabled` 已参与写入和检索过滤。embedding 向量只保存在本地 PostgreSQL/pgvector 中，不通过前端或 API 返回。

新增配置：
```text
EMBEDDING_CONTENT_MAX_CHARS=6000
```

文档详情页会展示已向量化数量、索引文本数量和带上下文标题切片数；切片详情页会展示是否已索引、索引模型、维度和索引文本预览。
## 切分配置修复

新增“切分配置”页面和 `/api/v1/chunker-config` 接口，可动态调整：

- 切分策略：`auto` / `heading` / `recursive` / `legacy`
- 切片大小与重叠长度
- 分隔符列表
- 是否启用父子分块
- 父切片大小、子切片大小、子切片重叠

配置保存后对新上传或重新解析的文档生效；已有文档不会自动重切。切片列表已显示向量化状态，`parent_text` 会显示为“不参与向量化”。
## WeKnora 对齐 Phase D

Phase D 已将 hybrid-search 默认融合策略改为 RRF（Reciprocal Rank Fusion），同时保留 `weighted` 加权分数作为兼容模式。

hybrid-search 同时支持旧参数和 WeKnora 风格参数，例如 `query_text`、`match_count`、`vector_threshold`、`keyword_threshold`、`disable_vector_match`、`disable_keywords_match`、`include_context`。返回结果补充 `rrf_score`、`vector_rank`、`keyword_rank` 和 `context.parent/previous/next` 预览字段。

默认配置：
```text
HYBRID_FUSION_STRATEGY=rrf
RRF_K=60
RRF_VECTOR_WEIGHT=1.0
RRF_KEYWORD_WEIGHT=1.0
```

## Phase E - 文档处理增强：RAGFlow-lite PDF + WeKnora-style API 配置

本次改造新增项目本地的多模态解析抽象，不直接复制 RAGFlow 内部依赖：

- 新增 `ParsedDocument / ParsedBlock`，统一表示 `text / table / image` 三类解析块。
- DOCX 解析改为按 Word body XML 顺序遍历段落、图片、表格：
  - 段落生成 text block；
  - 内嵌图片保存到 `LOCAL_STORAGE_DIR/<kb_id>/<doc_id>/media/` 并生成 image block；
  - 表格转 HTML + 纯文本，并生成 table block。
- PDF 解析新增 `ragflow_lite_pdf`：
  - 优先用 `pdfplumber` 做页级文本和表格抽取；
  - 失败时回退 `pypdf` 文本抽取；
  - 尝试提取 PDF 内嵌图片；
  - 文本为空时如安装 `pypdfium2`，会把页面渲染为图片 block，便于后续 OCR/VLM。
- chunk 入库支持 `chunk_type=text/table/image`，并将图片文件信息写入 `image_info`。
- hybrid search 与 embedding 的可用 chunk 类型从纯 text 扩展为 `text/table/image`。
- 新增 `/files/<relative_path>` 本地文件访问路由，用于前端预览解析出的图片。
- 模型配置页增加 Image2Text / OCR API 配置项，风格上参考 WeKnora 的“供应商 + Base URL + API Key + 模型名”方式。
- 如果配置了 Image2Text 模型，解析文档时会尽力为 image block 生成图片描述；未配置时只保存图片并生成基础占位文本。

新增依赖：

```text
pdfplumber==0.11.4
pypdfium2==4.30.0
Pillow==11.0.0
```

使用建议：

1. 执行 `docker compose up -d --build` 重新构建后端镜像。
2. 执行 `docker compose exec backend alembic upgrade head` 保持数据库最新。
3. 在“模型与 API 配置”页面按需配置 Image2Text API；不配置也不影响文本/表格/图片保存。
4. 上传含图片和表格的 DOCX，确认切片列表出现 `text / image / table` 三类 chunk。
5. 上传 PDF，确认普通文本 PDF 能生成 text/table chunk；扫描件 PDF 会生成 page image chunk，后续可接 OCR。

## Phase G 更新

本版本新增 RAGFlow-style PDF 高级解析、WeKnora-style 模型 API 配置、Qdrant/Milvus 向量库切换、图片预览弹窗与 OCR/VLM 失败状态统计。详见 `docs/phase_g_multimodal_vector.md`。

## Phase H 更新：DeepDoc 生产模型链路与 API 测试

本版本新增可选 DeepDoc 生产模型链路：`DEEPDOC_LAYOUT_MODEL`、`DEEPDOC_TSR_MODEL`、`DEEPDOC_XGB_MODEL` 可接入 ONNX LayoutRecognizer、ONNX TableStructureRecognizer 和 XGBoost 文本框纵向合并模型。未配置模型时自动回退到启发式 RAGFlow-style PDF 解析。

模型与 API 配置页新增 LLM、Embedding、Image2Text、OCR 的 API 连通性测试按钮，并修复供应商切换时 Base URL / 默认模型名不同步的问题。详见 `docs/phase_h_deepdoc_api_test.md`。


## Phase I：RAGFlow-style DeepDoc 默认启用

- DeepDoc OCR 现在默认为内置提供方 `deepdoc-builtin`，不再要求用户配置 OCR API。
- API 配置页仍保留外部 OCR / VLM API 测试能力，但 OCR 默认显示为 DeepDoc 内置 OCR。
- DeepDoc 状态页显示运行模式：`local-models`、`external-service` 或 `builtin-fallback`。
- 当容器中存在 RAGFlow DeepDoc ONNX / XGBoost 模型或外部 DeepDoc 服务时，会自动使用生产级链路；否则使用启发式兜底，不影响文档上传。

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

### Phase T：RAGFlow Vendor 依赖补齐

本阶段修复了将 RAGFlow DeepDoc 原版 parser/vision 作为 vendor 引入 know_rag 后的依赖注入问题。当前做法仍保持 know_rag 的入库、chunk、embedding、retrieval、前端管理体系不变，只补齐 RAGFlow parser 在识别/清洗阶段需要的轻量兼容函数和 stub 模块。

更新后建议执行：

```powershell
docker compose build backend
docker compose up -d --force-recreate backend
docker compose exec backend alembic upgrade head
```

验证 vendor 依赖：

```powershell
docker compose exec backend sh -lc "python - <<'PY'
import deepdoc
from rag.nlp import concat_img, find_codec, append_context2table_image4pdf
from deepdoc.parser.pdf_parser import RAGFlowPdfParser
print('ragflow vendor ok')
PY"
```


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

## Phase Z2：解析工作台 UI

本版本在当前 know_rag 基础上新增“解析工作台”能力。上传文档后，系统会创建后台解析任务，并进入 `/parse-tasks/{task_id}` 页面。该页面以三栏方式展示解析进度、事件流和块级详情，便于测试 PDF、DOCX、Excel、PPT、图片等格式的解析效果。

主要新增内容：

- 后端：`ParseTask`、`ParseTaskEvent`、任务事件 API、后台解析任务入口。
- 前端：新版文档上传页、解析工作台页、文档详情页工作台入口。
- 体验：解析阶段、OCR/VLM、block、chunk 以事件形式逐步展示；可点击查看裁剪图、表格、文本和 metadata。

升级后执行：

```bash
docker compose build backend
docker compose up -d --force-recreate backend
docker compose exec backend alembic upgrade head

docker compose build frontend
docker compose up -d --force-recreate frontend
```

### Phase Z3：解析结果优先与工作台默认不向量化

本版本将解析工作台调整为“先看解析结果”的流程：后台解析任务默认不自动生成向量，避免 embedding 配置或模型不可用影响文档解析验收。解析完成后，用户可在文档详情页手动点击“生成向量”。

同时，系统对 parser metadata、image_info、parse_task_event payload 等 JSON 内容做递归清洗，避免扫描 PDF / OCR / VLM 返回的控制字符造成 PostgreSQL 入库失败。若切片入库仍失败，解析任务不会直接丢失已识别结果：文档会保留解析统计，解析工作台会保留 parsed_block 事件，便于先查看当前解析效果。
