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

## 召回上下文重叠归一化（消除相邻 Chunk 重复展示）

### 问题与根因

索引/向量化允许 chunk overlap（`chunk_overlap` + `source_child_overlap` /
`continuation_across_floating_blocks`），同 parent 下相邻子块 source span 重叠；两个相邻
chunk 同时被召回时，context/MCP 会把同一段文本展示两遍（如 `[0027][0028]` 与 `[0027][0028][0029]`）。

DB 实测：文本子块 3034 个，`source_position_is_exact=true` 100%，`len(content)` 与
`end-start` 相差 ≤10 的占 99.8%。相邻对 2452 组中 1819 组（74%）存在重叠，其中 ≥80% 高重叠 27 组、
30%~80% 中等重叠 807 组。确认是切块真实 overlap，非数据库重复写入。

### 改动（只加一层，不动切块/索引/向量/BM25/RRF/rerank/FigureLinker/Relation Graph）

- 新增 `app/services/retrieval_context_normalizer.py`：纯函数 `normalize_spans`，按
  `start_at`/`end_at` offset 去重。direct hit 为 anchor 永不裁，低优先级 chunk 裁掉与已认领
  span 的前缀/后缀重叠；完全包含则 content 置空但保留 chunk_id/evidence。按 `group_key`
  （document+parent）隔离，跨 parent 与 standalone root 不归一化；非 text 不参与。
- `llm_context_builder.py`：`build_evidence_groups_payload` 与 `build_cloud_flat_chunks_payload`
  在输出前按 group 调用归一化，把裁后的 content 写入 chunk，并附加 `overlap_trimmed` /
  `trimmed_chars` / `overlap_source_chunk_ids` 标记。
- `retrieval_service.py`：`_row_child_summary` / `_compact_child_from_result` 补 `start_at`/
  `end_at`（parent_children / matched_children 透传 offset），供归一化读 offset。
- `schemas/evidence_groups.py`：`EvidenceChunkPayload` 增 `overlap_trimmed`/`trimmed_chars`/
  `overlap_source_chunk_ids` 三字段（默认 False/0/[]，向后兼容）。
- 新增 `scripts/analyze_retrieval_overlap.py` 诊断脚本（`--kb`/`--document-id`/
  `--min-overlap-ratio`/`--show-content`），用于 baseline 与回归。
- 文档：`docs/api.md`、`docs/index.md`、`docs/mcp-client-guide.md`、
  `docs/retrieval_context_overlap_normalization_plan.md`。

### 验证

- 专项：`tests/test_retrieval_context_normalizer.py` 17 例全通过（覆盖 12 case + 集成）。
- 全量：1122 passed，3 failed（chunker/pipeline 先存失败，与本轮无关）。
- ruff：改动文件全部通过。
- 真实库回归：查询「自动清洁设备 清洁刷」返回的 evidence group 中，相邻 hit
  `9ac194b7` 被裁 240 字符（`overlap_source_chunk_ids=[28263738]`），重复 span 只保留一次。

### 范围约束（未修改）

Chunk overlap 参数、Canonical Parent-Child 切分、Embedding、ES 索引、BM25、Vector Search、
RRF、Rerank、FigureLinker、Relation Graph、Media Parent Assignment、图片绑定——均未修改。
`hybrid-search` 的 `results`（raw retrieval hit）保持原样；只在 llm-context / MCP context
展示层归一化。
