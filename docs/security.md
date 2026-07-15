# Security

> 状态：Current
> 维护日期：2026-07-15

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
- audit_logs 不记录 embedding 向量、不记录完整 chunk 内容、不记录大段检索结果。
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
