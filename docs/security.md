# Security

## 本地知识资产

以下数据均属于本地知识资产，默认保留在学校本地：

- 原始文件。
- 解析文本。
- chunk。
- embedding 向量。
- pgvector 索引。
- 检索结果和来源信息。
- 后续 FAQ 答案。

## Phase 4 安全边界

- Embedding 只调用本地 Ollama。
- hybrid search 只在本地 PostgreSQL / pgvector 中执行。
- API 不直接返回 embedding 向量。
- audit_logs 不记录 embedding 向量、不记录完整 chunk 内容、不记录大段检索结果。
- PostgreSQL 和 Redis 默认仅绑定本机。
- 不启用 Web Search、Agent、外部 SaaS 数据源和远程模型供应商。

## MCP

MCP Gateway 当前仍未实现，只保留 placeholder。后续实现时需要独立鉴权、权限校验、脱敏和审计。
