# MCP 接入：拿到 client_id + client_secret 怎么访问

你只需要三样东西：**`client_id`**、**`client_secret`**、端点。协议 `protocolVersion = 2025-06-18`。凭据由管理端（「系统 → MCP 接入」）签发，agent 端无需自行生成。

**端点**（签名里 `path` 一律是 `/mcp`，与走哪个入口无关）：

| 入口 | URL |
|------|-----|
| 公网 | `http://sqx6.ipyingshe.net/mcp` |
| 局域网直连 | `http://192.168.110.133:8010/mcp` |
| 本机直连 | `http://127.0.0.1:8010/mcp` |

> 用标准 MCP 客户端（Claude Desktop / MCP Inspector / Cursor）做不了逐请求 HMAC 的，走签名网关 `:8011`，见文末备注。

## 1. 签名（每个请求都要算）

```
canonical = "{client_id}\n{timestamp}\n{nonce}\n{METHOD}\n{path}\n{body_sha256}"   // 6 行 \n 连接，末尾无换行
signature = hex( HMAC-SHA256(key = client_secret, msg = canonical) )
```

- `timestamp`：Unix 秒，与服务器时钟差 ≤ 300s
- `nonce`：每请求随机新生成（如 `secrets.token_hex(16)`），复用即重放 → 401
- `path`：实际请求路径，逐字节一致（`/mcp`）
- `body_sha256`：请求体**原始字节**的 SHA-256 小写 hex；空 body = `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

```python
import hashlib, hmac, json, secrets, time, urllib.request
CID, SECRET, URL = "kr_xxx", "<secret>", "http://sqx6.ipyingshe.net/mcp"

def call(envelope, sid=""):
    body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    canonical = "\n".join([CID, ts, nonce, "POST", "/mcp", hashlib.sha256(body).hexdigest()])
    sig = hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-06-18",
               "X-Client-Id": CID, "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig}
    if sid: headers["Mcp-Session-Id"] = sid
    r = urllib.request.urlopen(urllib.request.Request(URL, body, headers, method="POST"), timeout=60)
    return r.status, r.headers.get("Mcp-Session-Id"), r.read().decode()

def parse_sse(raw):
    """SSE 或 application/json 都兼容，返回 JSON 对象。"""
    if raw.lstrip().startswith(("event:", "data:")):      # SSE
        for line in raw.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ValueError("SSE 无 data 行")
    return json.loads(raw)                                 # application/json 兜底
```

## 2. 请求：三步握手

| 步 | method | 说明 |
|----|--------|------|
| 1 | `initialize` | 从响应头 `Mcp-Session-Id` 取会话 ID |
| 2 | `notifications/initialized` | 无 `params`（body 为 `{"jsonrpc":"2.0","method":"notifications/initialized"}`）；返回 202；仍要发、仍要签名 |
| 3 | `tools/call` | 带 `Mcp-Session-Id` |

```python
_, sid, _ = call({"jsonrpc":"2.0","id":0,"method":"initialize","params":{
    "protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"my-agent","version":"0.1"}}})
call({"jsonrpc":"2.0","method":"notifications/initialized"}, sid)
_, _, raw = call({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
    "name":"know_rag_search_context","arguments":{...}}}, sid)
payload = parse_sse(raw)                                    # 解析 SSE / JSON
business = json.loads(payload["result"]["content"][0]["text"])   # 业务对象
```

响应是 **SSE**（`event: message` + `data: {jsonrpc}`）或 `application/json`，两种格式 `parse_sse()` 已统一处理；业务对象在 `payload["result"]["content"][0]["text"]` 里，`json.loads` 后使用。

## 3. 工具（请求字段 / 返回字段）

### 3.1 `know_rag_list_knowledge_bases` — 列知识库

**请求字段** `arguments`：

| 字段 | 类型 | 默认 |
|------|------|------|
| `page` | int | 1 |
| `page_size` | int | 20 |

**返回字段**：工具结果在 `result.content[0].text` 里是 **JSON 字符串**，`json.loads` 后：

```json
{"success":true,"items":[{"id":"9bdbd596-...","name":"TEST1","description":"","document_count":6,"chunk_count":862}],"total":5,"page":1,"page_size":20}
```

### 3.2 `know_rag_search_context` — 检索证据组

**请求字段** `arguments`（仅前两个必填）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `query_text` | str | — | 查询文本（**必填**） |
| `knowledge_base_id` | str | — | 知识库 ID（**必填**，来自 list 的 `id`） |
| `match_count` | int | 10 | 命中数 |
| `retrieval_mode` | str | `hybrid` | 检索模式 |
| `include_images` | bool | `true` | 是否含图片 |
| `include_image_urls` | bool | `false` | 是否带图片 URL |
| `include_tables` | bool | `true` | 是否含表格 |
| `include_equations` | bool | `true` | 是否含公式 |
| `max_context_chars` | int | 8000 | 上下文总字符上限 |
| `max_sources` | int | 8 | 最大来源数 |
| `score_threshold` / `vector_threshold` / `keyword_threshold` | float | `-1.0` | `-1.0` = 不设 |

**返回字段** `json.loads(result.content[0].text)` 后：

```json
{
  "success": true,
  "query_text": "什么是专利",
  "groups": [ ... ],   // 主要输出，按相关度排序
  "chunks": [ ... ],   // 扁平 chunk，与 groups[] 一一对应
  "media": [ ... ],    // 扁平图片，与 groups[] 一一对应
  "meta": { "schema_version": "evidence_groups.v1", "group_count": 3, "top_k": 3, "hit_count": 3 },
  "warnings": []
}
```

**`groups[]` 每个证据组**（一个命中块 + 邻居 + 相关图片，按 `asset_id` 去重）：

```json
{
  "group_no": 1,
  "group_id": "6905db29-...",
  "parent_chunk_id": "6905db29-...",
  "document_id": "...",
  "document_url": "http://<frontend>/documents/<id>/content",
  "score": 0.0298,
  "direct_hit_count": 1,
  "eligible_child_count": 3,
  "hit_ratio": 0.33,
  "has_direct_image": true,
  "chunks": [ { "item_no":"1-1", "chunk_id":"...", "document_id":"...", "content":"...", "chunk_type":"text", "direct_hit":true, "context_reason":"retrieval_hit", "retrieval_score":0.82 } ],
  "media":  [ { "item_no":"1-M1", "asset_id":"...", "chunk_id":"...", "document_id":"...", "content":"章节：...", "primary_reason":"figure_binding", "relation_sources":["figure_binding"], "image_url":"..." } ]
}
```

- `group.chunks[]` 关键：`chunk_id`、`content`、`chunk_type`、`direct_hit`、`context_reason`（`retrieval_hit`/`neighbor`）、`retrieval_score`
- `group.media[]` 关键：`asset_id`、`chunk_id`、`content`、`primary_reason`（`direct_hit` > `figure_binding` > `same_parent`）、`relation_sources`、`image_url`
- 文档归属在 **group 级**：`document_id` + `document_url`（一个 group 一个源文档，`document_url` 指向前端 `/documents/{id}/content` 内容页，域名由 `FRONTEND_BASE_URL` 决定）。chunk / media 条目仍带块级 `document_id`、`document_title`、`file_name` 用于归属标识，但不重复 `document_url`
- `group_no`/`item_no` 只是展示编号，真实身份看 `group_id`/`chunk_id`/`asset_id`
- 完整字段契约见 `docs/api.md`「MCP 上下文返回」

> ⚠️ `document_url` 的域名取自 `FRONTEND_BASE_URL`，当前指向**局域网前端**（示例里 `192.168.110.133:5174`），远程/公网 agent 拿到后打不开；而 `image_url` 走公网 `sqx6.ipyingshe.net`。前端对外部署后把 `FRONTEND_BASE_URL` 改成公网地址，两个链接才会一致可达。

## 4. 完整示例：检索「权利要求」（专利库）

**请求**（第 3 步 `tools/call`，已签名）：

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"know_rag_search_context","arguments":{"query_text":"权利要求","knowledge_base_id":"e532eff1-bbca-4029-aa43-dcd2d6949817","match_count":3,"include_image_urls":true}}}
```

**响应**（SSE，取 `data:` 行后是 JSON-RPC；业务对象在 `result.content[0].text` 里）：

```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{\"success\":true,...}"}],"isError":false}}
```

**`json.loads(result.content[0].text)` 得到的业务对象**（长正文已省略，3 组里取前 2 组）：

```json
{
  "success": true,
  "query_text": "权利要求",
  "groups": [
    {
      "group_no": 1,
      "group_id": "24331284-8565-409b-b0c5-e42e79b092bf",
      "document_id": "5d7cdf37-360b-471c-9a6e-6b1aa985e637",
      "document_url": "http://192.168.110.133:5174/documents/5d7cdf37-360b-471c-9a6e-6b1aa985e637/content",
      "score": 0.016129,
      "direct_hit_count": 1,
      "eligible_child_count": 7,
      "hit_ratio": 0.142857,
      "has_direct_image": false,
      "chunks": [
        {
          "item_no": "1-1",
          "chunk_id": "329aa34d-1c62-4387-a2bc-5360e8f6018f",
          "document_id": "5d7cdf37-360b-471c-9a6e-6b1aa985e637",
          "chunk_type": "text",
          "context_header": "## (57)摘要",
          "content": "权利要求书 2 页 说明书 41 页 附图 21 页\nCN 222885338 U\n1 .一种自动清洁设备，其特征在于，包括清洁刷…（省略）",
          "direct_hit": true,
          "context_reason": "retrieval_hit",
          "retrieval_score": 0.016129
        }
      ],
      "media": []
    },
    {
      "group_no": 2,
      "group_id": "13ed6dda-5888-4cc7-a861-62f25841a147",
      "document_id": "3459a1f1-04f6-4f0e-8993-bb5e5bd06ea2",
      "document_url": "http://192.168.110.133:5174/documents/3459a1f1-04f6-4f0e-8993-bb5e5bd06ea2/content",
      "score": 0.0,
      "direct_hit_count": 1,
      "eligible_child_count": 6,
      "hit_ratio": 0.166667,
      "strong_relation_count": 2,
      "has_direct_image": false,
      "chunks": [
        {
          "item_no": "2-1",
          "chunk_id": "ae9fa30a-3be8-4f53-b875-3288ae6b54c5",
          "document_id": "3459a1f1-04f6-4f0e-8993-bb5e5bd06ea2",
          "chunk_type": "text",
          "context_header": "## 具体实施方式",
          "content": "[0036]本实施例中，所述风扇变速装置包括转动安装在所述低压转子轴 231 上的中心齿轮 27…（省略）",
          "direct_hit": true,
          "context_reason": "retrieval_hit",
          "retrieval_score": 0.0
        }
      ],
      "media": [
        {
          "item_no": "2-M1",
          "asset_id": "image-0002-d2251af1e3f4",
          "chunk_id": "b608d61b-7941-4c60-9b14-6f97f9d3b7b5",
          "document_id": "3459a1f1-04f6-4f0e-8993-bb5e5bd06ea2",
          "page_no": 9,
          "chunk_type": "image",
          "content": "章节：## 具体实施方式\n图1\n图片标题：图1",
          "primary_reason": "figure_binding",
          "relation_sources": ["figure_binding"],
          "figure_ids": ["figure:1"],
          "related_group_nos": [3],
          "image_url": "http://sqx6.ipyingshe.net/files/kb/e532eff1-bbca-4029-aa43-dcd2d6949817/documents/3459a1f1-04f6-4f0e-8993-bb5e5bd06ea2/media/a5f897723cf5d43e43b9efa63c6dd3dfa5b59efc_9dd9e1a470a27451.jpg"
        },
        {
          "item_no": "2-M2",
          "asset_id": "image-0005-3f79cf39b2a3",
          "chunk_id": "81cc0233-e434-41aa-b233-155ba26fba22",
          "page_no": 10,
          "chunk_type": "image",
          "content": "章节：## 具体实施方式\n图5\n图片标题：图5",
          "primary_reason": "figure_binding",
          "relation_sources": ["figure_binding"],
          "figure_ids": ["figure:5"],
          "image_url": "http://sqx6.ipyingshe.net/files/kb/e532eff1-bbca-4029-aa43-dcd2d6949817/documents/3459a1f1-04f6-4f0e-8993-bb5e5bd06ea2/media/eeb20542680d9e3ab5a03227d5115de39c81edea_9c0a5310be57a7e5.jpg"
        },
        {
          "item_no": "2-M3",
          "asset_id": "image-0007-35f5460a6788",
          "chunk_id": "358b75ca-23be-4f49-94fb-de50e855c26f",
          "page_no": 11,
          "chunk_type": "image",
          "content": "章节：## 具体实施方式\n图6\n图7\n图片标题：图6\n图7",
          "primary_reason": "figure_binding",
          "relation_sources": ["figure_binding"],
          "figure_ids": ["figure:6"],
          "image_url": "http://sqx6.ipyingshe.net/files/kb/e532eff1-bbca-4029-aa43-dcd2d6949817/documents/3459a1f1-04f6-4f0e-8993-bb5e5bd06ea2/media/bdebf30cf1168e18af47ff3d3a9cc2c4d8ac16e9_5c27a050c3b9d95e.jpg"
        }
      ]
    }
  ],
  "chunks": [ "…7 项（各组 chunk 的扁平镜像）" ],
  "media": [ "…3 项（各组图片的扁平镜像）" ],
  "meta": { "schema_version": "evidence_groups.v1", "top_k": 3, "group_count": 3, "hit_count": 3 },
  "warnings": []
}
```

这个例子里可以读到：查询「权利要求」返回 3 个证据组（此处省略第 3 组）。组 1 命中专利摘要段的权利要求正文
（`direct_hit=true`，标题 `(57)摘要`）；组 2 命中「具体实施方式」，其附图 1/5/6 以 `primary_reason=figure_binding`
（P2 图绑）关联返回并携带 `figure_ids`；其中图 1 同时归属组 3（`related_group_nos=[3]`），体现跨组去重 + 多组归属。
每个图片带 `image_url`（`include_image_urls=true` 时返回，可直接访问；域名由部署配置决定）。
每个 group 带 `document_id` 和 `document_url`（`document_url` 指向该组源文档内容页 `/documents/{id}/content`，域名由 `FRONTEND_BASE_URL` 决定，`document_id` 为空时为 `""`）；组 1 源文档是摘要那篇，组 2 源文档是具体实施方式那篇。chunk / media 条目带块级 `document_id`（用于归属标识，个别图绑图片可能为空），不再重复 `document_url`。
顶层 `chunks`/`media` 仍是 `groups[]` 的扁平镜像。

## 5. 错误码

失败返回 `{"jsonrpc":"2.0","id":null,"error":{"code":-32001,"message":"MCP access denied: <原因>"}}`

| HTTP | 原因 |
|------|------|
| 401 | 缺签名头 / client 不存在 / 时间戳过期 / nonce 重放 / 签名错 |
| 403 | client 禁用或过期 / scope 不足 |
| 413 | 请求体 > 10MB |
| 503 | 后端校验不可用（fail-closed） |

## 6. 备注：签名网关（可选）

标准 MCP 客户端只能带静态 header，做不了逐请求 HMAC。部署方可用签名网关把 HMAC 包掉：
客户端用 `Authorization: Bearer <GATEWAY_TOKEN>` 连 `http://127.0.0.1:8011/mcp`（网关仅绑定本机，不对外），网关注入签名后转发到真实 MCP，返回与直连完全一致。
