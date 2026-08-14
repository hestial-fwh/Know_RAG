# 每日报告 2026-08-14

## 主题：MCP 上下文返回新增 document_url + Agent 接入指南收尾

在 evidence_groups 返回契约中补上文档内容页链接 `document_url`，并把 Agent 端 MCP
接入指南补全到「照抄即通」的程度。

## 改动清单

### document_url 字段（后端）

- 契约：`app/schemas/evidence_groups.py` 的 `EvidenceChunkPayload` /
  `EvidenceMediaPayload` 各增 `document_url: str = ""`（置于 `document_id` 之后）。
- 构建：`app/services/llm_context_builder.py` 的 `_evidence_chunk_payload` /
  `_evidence_media_payload` 增 `document_url`，复用已有的 `_frontend_document_url()`
  （拼 `{FRONTEND_BASE_URL}/documents/{id}/content`）；`document_id` 提取改为局部变量
  避免重复计算。
- 配置：`.env` 增 `FRONTEND_BASE_URL=http://192.168.110.133:5174`；`docker-compose.yml`
  的 backend 服务 `environment` 增 `FRONTEND_BASE_URL: ${FRONTEND_BASE_URL:-http://localhost:5174}`。
  （MCP 进程通过 `env_file` 已能读到该变量，但上下文实际由 backend 构建，backend 只注入
  显式列出的 env，故需补上这一行——否则 `document_url` 会落到 config 默认的 localhost。）

### 测试

- `tests/test_evidence_groups.py` 新增 `TestDocumentUrl` 2 例：带 `document_id` 时
  chunk/media 的 `document_url` 正确拼出；无 `document_id` 时为空串。

### 文档

- `docs/mcp-client-guide.md`：补 `parse_sse()` 代码（SSE / application/json 统一解析）、
  真实端点表（公网 `sqx6.ipyingshe.net/mcp` / 局域网 `192.168.110.133:8010` / 本机）、
  凭据来源说明、第 3 张图（2-M3 图6）与 `document_url` 字段说明。
- `docs/api.md`：「MCP 上下文返回（Evidence Group）」补 chunk/media 文档归属字段说明。
- `docs/index.md`：更新维护日期。

## 验证结果

- 专项测试：`tests/test_evidence_groups.py` 29/29 通过（含新增 2 例）。
- 全量后端：1105 passed，3 failed（chunker/pipeline 先存失败，与本轮无关）。
- ruff：改动文件全部通过。
- 重建 backend/mcp 镜像后真实 MCP 链路验证：三步握手 + 签名检索返回 200，
  `document_url` 正确返回（`http://192.168.110.133:5174/documents/{id}/content`），
  `image_url` 可下载（200），域名 `sqx6.ipyingshe.net` 与文档一致；公网隧道已恢复
  （未签名请求返回 401 而非 502）。

## 追加修正：document_url 上移到 group 级

反馈「一个 group 应该就一个 document_url」后调整了契约：`document_url` 不再逐 chunk/media
条目返回，而是上移到 group 级（一个 group 一个源文档链接），条目级只保留块级
`document_id`/`document_title`/`file_name` 用于归属标识。

- 契约：`schemas/evidence_groups.py` 的 `EvidenceGroup` 增 `document_id`/`document_url`，
  `EvidenceChunkPayload`/`EvidenceMediaPayload` 去掉 `document_url`。
- 构建：`llm_context_builder.py` 组装 group 时取「第一个 direct hit 的 `document_id`」
  作为主文档（无 hit 的孤立图片组回退到 media），拼 `document_url`；条目 payload 去掉
  `document_url`。
- 根因：figure_binding 是跨 parent 的绑定，`_attach_binding_related_chunks` 拉图块时
  SQL 未查 `document_id`，导致个别图片块 `document_id` 缺失；逐条目放 `document_url`
  会让同一 group 出现「文本块有 url、图片块 url 空」的不一致。上移后 group 级主文档
  始终确定，不受个别图块字段缺失影响。
- 测试：`TestDocumentUrl` 改为断言 group 级 `document_url` 存在、条目级无 `document_url`。
- 文档：`docs/mcp-client-guide.md`、`docs/api.md` 同步（第 4 节真实示例组 1 主文档
  `5d7cdf37`、组 2 主文档 `3459a1f1`，由 DB 实查确认）。

## 遗留

- `document_url` 依赖 `FRONTEND_BASE_URL`，当前指向局域网前端 `:5174`；前端对外部署后需
  改为公网地址，否则远程 agent 拿到的链接不可达。
- 部分 figure_binding 图片 chunk（如 2-M3 图6）`document_id` 为空——属数据层差异，非本轮引入。
  `document_url` 已上移到 group 级，条目级 `document_id` 为空不影响 group 级主文档链接。
