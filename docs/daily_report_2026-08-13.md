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

---

## 主题：MCP 检索返回升级为 Evidence Group（证据组）

将 `know_rag_search_context` 的返回从「按 Parent 返回扁平上下文」升级为可排序、可编号、可去重的
Evidence Group。Parent 仅作结构边界，不再整块返回。

### 改动清单

- `app/schemas/retrieval.py`：`LLMContextRequest` 补 `return_mode` 及选择性上下文参数
  （此前被 Pydantic 静默丢弃，导致 MCP 调参失效）。
- `app/services/llm_context_builder.py`：新增 `build_evidence_groups_payload` 及一组纯函数
  helper——按 parent 聚合 + 远距命中拆分、局部上下文窗口（hit + ±1 neighbor）、图片优先级
  （`direct_hit > figure_binding > same_parent`）、`asset_id` 全局去重 + 证据合并 + 多组归属、
  `hit_ratio` P3 内部排序、确定性 group 打分。
- `app/services/context/knowledge_context_service.py`：新增 `return_mode="evidence_groups"` 分派。
- `app/integrations/mcp/tools.py` / `server.py`：默认切到 `evidence_groups`，返回 `groups` 并保留
  `chunks` 扁平兜底字段。
- 测试：`tests/test_evidence_groups.py` 覆盖 12 个验收用例。

### 验证结果

- 专项测试：17/17 通过。
- 现有相关测试：134 通过（llm_context_builder + knowledge_context_service）。
- 全量后端：1093 passed，3 failed（chunker/pipeline 先存失败，与本轮无关）。
- ruff：改动文件全部通过。
- TEST1 真实调用（清洁模块 / 图5 / 具体实施方式 / 王羲之书法）：evidence_groups 结构正确，
  无整 Parent 返回，图片 `direct_hit`/`figure_binding` 优先级正确，跨组去重 + 证据合并生效
  （`relation_sources`/`related_group_nos`），图片零重复。

### 修复：图片 chunk 的「上下文：」字段被误删导致同父图片被判低信息

- 问题：`_cloud_clean_content` 把 `上下文：` 行当作「上下文窗口包装标签」统一剥掉；但图片
  chunk 的 content 由 `compose_image_child_content` 生成，`上下文：` 是其**一等内容字段**
  （承载 nearby/OCR 正文）。剥掉后图片内容只剩「章节/图片标题」结构标签，被
  `_cloud_is_low_information_chunk` 的「<120 字且无句末标点」规则误判为低信息，导致同父
  图片从 `media` 中被过滤（王羲之书法场景复现）。
- 修复：`_cloud_clean_content` 增加 `kind` 参数，仅 `kind != "image"` 时剥 `上下文：` 行；
  `_cloud_content` 调用时传入 `kind`。
- 验证：`tests/test_evidence_groups.py` 新增 `TestImageContextLabelPreserved` 4 例；实测
  王羲之图片 chunk（`image-0001-604cf1de23da`）由 `low_info=True` 变为 `False` 并保留 155 字
  OCR 上下文；真占位图（`image-0002-863bba9b599c`，22 字无描述）仍正确过滤。

### 契约收口：Evidence Group 返回结构清理

- 删 `context_text` 空占位：`build_evidence_groups_payload` 不再返回空 `context_text`；
  MCP `search_context` 的 `context_text/sources/images/tables/equations` 仅在非
  evidence_groups 模式且后端实际填充时才带出。
- 修 `chunks` 异构混装：顶层扁平数组拆为同构的 `chunks`（只含 chunk 条目）与 `media`
  （只含图片条目），与 `groups[]` 一一对应，不再把两种 shape 混在一个数组里。
- 清冗余/误导字段：删 group 级 `hit_count`（与 `direct_hit_count` 重复）、media 的
  `dedup_key`（内部去重键外泄）、chunk 的 `original_rank`（实为组内序号，且与 `item_no`
  冗余、语义误导）。
- 验证：`tests/test_evidence_groups.py` 新增 `TestEvidenceGroupsContract` 4 例；全量
  1101 passed，3 failed（chunker/pipeline 先存失败，与本轮无关）；ruff 通过；backend/mcp
  双镜像重建。

### Evidence Group 增加 Pydantic 响应契约

- 新增 `app/schemas/evidence_groups.py`：`EvidenceGroupsResponse` 及
  `EvidenceChunkPayload / EvidenceMediaPayload / EvidenceGroup / EvidenceGroupsMeta`
  五类模型，作为 evidence_groups 返回结构的机器可读契约。
- `build_evidence_groups_payload` 返回前用 `EvidenceGroupsResponse.model_validate(...)`
  校验并 `model_dump()`，schema 与实际输出不再漂移。
- `meta.schema` 字段更名 `meta.schema_version`（`schema` 会 shadow Pydantic
  `BaseModel.schema`，触发告警；`schema_version` 与仓库 relation_graph 命名一致）。
- 验证：`tests/test_evidence_groups.py` 新增 `TestEvidenceGroupsSchema` 2 例；全量
  1103 passed，3 failed（chunker/pipeline 先存失败，与本轮无关）；ruff 通过；backend
  镜像重建。

### 新增 Agent 端 MCP 接入指南（文档）

- 新增 `docs/mcp-client-guide.md`：面向 agent 端开发人员的接入指南，覆盖两种接入方式
  （签名网关 `:8011` 静态 Bearer token / 直连 `:8010` HMAC-SHA256 签名）、canonical 串与
  签名算法、三步握手（initialize → initialized → tools/call）、SSE 响应封套与
  `result.content[0].text` 取数方式、两个工具的完整入参/返回字段表、错误码表与最小可跑示例。
- 内容均基于真实生效代码 + 真实 MCP 链路实测：实测确认响应为 SSE、工具结果序列化在
  `content[0].text` 的 JSON 字符串、`meta.schema_version="evidence_groups.v1"`、`groups`/
  `chunks`/`media`/`meta`/`warnings` 顶层结构。
- 明确「签名 ≠ 加密」：HMAC 保鉴权与完整性不保机密，传输默认 HTTP 明文，生产需前置 TLS。
- `docs/index.md` 增补该指南索引并更新维护日期。

