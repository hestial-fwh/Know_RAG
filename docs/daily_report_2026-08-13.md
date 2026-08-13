# 每日报告 2026-08-13

## 主题：MCP 接入认证管理（Client Credential + HMAC-SHA256 签名鉴权）

为 Know_RAG 只读 MCP 服务新增一套完整的外部 Client 接入认证能力，在现有 MCP 前增加签名鉴权，
不改动两个既有 Tool。

## 改动清单

### 后端

- 数据模型：`app/db/models.py` 新增 `McpClient`；migration `0015_mcp_clients.py`。
- 配置：`app/core/config.py` 新增 `mcp_client_secret_encryption_key`、`mcp_signature_max_age_seconds`。
- 凭据服务：`app/services/mcp_access/crypto.py`（AES-256-GCM）、`signature.py`（HMAC-SHA256）、
  `nonce_store.py`（Redis SET NX + 内存回退）、`service.py`（CRUD + verify）。
- 管理 API：`app/api/routes_mcp_clients.py`（CRUD + reset-secret + enable/disable）。
- 内部校验 API：`app/api/routes_mcp_access.py`（`/internal/mcp-access/verify`）。
- MCP 鉴权中间件：`app/integrations/mcp/auth_middleware.py`（纯 ASGI，fail-closed）。
- MCP 入口：`http_server.py` 用中间件包裹 `mcp.streamable_http_app()`；`server.py` 传入
  `streamable_http_path`。
- Docker：`Dockerfile` 复制 `pyproject.toml`；`docker-compose.yml` backend 增两个 env；`.env`
  增 `MCP_CLIENT_SECRET_ENCRYPTION_KEY`、`MCP_SIGNATURE_MAX_AGE_SECONDS`。
- 测试客户端：`scripts/test_mcp_signed_client.py`（完整 MCP 会话：initialize → initialized → tools/call）。

### 前端

- `src/api/mcpClients.ts`（API 模块 + 类型）；`src/api/client.ts` 新增 `PATCH`。
- `src/views/McpAccessManagementView.vue`（MCP 接入管理页）。
- 路由 `/settings/mcp-access` + 侧栏「系统 → MCP 接入」。

### 测试

- 后端新增 6 个测试文件，覆盖 crypto / signature / nonce / admin API / verify API / middleware，共 64 用例。

## 验证结果

- 后端专项测试：64/64 通过。
- 全量后端：1076 passed，3 failed（均为 chunker/keyword 模块既有未提交改动导致的先存失败，与本轮无关）。
- ruff：新增文件全部通过（仓库既有的 `test_document_relation_phase2.py` 有 4 个 F821 先存告警）。
- 前端：`npm run check`（typecheck + lint + vitest 9/9）与 `npm run build` 均通过。
- 安全矩阵（真实 MCP 链路）：正确签名 / 错误 secret / 过期 timestamp / 重复 nonce / disabled /
  expired / scope 不足 / body 篡改 / secret 重置，全部符合预期。
- TEST1 真实调用：`list_knowledge_bases` 返回 5 个知识库；`search_context` 返回真实专利 chunk。

## 遗留

- 3 个 chunker/keyword 先存失败测试（非本轮引入），建议后续单独排查。
- 正式部署需轮换 `.env` 中的 `MCP_CLIENT_SECRET_ENCRYPTION_KEY` 与 `KNOW_RAG_SERVICE_TOKEN`，
  并将 MCP 端口收紧为 `127.0.0.1:8010`。
