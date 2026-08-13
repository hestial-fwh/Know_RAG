# MCP 接入认证管理 — 设计与实施计划

> 日期：2026-08-13
> 状态：已实施并验收（安全矩阵 12/12、专项测试 64/64、前端 check+build 通过、TEST1 真实调用成功）
> 范围：一期 Client Credential + HMAC-SHA256 签名鉴权；不做 OAuth2 / IP 白名单 / 限流 / 复杂 RBAC。

---

## 1. 当前 MCP 架构（修改前）

```
外部 MCP Client ──①──▶ MCP server (:8010/mcp, FastMCP streamable-http) ──②──▶ Know_RAG Backend (:8000)
```

| 跳 | 现状 | 代码证据 |
|----|------|----------|
| ① 客户端 → MCP | **无任何鉴权**。`mcp.run(transport="streamable-http")` 裸跑，无 middleware | `backend/app/integrations/mcp/http_server.py` |
| ② MCP → Backend | 有鉴权：`KNOW_RAG_API_TOKEN`（service token，映射 admin、绕过验证码）或 `ADMIN_USERNAME/PASSWORD` 登录 | `backend/app/integrations/mcp/http_client.py` + `backend/app/core/security.py::_is_service_token` |

- MCP SDK：`mcp==1.12.4`（官方 Python SDK）。`FastMCP.streamable_http_app()` 返回**完整 Starlette app**（内部已 mount `/mcp`），`mcp.run(transport="streamable-http")` 只是对其执行 `uvicorn.run`。因此可以用**纯 ASGI middleware 包裹**，不触碰 MCP 协议本身。
- 端口：`docker-compose.yml` 中 mcp 服务 `ports: "8010:8010"`（0.0.0.0 全网卡暴露）。
- Streamable HTTP 客户端可自定义 HTTP Header：协议不限制额外 header；httpx / Claude Desktop / Cursor 等均可携带自定义头 → `X-Client-Id` 等签名头方案可行。
- MCP Tool 注册：`backend/app/integrations/mcp/server.py`，两个 tool：`know_rag_list_knowledge_bases`、`know_rag_search_context`（本轮保持不动）。
- Redis：`redis==5.2.1`，已有使用先例 `backend/app/services/captcha_service.py`（Redis 优先 + 内存回退的容错模式），本方案沿用同一容错思路，用 `SET NX + TTL` 做 nonce 防重放。
- ORM：SQLAlchemy 2.0 `DeclarativeBase`，模型集中在 `backend/app/db/models.py`；migration 为手写 SQL（`backend/app/db/migrations/versions/`，最新 `0014`）。
- Backend API 风格：`APIRouter(prefix=...)` + `Depends(get_current_user)` + `success_response/error_response`（`app/core/response.py`），service 层与 routes 分离，schemas 在 `app/schemas/`。
- Frontend：Vue3 + TS。`frontend/src/api/client.ts` 统一 fetch 封装（**当前无 PATCH，需要补**）；全局样式类 `.page/.card/.data-table/.btn/.badge/.modal/.empty-state`；侧栏菜单在 `MainLayout.vue`「系统」分组。

---

## 2. 目标架构

两套认证明确分工，**互不替代**：

| 认证 | 负责段 | 实现 | 变更 |
|------|--------|------|------|
| **内部服务认证**（保留） | MCP → Backend | `KNOW_RAG_SERVICE_TOKEN`（`_is_service_token` → admin 用户） | 不变，继续用于 MCP 调 backend、后续内部服务 |
| **外部 Client Credential**（新增） | 客户端 → MCP | HMAC-SHA256 签名 + timestamp + nonce + scope | 新增，强制覆盖所有 `/mcp` HTTP 请求 |

```
Know_RAG 管理后台（新增 MCP 接入管理页）
        ↓ 创建 Client（admin API）
Client ID + Client Secret（secret 仅返回一次，DB 只存密文）
        ↓
外部 MCP Client
  X-Client-Id / X-Timestamp / X-Nonce / X-Signature
        ↓
MCP Auth Middleware（新增，ASGI 层，位于 FastMCP app 之前）
  → 提取签名头 + body SHA256 + tool name
        ↓ HTTP 调用（service token 保护）
Backend 内部校验端点 POST /api/v1/internal/mcp-access/verify
  1. 查询 Client      2. is_enabled   3. expires_at
  4. timestamp ±300s  5. nonce 防重放 6. HMAC 验签
  7. Tool Scope       8. last_used_at
        ↓ 放行 / 401 / 403
现有 MCP Tools → Know_RAG Backend
```

**设计要点：**

1. **Secret 只存在 backend**。MCP 容器不持有任何 client secret，也不直连数据库（其 `env_file: .env` 虽含 DATABASE_URL，但本轮不让 MCP 进程使用）。
2. **验签集中在一个后端内部端点**，MCP middleware 只做「提取 + 转发 + 按结果放行」，保证 canonicalization 只有一份实现（`app/services/mcp_access/signature.py`），测试客户端脚本直接 import 同一实现，杜绝前后端不一致。
3. 校验端点用 service token 保护（沿用 `_is_service_token`），与现有 MCP→backend 调用同一信任边界。
4. middleware 失败时**fail-closed**（backend 不可达 = 拒绝），错误响应为标准 JSON-RPC error（HTTP 401/403 + `{"jsonrpc":"2.0","id":null,"error":{"code":-32001,...}}`）。

---

## 3. 数据模型

新增表 `mcp_clients`（Alembic migration `0015_mcp_clients`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) PK | `uuid4()`（`app.utils.ids.new_id`） |
| name | VARCHAR(255) NOT NULL | 应用名称 |
| client_id | VARCHAR(64) UNIQUE NOT NULL | `kr_` + `secrets.token_hex(16)`，全局唯一 |
| client_secret_encrypted | VARCHAR(512) NOT NULL | `v1:` + base64(nonce‖AESGCM密文)，禁止明文 |
| is_enabled | BOOLEAN NOT NULL DEFAULT true | 启用/禁用 |
| expires_at | TIMESTAMPTZ NULL | NULL = 永久有效 |
| scopes | JSON NOT NULL DEFAULT `'[]'::json` | Tool 名数组，取值 ⊂ {`know_rag_list_knowledge_bases`, `know_rag_search_context`}，创建时至少 1 个 |
| description | TEXT DEFAULT '' | 描述 |
| last_used_at | TIMESTAMPTZ NULL | 每次验签成功更新 |
| created_at / updated_at | TIMESTAMPTZ | 项目通用 `server_default=now()` 风格 |

ORM：`McpClient` 加入 `backend/app/db/models.py`（沿用 `Mapped`/`mapped_column` 风格）。

## 4. Secret 安全存储

- 生成：`secrets.token_urlsafe(32)`。
- 加密：AES-256-GCM（`cryptography` 库，容器内已验证可用）。
  - 密钥 = SHA256(`MCP_CLIENT_SECRET_ENCRYPTION_KEY` 环境变量值)（32 字节 Master Key 派生）。
  - 密文格式：`v1:` + base64( 12B nonce ‖ ciphertext+tag )，带版本前缀便于将来轮换。
- 约定：创建 / 重置 Secret 时 API 返回一次明文；list/detail **永不**返回 secret；忘记只能重置。
- Master Key 从环境变量读取，不硬编码；开发默认值沿用项目惯例（如 `JWT_SECRET=dev-only-change-me` 风格），`.env` 中写入随机生成值。

## 5. 请求签名协议

请求头：

```
X-Client-Id    kr_xxxx（client_id）
X-Timestamp    Unix 秒（UTC）
X-Nonce        客户端生成的随机 hex（≥16 字节熵）
X-Signature    小写 hex HMAC-SHA256
```

Canonical String（6 行，`\n` 分隔，无尾部换行）：

```
{client_id}
{timestamp}
{nonce}
{METHOD}            # 大写，如 POST / GET
{path}              # 原始 path（如 /mcp），不含 query string
{body_sha256}       # 请求体原始字节的 SHA256，小写 hex；GET/空体 = sha256(b"")
```

签名：

```
signature = hex(HMAC-SHA256(key=client_secret, msg=canonical_string))
```

校验规则：

| 检查 | 规则 | 失败响应 |
|------|------|----------|
| Client 存在 | 按 client_id 查询 | 401 invalid client |
| 启用 | `is_enabled == True` | 403 client disabled |
| 有效期 | `expires_at is None or expires_at > now` | 403 client expired |
| Timestamp | `\|now - timestamp\| ≤ MCP_SIGNATURE_MAX_AGE_SECONDS`（默认 300） | 401 timestamp expired |
| Nonce | Redis `SET NX mcp:nonce:{client_id}:{nonce} EX 600`（TTL=2×窗口）；重复 → 拒绝 | 401 nonce reused |
| 签名 | 解密 secret → 重算 HMAC → `hmac.compare_digest` | 401 signature mismatch |
| Tool Scope | 仅 `tools/call` 请求：从 JSON-RPC body 的 `params.name` 提取 tool 名（middleware 层解析；非 tools/call 如 initialize/tools/list 跳过 scope 检查，但仍需有效签名） | 403 tool not allowed |

Middleware 提取 tool name 的方式：body 为 JSON-RPC 2.0 时读 `method == "tools/call"` 的 `params.name`。这是对标准 JSON-RPC payload 的结构化解析，不是脆弱字符串路径；解析失败时 tool_name 为空，由 backend 按「无 scope 要求的请求」处理（仍验签）。

## 6. 后端 API

### 6.1 管理 API（`/api/v1/admin/mcp-clients`，`Depends(get_current_user)`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/mcp-clients` | 分页列表（page/page_size），含计算字段 `status`（`enabled`/`disabled`/`expired`）；**无 secret** |
| POST | `/admin/mcp-clients` | 创建 `{name, description?, scopes[], expires_in_days?}`（null=永久）；响应含 `client_secret`（唯一一次） |
| GET | `/admin/mcp-clients/{id}` | 详情；**无 secret** |
| PATCH | `/admin/mcp-clients/{id}` | 编辑 `{name?, description?, scopes?, expires_in_days?}`（null=永久，数字=从当前起算） |
| POST | `/admin/mcp-clients/{id}/reset-secret` | 重置；旧 secret 立即失效；响应含新 `client_secret`（唯一一次） |
| POST | `/admin/mcp-clients/{id}/enable` | 启用 |
| POST | `/admin/mcp-clients/{id}/disable` | 禁用 |
| DELETE | `/admin/mcp-clients/{id}` | 删除，凭证立即失效 |

- `expires_in_days` 语义：null/省略 = 永久；正数 N = now + N 天（前端自定义日期按天数取整转换）。
- scopes 校验：必须是合法 tool 名集合的非空子集。
- 错误沿用 `HTTPException` + `error_response`（`main.py` 已有统一异常处理）。

### 6.2 内部校验 API（`/api/v1/internal/mcp-access`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/internal/mcp-access/verify` | 仅 service token 可调。请求 `{client_id, timestamp, nonce, signature, method, path, body_sha256, tool_name?}` → 按第 5 节规则校验 → `{allowed: true/false, reason, client_id, name, scopes}` |

### 6.3 新增文件

| 文件 | 内容 |
|------|------|
| `backend/app/services/mcp_access/__init__.py` | 包 |
| `backend/app/services/mcp_access/crypto.py` | AES-GCM 加解密 |
| `backend/app/services/mcp_access/signature.py` | canonical string + HMAC 生成/校验（**唯一实现**） |
| `backend/app/services/mcp_access/nonce_store.py` | Redis `SET NX` + 内存回退（仿 captcha_service） |
| `backend/app/services/mcp_access/service.py` | 管理 CRUD + verify 业务逻辑 |
| `backend/app/api/routes_mcp_clients.py` | 管理 API |
| `backend/app/api/routes_mcp_access.py` | 内部 verify API |
| `backend/app/schemas/mcp_client.py` | Pydantic schemas |
| `backend/app/integrations/mcp/auth_middleware.py` | 纯 ASGI 鉴权 middleware |
| `backend/scripts/test_mcp_signed_client.py` | 签名测试客户端 |
| `backend/tests/test_mcp_access_*.py` | 自动测试（见第 8 节） |

### 6.4 修改文件

| 文件 | 改动 |
|------|------|
| `backend/app/db/models.py` | 新增 `McpClient` |
| `backend/app/db/migrations/versions/0015_mcp_clients.py` | 建表 |
| `backend/app/core/config.py` | `mcp_client_secret_encryption_key`、`mcp_signature_max_age_seconds` |
| `backend/app/integrations/mcp/server.py` | `_create_mcp()` 传入 `streamable_http_path`（读 `MCP_MOUNT_PATH`） |
| `backend/app/integrations/mcp/http_server.py` | 用 middleware 包裹 `mcp.streamable_http_app()` 后 `uvicorn.run` |
| `backend/app/main.py` | 注册两个新 router |
| `docker-compose.yml` | backend 环境增加 `MCP_CLIENT_SECRET_ENCRYPTION_KEY`、`MCP_SIGNATURE_MAX_AGE_SECONDS` |
| `.env` | 新增两个配置（key 为随机生成值） |

---

## 7. 前端页面设计

路由：`/settings/mcp-access`（加入 MainLayout「系统」分组：「MCP 接入」）。

页面 `frontend/src/views/McpAccessManagementView.vue`，沿用现有后台风格（`.page/.card/.data-table/.btn/.badge/.modal` + PageHeader/StatusBadge/EmptyState 组件）：

**列表表格列**：应用名称 / Client ID（带复制按钮）/ 权限（scope badge 列表）/ 状态（Badge：启用中 success、已禁用 error、已过期 warning）/ 有效期（日期或「永久」）/ 最后调用时间 / 创建时间 / 操作（编辑、启用/禁用、重置 Secret、删除）。

**创建弹窗**（「+ 创建接入」）：
- 应用名称 *（必填）
- 描述（选填）
- 有效期：○30 天 ○90 天 ○1 年 ○自定义（天数输入） ○永久
- MCP Tool 权限：☑ list_knowledge_bases ☑ search_context（默认全选）

**Secret 一次性展示弹窗**（创建 / 重置后）：
- 展示 Client ID + Client Secret，各带复制按钮
- 醒目提示：「Client Secret 仅展示一次，请立即保存。关闭后无法再次查看，遗忘需重置。」
- 关闭后 secret 从内存清空（不保留在组件状态）

**交互约束**：
- 编辑：名称/描述/有效期/权限
- 重置 Secret：二次确认，文案明确「旧 Secret 将立即失效」
- 禁用/删除：均有确认；禁用可恢复，删除后凭证立即失效
- Loading / Error / Empty 状态完整；无控制台报错

API 模块：`frontend/src/api/mcpClients.ts`；`client.ts` 增加 `PATCH`。

---

## 8. 测试方案

### 8.1 自动测试（`backend/tests/`，pytest）

| 文件 | 覆盖 |
|------|------|
| `test_mcp_access_crypto.py` | 加解密往返、错误 key 失败、密文篡改失败、格式 |
| `test_mcp_access_signature.py` | canonical 串构造、HMAC 生成、验签通过/失败、timestamp 过期、body hash 不一致 |
| `test_mcp_access_nonce_store.py` | Redis SET NX 语义（注入 fake client）、内存回退、TTL |
| `test_mcp_access_admin_api.py` | **集成**（FastAPI TestClient + SQLite in-memory + dependency override）：CRUD、client_id 唯一、secret 只返回一次、list/detail 不含 secret、reset 后旧失效、enable/disable、过期状态 |
| `test_mcp_access_verify_api.py` | **集成**（同上 + override service token 路径）：正确签名通过；错误 secret / 错误 signature / 过期 timestamp / 重复 nonce / disabled / expired / scope 不足 / body 篡改（签名后改 body hash）/ reset 后旧新验证 → 各返回预期状态码 |
| `test_mcp_access_middleware.py` | 纯 ASGI：stub inner app + monkeypatch verify 调用；放行时 body 完整透传（下游可读 body）、拒绝时 401/403 JSON-RPC 错误、非 /mcp 路径跳过、GET 空 body |

### 8.2 安全测试样例（真实链路）

用 `backend/scripts/test_mcp_signed_client.py` 对真实 MCP 执行：

| 场景 | 预期 |
|------|------|
| 正确签名 | 200 / MCP 正常响应 |
| 错误 Secret | 401 拒绝 |
| 错误 Signature | 401 拒绝 |
| 过期 Timestamp（-600s） | 401 拒绝 |
| 重复 Nonce（第 2 次） | 401 拒绝 |
| Client Disabled | 403 拒绝 |
| Client Expired | 403 拒绝 |
| Scope 不足（仅 list 权限调 search） | 403 拒绝 |
| Body 篡改（签名后改参数） | 401 拒绝 |
| Secret Reset 后：旧 secret 失败 / 新 secret 成功 | 401 / 200 |

### 8.3 测试客户端脚本

`backend/scripts/test_mcp_signed_client.py`：

- 配置：`MCP_URL`（默认 `http://localhost:8010/mcp`）、`CLIENT_ID`、`CLIENT_SECRET`（或 `--client-id/--client-secret` 参数）
- 自动完成：timestamp、nonce、body hash、HMAC 签名、HTTP 请求（import `app.services.mcp_access.signature` 保证 canonicalization 一致）
- Case A：`know_rag_list_knowledge_bases`；Case B：`know_rag_search_context`（TEST1 知识库真实 query）
- 负例开关：`--no-sign`、`--timestamp-offset -600`、`--fixed-nonce`、`--tamper-body`、`--wrong-secret`

### 8.4 前端验收（逐项执行并记录）

1. 创建：名称「TEST MCP Client」、30 天、双 scope → ID/Secret 生成、只展示一次、复制可用
2. 列表：刷新后存在、不显示 Secret、状态/有效期/权限正确
3. 真实调用：用页面创建的凭证跑测试客户端，两个 Tool 均成功
4. 禁用 → MCP 失败；启用 → 恢复
5. 重置 Secret：旧 fail / 新 success
6. 权限：去掉 search_context → list 成功 / search 403
7. 删除 → 凭证失效

### 8.5 最终回归

```bash
docker compose exec backend python -m pytest tests/ -v      # 记录 passed/failed/新增失败
docker compose exec backend ruff check app tests            # 若环境有 ruff
cd frontend && npm run check && npm run build               # type-check + lint + vitest + build
```

---

## 9. 部署安全说明

- 开发环境：`:8010/mcp` 本地直接验证（本轮不因 HTTPS 阻塞功能开发）。
- 正式部署建议：`Internet → HTTPS Gateway/Nginx → 127.0.0.1:8010/mcp`；`docker-compose.yml` 的 mcp 端口可改为 `127.0.0.1:8010:8010`（写进部署文档，不阻塞本轮）。
- `.env` 新增：`MCP_CLIENT_SECRET_ENCRYPTION_KEY`（随机生成，正式环境必须更换）、`MCP_SIGNATURE_MAX_AGE_SECONDS=300`。

## 10. 兼容性

- `KNOW_RAG_SERVICE_TOKEN`（内部服务认证）与 username/password retry **原样保留**，继续负责 MCP→backend 与 verify 端点保护。
- stdio transport 不经过 HTTP middleware，行为不变（本地进程场景）。
- streamable-http 所有请求（POST/GET）强制签名，无任何旁路。

## 11. 一期不做

OAuth2 / IP 白名单 / QPS 限流 / 调用额度 / 复杂 RBAC / 多 Secret 并行轮换 / 完整审计平台 / Body AES 加密。

## 12. 验收标准（映射）

1 后台创建 Client ✅ → 6.1 POST
2 自动生成 ID/Secret ✅ → service 层
3 Secret 只展示一次 ✅ → 前端弹窗 + API 语义
4 DB 不明文 ✅ → crypto.py
5 有效期 ✅ → expires_at
6 启用/禁用 ✅ → enable/disable
7 Tool Scope ✅ → scopes + verify
8 重置 Secret ✅ → reset-secret
9 HMAC 验签 ✅ → signature.py
10 Timestamp 过期拒绝 ✅
11 Nonce 重放拒绝 ✅
12 Body 篡改失效 ✅
13 无权限拒绝 ✅
14 删除后失效 ✅
15 前端流程完整 ✅ → Phase 9
16 原两个 Tool 正常 ✅ → Phase 10
17 TEST1 search_context 成功 ✅ → Phase 10
18 全量测试无新增回归 ✅ → Phase 11
19 前端 build 成功 ✅ → Phase 11
20 设计文档 + 使用说明 ✅ → 本文档 + docs/security.md、docs/api.md、docs/frontend.md、docs/database.md、docs/architecture.md、docs/index.md、docs/daily_report_2026-08-13.md
