# Security

知识库可通过retrieval_profile.index_model选择独立的本机Ollama模型，仍由既有本机主机白名单、
禁代理和禁重定向规则约束。names生成模式使用服务器产生的来源片段ID，未知ID及未定义实体端点
拒绝发布，实体名称、限定与关系词继续逐字回查该片段。原文以当前KB的准入投影为依据。

知识库图谱GET接口沿用用户认证，所有来源与端点限定当前KB，复用检索的有效版本和准入门禁。
单次最多150节点、300边，扫描与来源引文另有上限，裁剪通过truncated标记。图谱浏览只读取本地数据。

## P5 探索边界

auto在用户选定家族内路由，DRIFT子问题只调用同一KB的既有Local检索。
规划仅发送有效社区要点与准入原文，模型生成的子问题携带实际输入要点ID，未知ID拒绝执行。
所有轮次共享候选、模型、检索、字符和时间预算，原文与社区版本在最终异步重排后重验。
首次规划失败的回退计入总额度，已有证据的补查失败按策略部分返回或报错。
已有本机Ollama限制同时约束DRIFT规划，token计量只使用服务端实际计数。
debug模式增加的原始召回结果沿用同一登录认证和KB范围。

## P4 社区与Global边界

社区构建按KB显式开启；官方graphrag==2.7.0仅安装在独立镜像虚拟环境，通过固定脚本/参数运行，
不执行用户传入命令或任意路径。官方适配只调用Leiden社区算法与Parquet，不启用云供应商。
报告生成及Global沿用本机Ollama白名单和禁重定向/代理规则，模型输入只来自有效准入投影。
摘要必须标记derived_summary并绑定当前revision和逐字支持引文；逐字命中不等于语义正确性证明。
HTTP/MCP返回前重验依赖，Context裁剪后剔除支持不完整的要点，避免生成文本冒充原文。
社区接口沿用认证与KB范围；状态/列表不返回模型配置、来源快照或完整模型错误响应。

## P3 关系/事件证据边界

关系和事件沿用P2本机Ollama限制，默认不开启；选择查询策略不会触发抽取或云调用。
所有参与者、关系词及限定需支持引文；事件text是生成的检索表示，不能伪装成原文返回。
图/事件结果使用准入投影，路径保留来源通道，VLM等派生描述不能伪装成明确原文事实。
共享实体只作为关联线索，不从共现构造确定关系；所有SQL均参数化并限定KB/当前manifest。

## P2 实体索引边界（2026-11-04）

实体抽取默认关闭，通过 KB profile.build_indexes 显式开启。当前 worker 只使用已配置的
本机 Ollama LLM，URL 主机仅允许 localhost/127.0.0.1/::1/host.docker.internal/
gateway.docker.internal，禁止重定向和代理环境继承，不拉取模型，不使用云供应商配置。
输入只来自现有字段准入后有效 child；本地 PostgreSQL 保存版本化抽取快照与支持引文。
jobs 接口不返回输入快照、模型地址或凭据；错误记录不保存完整模型响应。
实体 API 必须经过现有登录认证，并按 KB 和有效来源回查；模型生成的实体别名保持候选性质，
不会据此生成确定关系。准入投影 offset 明确区别于 Canonical 原始字符范围。

> 状态：Current
> 维护日期：2026-08-13

## 本地知识资产

以下数据均属于本地知识资产，默认保留在学校本地：

- 原始文件。
- 解析文本。
- chunk。
- embedding 向量。
- pgvector 索引。
- 检索结果和来源信息。
- 后续 FAQ 答案。

## 默认安全边界

- 默认 Embedding 和 LLM 调用本地 Ollama。
- 默认 hybrid search 在 PostgreSQL/pgvector 与 Elasticsearch 中执行。
- 普通 API 不返回完整 embedding 向量，只返回维度、范数和最多 16 维预览。
- PDF 页面主视觉保护只在本地读取原 PDF，并将选页渲染结果写入现有 MinIO 媒体边界；不开启
  远程模型或外部搜索。功能默认关闭，且同时受文档 `enable_media_crop` 选项约束。
- audit_logs 不记录 embedding 向量、不记录完整 chunk 内容、不记录大段检索结果。
- 图文关系检索扩展同时以目标 ID、`knowledge_base_id` 和 `document_id` 约束数据库查询，并在映射
  返回前再次校验两个 scope；污染 metadata 不得跨知识库或跨文档读取 Chunk。
- `DOCUMENT_RELATION_GRAPH_ENABLED`、`DOCUMENT_RELATION_MATERIALIZATION_ENABLED` 与
  `DOCUMENT_RELATION_RETRIEVAL_EXPANSION_ENABLED`、`DOCUMENT_ASSET_GROUP_ENABLED` 默认关闭，
  可独立回滚；group 仅使用同一 MinerU page coordinate space，不跨 parser/section 自动绑定；
  扩展异常 fail-open，不扩大权限边界，也不使基础检索失败。
- 完整 relation candidate/evidence 只保存在文档级本地 artifact；Chunk metadata 不复制完整候选图，
  只保存关系 ID 和检索所需摘要。
- PostgreSQL 和 Redis 默认仅绑定本机。
- 不提供 Web Search 或通用 Agent。

## 可选远程能力

模型配置支持 OpenAI-compatible、DeepSeek、DashScope、SiliconFlow 等远程供应商，向量库也可以
切换到 Qdrant/Milvus。它们必须由管理员显式配置。启用前应确认：

- 允许发送哪些 chunk、图片和 OCR 文本。
- API Key 只保存在本地 `.env` 或数据库配置中，不提交到 Git。
- 供应商的数据保留、日志和跨境策略符合部署要求。
- MCP 的 `include_image_urls` 默认保持关闭。

## 仓库与凭据

- `.env` 是本地敏感文件，模板使用 `.env.example`。
- `.gitignore` 阻止新的 `.env`、缓存、依赖和构建产物进入版本控制。
- 若历史提交曾包含真实凭据，仅添加忽略规则并不能消除历史风险，必须轮换凭据并单独清理历史。

## MCP

只读 MCP 服务已实现，通过 token 或用户名密码访问后端，并复用 `KnowledgeContextService`。
`mcp-placeholder` 是兼容路由。MCP 部署仍需限制监听地址、令牌权限、上下文长度、图片 URL 和审计范围。

### MCP 接入认证（Client Credential + HMAC-SHA256 签名）

外部 MCP Client 调用 `/mcp` 前必须携带 `X-Client-Id / X-Timestamp / X-Nonce / X-Signature`
四头；签名 = `hex(HMAC-SHA256(client_secret, canonical_string))`，canonical 串覆盖
client_id / timestamp / nonce / HTTP method / path / body SHA256。服务端校验顺序：client 存在 →
is_enabled → expires_at → timestamp（±`MCP_SIGNATURE_MAX_AGE_SECONDS`，默认 300s）→ nonce
防重放（Redis `SET NX`）→ HMAC 验签（constant-time）→ Tool Scope → 更新 last_used_at。MCP auth
middleware 只在验签通过后放行，backend 不可达时 fail-closed 拒绝。

- Client Secret 用 AES-256-GCM 加密入库（Master Key 来自 `MCP_CLIENT_SECRET_ENCRYPTION_KEY`），
  明文仅在创建/重置时返回一次。
- 两套认证分工：`KNOW_RAG_SERVICE_TOKEN` 负责 MCP→后端内部调用与 verify 端点保护；外部
  Client Credential 负责客户端→MCP。二者不互相替代。
- 开发默认 `:8010/mcp`；正式部署应 `Internet → HTTPS Gateway/Nginx → 127.0.0.1:8010/mcp`，
  不要将 8010 公网裸暴露。生产必须轮换 `MCP_CLIENT_SECRET_ENCRYPTION_KEY` 与
  `KNOW_RAG_SERVICE_TOKEN`。
- 标准 MCP 客户端（MCP Inspector / Claude Desktop / Cursor 等）无法逐请求计算 HMAC，需在它们
  与 MCP 之间放一个签名网关：`backend/scripts/mcp_signing_gateway.py`。客户端用静态
  `Authorization: Bearer <GATEWAY_TOKEN>` 连网关（`:8011`），网关持有 client_id + client_secret，
  注入四头签名后转发到 `:8010`。网关自身鉴权失败 fail-closed，secret 只存在网关内不落库。
