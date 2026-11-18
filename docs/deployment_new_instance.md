# Know_RAG_new 本机独立部署

> 状态：Current，更新至2026-11-06 P5。
> 工作目录：`D:\know_rag_new\know_rag`。Compose 项目名 `know_rag_new`，容器前缀 `Know_RAG_new-`。

2026-11-04 P2 更新：新实例已迁移至 `0018_knowledge_index_foundation`，后端、前端、MCP
和 gateway 已使用 P2 代码重建。健康检查、网页5175、OpenAPI新路由和登录态只读接口通过。
验证时业务数据为2个知识库、10篇文档、1148个切片；实体任务0，开启实体索引的知识库0。
此记录是当前观测，不代表旧迁移记录中的文档数量始终不变；本次没有为业务文档调用模型。
独立消费者 `Know_RAG_new-knowledge-index-worker` 已启动并验证为 running，当前无待处理任务。
实体开关仍由用户按库启用；不因 worker 运行而自动开启已有知识库。

2026-11-11 新增 `Know_RAG_new-index-worker` compose 服务（`docker-compose.new.yml`），
常驻消费文档级实体/关系/事件任务和 KB 级社区任务；新建知识库必须选择检索家族：
关系型（A）构建实体、关系、社区，事件型（B）构建实体、事件。
worker 与后端共用镜像，通过 `know-rag-net` 访问 postgres，并通过 host-gateway 访问本机 Ollama。

## 访问入口

2026-11-06图谱配色调整，画布、工具栏与证据浮层统一为系统浅色，交互强调沿用品牌绿色，
实体与事件使用低饱和蓝色和琥珀色，网格与连线降低视觉重量。

2026-11-06图谱样式按确认的效果图更新为深色画布、窄导航和紧凑浮层，提供列表/2D切换、
当前子图来源筛选、邻接高亮及按需证据面板。前端类型检查、ESLint、15项测试和构建通过。
浏览器自动化连接仍超时，实际页面交互与带数据的视觉验收待完成。

2026-11-06新增知识库图谱浏览，backend与frontend已更新，知识库详情可进入实体/关系/事件图谱。
图谱与关联检索专项39项通过，前端15项测试、类型检查、ESLint和构建通过，Ruff通过。
实际登录态GET graph接口、匿名401、limit越界422及前端图谱路由HTTP 200已验证。
当前3个业务库的图谱开关关闭，knowledge_entities、knowledge_relations、knowledge_events均为0，
页面初始显示空状态。带数据的交互已由固定样本契约与布局测试覆盖，浏览器实机交互仍待验收。

2026-11-06 P5更新，后端/前端/MCP/gateway及独立索引worker已更新至auto/DRIFT实现。
数据库版本为0020_graph_communities，P5策略和预算复用profile JSON。
登录态实测auto使用本机Embedding与BM25返回2条结果，debug的召回与Context来自同次执行。
3个业务库的严格DRIFT均正确提示需要有效社区及关系，非法auto/graph_method组合返回422。
本次读取到3个库、30篇文档、1337个切片，派生索引开关均关闭，派生任务数为0。
现有生成模型配置为DeepSeek，当前图索引与探索生成入口要求本机Ollama。
真实DRIFT业务收益需在满足该模型条件、构建指定KB索引并提供人工标注后评测。
工程测试、预算和评测命令见retrieval_p5_implementation.md。浏览器连接超时，本轮页面交互尚未验收。

2026-11-05 P4更新：新实例已迁移至0020_graph_communities，后端/前端/MCP/gateway已重建，
独立worker已替换为know_rag_new-graphrag:2.7.0；主后端解释器没有安装graphrag。
GraphRAG仅位于worker的/opt/graphrag环境，pip check通过；真实官方Leiden/Parquet断网测试通过。
登录态社区列表/status、严格Global未就绪拒绝及未登录401检查通过；没有启用任何业务库的派生索引。
本次观测为3个库、30篇文档、1337个切片，知识索引/社区任务与报告均0。
这反映本次运行时数据，不覆盖先前历史快照。业务模型抽取/主题质量及浏览器交互尚未验收。
P4细节、隔离镜像构建及worker命令见retrieval_p4_implementation.md。

2026-11-04 P3更新：独立新实例已迁移至0019_relation_event_retrieval，Local/事件入口已部署，
worker已替换为P3版本。实际OpenAPI包含relations/events构建能力；网页组件提供两种选择。
登录态严格请求在索引未就绪时明确失败，未悄悄回退或触发抽取。当前高级索引开启数0，
knowledge_index_jobs、knowledge_relations、knowledge_events均为0；本次未调用真实模型。

| 服务 | 地址 |
|---|---|
| 网页 | http://localhost:5175 |
| 后端与 API 文档 | http://localhost:8003/docs |
| MCP（要求签名认证） | http://localhost:8013/mcp |
| 后端/MCP 反向代理 | http://localhost:8484 |
| MinerU CPU | http://localhost:8004/docs |
| MinIO 控制台 | http://localhost:9011 |

所有新宿主端口仅绑定本机回环地址。数据库、MinIO、索引和缓存数据卷均以
`know_rag_new_` 开头，网络也独立。旧 `know_rag` 项目及数据卷保留。
首次创建不会自动复制旧数据；此后已按用户要求复制旧知识库和模型 API 配置，见下方迁移记录。
新实例管理员账号保持不变，使用本目录 `.env` 的 ADMIN_USERNAME / ADMIN_PASSWORD。
旧实例用户及 MCP 客户端没有复制到新实例。

## 启动与停止

在项目根目录执行；必须同时加载两个 Compose 文件：

```powershell
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml up -d --no-build --pull never
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml ps
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml stop
```

签名网关使用可选 `signed-gateway` profile；只有在新实例注册 MCP 客户端，并将对应凭据
配置到 `.env` 后才启用。未签名访问 MCP 返回 401 是正常认证行为。
GPU 与 Qdrant 沿用可选 profile，本次没有启动。

## 更新源码与首次初始化

### P2 派生索引 worker（2026-11-04）

P2 需要迁移至 `0018_knowledge_index_foundation`。实体索引默认关闭，不会因升级自动抽取旧库。
在知识库编辑页勾选“构建实体基础索引”后，API/解析服务只写任务；应另行启动消费者。
消费者复用 backend 的镜像和完整环境，保证与解析端使用相同 OCR 准入及内容长度配置：

```powershell
# 单次补偿并最多消费一个任务；不会安装依赖或自动拉取模型。
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml run --rm --no-deps --entrypoint python backend -m app.services.knowledge_index.cli --once --reconcile

# 长期运行；有任务时仅使用已有本机 Ollama LLM 配置。
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml run -d --no-deps --name Know_RAG_new-knowledge-index-worker --entrypoint python backend -m app.services.knowledge_index.cli --reconcile
```

停止该消费者使用 `docker stop Know_RAG_new-knowledge-index-worker`；再次创建同名容器前需先清理
已停止的同名 worker 容器。worker 是独立进程，API 重启不会丢失任务；进程崩溃后租约可回收。
自动失败重试最多3次，退避60秒；部分完成或次数耗尽后，可通过指定文档的 build/retry 接口重试。
默认补偿每轮最多新增100个任务，会继续扫描后续文档。未配置本机 Ollama、云模型配置或异常输出
将明确失败，不会静默发送到其他模型。表内保存本地输入快照；普通 jobs 接口不暴露快照。
P4版本worker按build_indexes构建实体、Local关系、事件及KB社区报告；官方provider使用隔离镜像。
下面的基础backend命令适合native；官方provider worker应使用retrieval_p4_implementation.md中的三文件命令。
仅实体ready不代表高级检索ready；前端根据实际manifest覆盖显示各能力。

`docker-compose.new.yml` 使用 Compose 2.24.4+ 的覆盖标签替换端口和容器名。
三个 `docker/new-*.Dockerfile` 基于本机已有 `know_rag-*` 镜像复用依赖层，
仅覆盖本工作区源码；本次没有重新安装 Python/Node/MinerU 依赖。
这是一份本机部署配置，不是可独立分发的完整依赖构建方案。
依赖清单变更时需先按标准 Dockerfile 重建基础依赖镜像。

```powershell
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml build backend frontend mineru-cpu
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml up -d --no-build --pull never postgres redis minio elasticsearch milvus mineru-cpu
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml run --rm --no-deps --entrypoint alembic backend upgrade head
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml up -d --no-build --pull never --force-recreate backend frontend mcp gateway
```

本实例已完成全量迁移至 `0016_kb_retrieval_profile`，且已初始化 MinIO 存储桶。
DeepDoc 自动下载在新覆盖文件中关闭；模型数据卷没有复制旧实例的模型文件。
首次部署时未验证真实 PDF 解析及 Embedding/Image2Text 调用；本地 MinerU 模型的后续补齐与验证见下文。

## 本次验证

- 新镜像构建成功，10 个默认服务运行。
- 网页 5175、后端 8003 健康、代理 8484、MinerU 8004 API 文档均可访问。
- 新网页实际加载策略组件；后端 OpenAPI 包含 strategy。
- 新容器内 `tests/test_retrieval_strategy.py`：47 passed。
- 新实例登录、创建默认 hybrid/v2 知识库、严格图模式拒绝、参数冲突拒绝通过真实 HTTP 验证。
- 临时测试知识库已清理；没有调用外部模型或复制旧库数据。

## 旧知识库与模型配置复制（2026-11-03）

按用户要求，从停止状态的旧 `know_rag` 实例复制数据，旧容器和原数据卷不修改。
旧 PostgreSQL、MinIO 卷先只读克隆，再由临时快照服务提供迁移读取。
迁移期间仅暂停新实例应用服务；完成后恢复新实例的 10 个默认服务。

| 内容 | 数量与结果 |
|---|---|
| 知识库 | 5 个，保留原 ID，迁移后使用 legacy_v1 兼容策略 |
| 文档 / Canonical artifacts | 各 47 条 |
| 切片 | 5,452 条 |
| 保存的向量 | 2,373 条；写入新实例独立 Milvus 并核对数量 |
| 原文与媒体对象 | 1,032 个，413,315,954 字节，逐对象 SHA-256 核验一致 |
| 模型配置 | 5 类：llm、embedding、image2text、ocr、rerank；包含 API 凭据，未输出密钥 |
| 检索 / 切片 / 向量库配置 | 各 1 条，Milvus 仍使用新网络内的 milvus 服务名 |
| 解析任务及事件 | 73 / 16 条 |
| 解析评测及明细 | 4 / 395 条 |
| 重建关键词索引 | 4,543 个可索引切片；父块等非检索单元不进入该索引 |

数据库各复制表在同一事务中导入，并按稳定列序及主键顺序计算 SHA-256 核对。
目标原有 3 条同类型模型配置已备份，以旧实例的 5 类配置替换；新实例账号保持不变。
旧库有 4 条非终止任务，但旧进程早已停止，无法迁移其执行状态。
仅在新实例将其标为 failed，stats 记录迁移前状态和阶段，并注明未自动重新解析。
既有文档、切片及解析版本不重建；本次未调用 Embedding、图片理解、重排或其他云模型 API。

实际核验：登录后可列出 5 个知识库；每个库的关键词 HTTP 检索成功且范围正确。
原先有向量的 4 个库通过保存向量的 Milvus 召回核验；另一个旧库原本没有向量，未额外生成。
47 份原文件经新前端文件代理下载，内容 SHA-256 与旧对象一致。
云模型凭据通过配置表摘要一致性验证，未发送真实模型请求，因此不代表远程 API 可用性已测试。

新实例迁移前完整备份、旧实例数据库快照备份和校验报告保存在
`backend/.generated/migration_old_20261103/`（Git 忽略目录），访问权限限制为当前 Windows 用户和 SYSTEM。
临时快照容器、临时数据卷及临时 MinIO 凭据文件已清理；旧实例原数据卷保留。

## MinerU 本地模型补齐（2026-11-03）

当前镜像中的 MinerU 为 3.3.1。程序依赖与模型权重分别部署；安装程序、启动 API 或访问
`/docs` 均不代表模型已下载。新模型卷起初仅有少量配置文件，首次解析触发的在线下载曾超时。

本次只读复用旧实例 pipeline 缓存，再从官方 ModelScope 镜像补齐缺失文件，未安装或升级包。
文件清单固定为 `opendatalab/PDF-Extract-Kit-1.0` 的 revision
`ed6b654c018d742e65a17671e379c5e6ecc87ec9`，覆盖当前下载工具定义的七类 pipeline 路径：
PP-DocLayoutV2、UniMERNet Small、PP-FormulaNet Plus-M、PaddleOCR、SLANet Plus、UNet 表格结构和表格分类。
共 40 个文件、2,595,586,833 字节；全部按官方 LFS SHA-256 或 Git blob SHA-1 校验。
本次不下载 VLM 后端或 DeepDoc 备用解析器权重。

模型及配置保存在独立的 `know_rag_new_know-rag-mineru-models` 卷中：

- 配置：`/data/models/mineru.json`，其中 `models-dir.pipeline` 指向完整 snapshot 根目录。
- 权重根目录：`/data/models/huggingface/hub/models--opendatalab--PDF-Extract-Kit-1.0/snapshots/ed6b654c018d742e65a17671e379c5e6ecc87ec9`。
- `docker-compose.new.yml` 的 `mineru-cpu` 设置 `MINERU_MODEL_SOURCE=local`，
  并通过 `MINERU_TOOLS_CONFIG_JSON=/data/models/mineru.json` 读取持久配置。
- 仅重建容器即可应用配置，不需要重新构建镜像；不得删除模型卷。

后续需要补充或恢复模型时，可使用容器中已有的下载工具，并显式指定下载源，避免被运行时
`local` 设置影响。配置文件仍写入持久卷；网络失败时保留缓存后重试，不要删除已校验权重：

```powershell
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml run --rm --no-deps --entrypoint mineru-models-download -e MINERU_MODEL_SOURCE=modelscope -e MINERU_TOOLS_CONFIG_JSON=/data/models/mineru.json mineru-cpu -s modelscope -m pipeline
```

下载工具会在原配置上合并 `models-dir.pipeline`。更换模型来源后路径可能变化，重启服务前应
检查配置中记录的根目录。请使用当前 3.x 下载命令，不要混用 MinerU 4.x 的配置或工具。

验证结果：在 `--network none`、模型卷只读的临时容器中，启用 OCR、公式和表格完成单页解析；
日志包含 `DocAnalysis init done!`、版面、公式、OCR 与两类表格推理。恢复服务后，通过真实
`/file_parse` 接口再次验证，HTTP 200 且任务成功，输出正文、`E=mc²` 的 LaTeX 和包含
120/150 数值的结构化表格。此为初始化和解析链路验证，不代表所有语言或文档的识别准确率。

文件清单及摘要报告、HTTP 解析结果保存在 `backend/.generated/mineru_models_20261103/`；
卷内另保留 `/data/models/pipeline-model-verification.json`。下载临时分片已清理。
既有失败或运行中的知识库任务未自动重试，Embedding/Image2Text 远程调用也未验证。

## 同知识库文件内容去重（2026-11-03）

新实例已迁移至 `0017_document_file_dedup`，为非空的
`documents(knowledge_base_id, file_hash)` 添加唯一索引。升级前核验现有文档没有同库指纹重复或
空指纹，未删除原文档。更新后端前等待当时正在解析的任务结束，再重建主后端容器。

上传在写 MinIO 前流式计算 SHA-256 并查重；同内容改名仍拒绝，同名不同内容或跨知识库允许。
数据库索引负责并发兜底，失败方回滚并清理自己的对象，返回已有文档信息；前端沿用原错误提示。
未修改前端、Python 依赖或模型配置。

验证：本次定向测试与原存储测试共 16 项通过；完整后端回归 1,185 项通过，Ruff 检查通过。
真实 HTTP 验证覆盖改名重复、同名不同内容、跨库相同内容、删除后重传和 8 请求并发；并发仅
创建 1 份文档。测试结束前核对 4 条测试文档对应恰好 4 个 MinIO 对象、0 个解析任务，随后
清理全部临时知识库。报告保存在 `backend/.generated/upload_dedup_20261103/verification.json`。
