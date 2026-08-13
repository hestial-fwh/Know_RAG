# Security

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
