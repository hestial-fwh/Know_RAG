# Know_RAG MCP 接口文档

## 1. 功能概述

Know_RAG MCP Server 用于将本地知识库检索能力通过 MCP 协议暴露给外部 Agent / 大模型系统。

当前 MCP 第一版只提供只读检索能力，不提供上传、删除、重建索引等写操作能力。

核心能力：

```text
用户问题
↓
MCP Tool 调用
↓
Know_RAG 本地知识库检索
↓
父子块聚合
↓
LLM Context Builder 清洗上下文
↓
返回 context_text / sources / images / tables / equations
↓
云端 Agent 基于上下文回答
```

---

## 2. 支持的 Transport

当前支持两种 MCP Transport。

### 2.1 Stdio MCP

适合本地 MCP Client，例如 MCP Inspector、Claude Desktop、Cursor 本地模式。

启动命令：

```bash
docker exec -i know-rag-backend python -m app.integrations.mcp.server
```

注意：

```text
MCP stdio 模式不要使用 -t，只使用 -i。
```

---

### 2.2 Streamable HTTP MCP

适合远程 Agent / 云端 Agent 通过网络访问。

当前本地测试地址：

```text
http://127.0.0.1:8000/mcp
```

如果使用独立 MCP 服务，也可以配置为：

```text
http://127.0.0.1:8010/mcp
```

正式部署时建议使用 HTTPS：

```text
https://your-domain.com/mcp
```

---

## 3. MCP Server 信息

服务名称：

```text
know-rag
```

当前版本：

```text
MCP Inspector 中显示版本：1.12.4
```

当前工具数量：

```text
2 个
```

工具列表：

```text
know_rag_list_knowledge_bases
know_rag_search_context
```

---

## 4. 工具一：know_rag_list_knowledge_bases

### 4.1 功能说明

列出当前 Know_RAG 中可用的知识库，用于让外部 Agent 获取 `knowledge_base_id`。

### 4.2 输入参数

| 参数        | 类型      | 必填 | 默认值 | 说明   |
| --------- | ------- | -: | --: | ---- |
| page      | integer |  否 |   1 | 页码   |
| page_size | integer |  否 |  20 | 每页数量 |

### 4.3 请求示例

```json
{
  "page": 1,
  "page_size": 20
}
```

### 4.4 返回示例

```json
{
  "success": true,
  "items": [
    {
      "id": "cf2293d9-a999-40b7-a06f-9fd7ff207fe7",
      "name": "论文知识库",
      "document_count": 1
    }
  ]
}
```

### 4.5 使用场景

外部 Agent 不知道具体知识库 ID 时，应先调用该工具获取可用知识库列表。

---

## 5. 工具二：know_rag_search_context

### 5.1 功能说明

根据用户问题检索指定知识库，并返回清洗后的大模型上下文。

该工具不会返回原始 chunk、调试分数、数据库字段或完整 metadata，而是返回适合大模型直接使用的结构化上下文。

内部链路：

```text
know_rag_search_context
↓
Know_RAG /llm-context
↓
hybrid_search
↓
parent context 聚合
↓
LLM Context Builder
↓
context_text / sources / images / tables / equations
```

---

### 5.2 输入参数

| 参数                         | 类型      | 必填 |      默认值 | 说明                                   |
| -------------------------- | ------- | -: | -------: | ------------------------------------ |
| query_text                 | string  |  是 |        - | 用户问题                                 |
| knowledge_base_id          | string  |  是 |        - | 知识库 ID                               |
| retrieval_mode             | string  |  否 |   hybrid | 检索模式，当前推荐 hybrid                     |
| match_count                | integer |  否 |       10 | 初始召回数量                               |
| score_threshold            | number  |  否 |      0.3 | 综合分数阈值                               |
| vector_threshold           | number  |  否 |      0.3 | 向量检索阈值                               |
| keyword_threshold          | number  |  否 |      0.3 | 关键词检索阈值                              |
| include_parent_context     | boolean |  否 |     true | 是否返回父块上下文                            |
| parent_context_mode        | string  |  否 | standard | 父块上下文模式，支持 compact / standard / full |
| parent_context_max_chars   | integer |  否 |     2000 | 每个父块最多返回字符数                          |
| neighbor_context_max_chars | integer |  否 |      300 | 前后邻居上下文最大字符数                         |
| parent_children_limit      | integer |  否 |       10 | 每个父块最多返回相关子块数                        |
| max_parent_contexts        | integer |  否 |        5 | 最多返回父块数量                             |
| merge_hits_by_parent       | boolean |  否 |     true | 是否按 parent_chunk_id 聚合命中             |
| max_context_chars          | integer |  否 |     8000 | 最终 context_text 最大字符数                |
| max_sources                | integer |  否 |        8 | 最多资料来源数量                             |
| include_images             | boolean |  否 |     true | 是否包含图片 / 图表语义证据                      |
| include_image_urls         | boolean |  否 |    false | 是否返回图片 URL                           |
| include_tables             | boolean |  否 |     true | 是否包含表格证据                             |
| include_equations          | boolean |  否 |     true | 是否包含公式证据                             |

---

### 5.3 推荐请求参数

```json
{
  "query_text": "退移速率曲线说明了什么？",
  "knowledge_base_id": "cf2293d9-a999-40b7-a06f-9fd7ff207fe7",
  "retrieval_mode": "hybrid",
  "match_count": 10,
  "score_threshold": 0.3,
  "vector_threshold": 0.3,
  "keyword_threshold": 0.3,
  "include_parent_context": true,
  "parent_context_mode": "standard",
  "parent_context_max_chars": 2000,
  "neighbor_context_max_chars": 300,
  "parent_children_limit": 10,
  "max_parent_contexts": 5,
  "merge_hits_by_parent": true,
  "max_context_chars": 8000,
  "max_sources": 8,
  "include_images": true,
  "include_image_urls": false,
  "include_tables": true,
  "include_equations": true
}
```

---

### 5.4 返回字段说明

| 字段             | 类型      | 说明          |
| -------------- | ------- | ----------- |
| success        | boolean | 工具调用是否成功    |
| query_text     | string  | 用户问题        |
| context_text   | string  | 清洗后的大模型上下文  |
| sources        | array   | 资料来源摘要      |
| images         | array   | 图片 / 图表语义证据 |
| tables         | array   | 表格证据        |
| equations      | array   | 公式证据        |
| meta           | object  | 上下文构造层统计信息  |
| retrieval_meta | object  | 检索层统计信息     |
| warnings       | array   | 警告信息        |

---

### 5.5 返回示例

```json
{
  "success": true,
  "query_text": "退移速率曲线说明了什么？",
  "context_text": "你是知识库问答助手。请只基于以下资料回答用户问题，如果资料不足，请说明无法确定。\n\n用户问题：退移速率曲线说明了什么？\n\n[资料 S1]\n文档：石蜡燃料的燃烧性能与其化学组成的关系_唐乐\n章节：3.3 退移速率测试\n页码：3-4\n命中内容：……\n父级上下文：……",
  "sources": [
    {
      "source_id": "S1",
      "document_title": "石蜡燃料的燃烧性能与其化学组成的关系_唐乐",
      "section_title": "3.3 退移速率测试",
      "page_range": "3-4",
      "matched_child_count": 1,
      "image_count": 2,
      "table_count": 0,
      "equation_count": 1,
      "summary_text": "S1 · 石蜡燃料的燃烧性能与其化学组成的关系_唐乐 · 3.3 退移速率测试 · 页码 3-4 · 命中子块 1 · 图表 2 · 表格 0 · 公式 1"
    }
  ],
  "images": [
    {
      "source_id": "S1",
      "caption": "退移速率变化曲线",
      "description": "图表显示不同条件下退移速率的变化趋势。",
      "image_url": null
    }
  ],
  "tables": [],
  "equations": [],
  "warnings": []
}
```

---

## 6. 重要设计说明

### 6.1 MCP 不选择 Embedding 模型

MCP 工具不提供 embedding 模型选择参数。

原因：

```text
embedding 模型与知识库中已经生成的向量强绑定。
如果查询时随意切换 embedding 模型，会导致 query embedding 与 chunk embedding 不在同一语义空间中，召回结果不可信。
```

当前 MCP 默认使用 Know_RAG 后端已保存的全局 Embedding 配置。

如果更换 Embedding 模型，需要重新生成对应知识库的向量。

---

### 6.2 默认不返回图片 URL

当前默认：

```json
{
  "include_images": true,
  "include_image_urls": false
}
```

含义：

```text
返回图片 / 图表的标题、OCR、VLM 描述等语义内容；
不默认返回本地 MinIO 图片 URL；
不默认把原始图片资源暴露给云端 Agent。
```

如果后续明确接入多模态云端大模型，并允许图片出网，可以显式设置：

```json
{
  "include_image_urls": true
}
```

---

### 6.3 MCP 工具只读

当前 MCP 只开放只读工具：

```text
know_rag_list_knowledge_bases
know_rag_search_context
```

不开放：

```text
上传文档
删除文档
删除知识库
重建索引
数据库查询
原始文件下载
```

---

## 7. 本地测试方式

### 7.1 Stdio 模式测试

命令：

```bash
docker exec -i know-rag-backend python -m app.integrations.mcp.server
```

MCP Inspector 配置：

```text
Transport Type: STDIO
Command: docker
Arguments: exec -i know-rag-backend python -m app.integrations.mcp.server
```

---

### 7.2 Streamable HTTP 模式测试

MCP Inspector 配置：

```text
Transport Type: Streamable HTTP
URL: http://127.0.0.1:8000/mcp
```
如果使用独立 MCP 服务：
```text
URL: http://127.0.0.1:8010/mcp
```
连接成功后应能看到：
```text
know_rag_list_knowledge_bases
know_rag_search_context
```
---

## 8. 云端 Agent 接入说明

云端 Agent 应通过 MCP 远程地址连接 Know_RAG：

```text
https://your-domain.com/mcp
```

调用建议：

```text
当用户问题涉及本地知识库内容时，先调用 know_rag_search_context；
将用户问题作为 query_text；
选择对应 knowledge_base_id；
使用返回的 context_text 和 sources 生成最终回答。
```

推荐 Agent 系统提示词：

```text
当用户问题可能涉及本地知识库内容时，必须先调用 know_rag_search_context。
回答时只能基于工具返回的 context_text 和 sources。
如果资料不足，请明确说明“根据当前知识库资料无法确定”。
不要编造 context_text 之外的信息。
```
