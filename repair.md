# MinerU 文档解析引擎 — 格式对齐修复

## 背景

MinerU 解析器 (`mineru_adapter.py`) 输出的 `ParsedBlock` 与下游管线 (`chunk_service.py`、`vision_service.py`、`block_reflow.py`) 之间存在多处字段名和格式不匹配。

## 修改文件清单

### 第一阶段：格式对齐（已合入）

---

### 1. `backend/app/services/document_parser/mineru_adapter.py`

#### 1.1 新增 `_markdown_table_to_html()` 函数（第 430–482 行）

**问题**：MinerU `pipeline` 后端返回的表格是 Markdown pipe table 格式 (`| A | B |\n|---|---|\n| 1 | 2 |`)，但下游 `chunk_service._block_index_content()` 调用 `_plain_text_from_html()` 从 `<table>` HTML 提取表格文本。原代码 `table_html = table_body if "<table" in table_body.lower() else ""` 导致 Markdown 表格的 `table_html` 始终为空。

**修复**：新增 `_markdown_table_to_html()` 函数，识别 Markdown pipe table（含 `|---|---|` 分隔行），转换为 `<table><thead><tr><th>...</th></tr></thead><tbody><tr><td>...</td></tr></tbody></table>` 结构。

#### 1.2 表格 block caption 写入标准键名（第 516–518 行）

**问题**：原代码只在 `context_header` 中保存表格标题，`metadata` 中没有 `caption` 键。下游 `_block_index_content` 通过 `_first_text_deep(meta, ["caption", "table_caption", "title"])` 查找标题，找不到 MinerU 的表格标题。

**修复**：在 `metadata` 中同时写入 `caption` 和 `table_caption`。

#### 1.3 图片 block caption 写入标准键名（第 541–545 行）

**问题**：MinerU 将图片标题存为 `image_caption`，但下游 `_block_index_content` 查找 `["caption", "figure_caption", "title"]`，导致图片 chunk 的检索内容缺失标题。

**修复**：同时在 `metadata` 中写入三个键名：`caption`、`figure_caption`、`image_caption`。

#### 1.4 解析器名称统一为 `"mineru"`（第 507 行）

**问题**：`_content_list_to_blocks` 写入 `parser="mineru_pdf"`，而 `_finalize` 写入 `parser="mineru"`。两个函数对同一文档返回不同的 parser 名称。

**修复**：统一为 `"mineru"`。

---

### 2. `backend/app/services/vision_service.py`

#### 2.1 `enhance_ocr_blocks()` 第 302 行 — OCR source 白名单

**问题**：`enhance_ocr_blocks` 有一个 source 白名单决定哪些 block 可以触发 OCR 增强：

```python
source in {"pdf_page_render", "pdfplumber_figure_crop", "pdfplumber_table",
           "pdfplumber_table_crop", "uploaded_image", "pptx_media", "epub_media"}
```

MinerU block 的 `source` 为 `"mineru_content_list"`，不在白名单中。当 MinerU 图片 block 没有自带 OCR 文本时，**不会触发外部 OCR 增强**。

**修复**：添加 `"mineru_content_list"` 到白名单。

---

### 3. `backend/app/services/chunk_service.py`

#### 3.1 `_block_index_content()` 表格标题查找（第 405 行）

```diff
- ("表格标题", _first_text_deep(meta, ["caption", "table_caption", "title"])),
+ ("表格标题", _first_text_deep(meta, ["caption", "table_caption", "title", "image_caption"])),
```

#### 3.2 `_block_index_content()` 图片标题查找（第 418 行）

```diff
- ("图片标题", _first_text_deep(meta, ["caption", "figure_caption", "title"])),
+ ("图片标题", _first_text_deep(meta, ["caption", "figure_caption", "title", "image_caption"])),
```

`_first_text_deep` 按传入的 key 顺序在 metadata 及其嵌套 dict 中查找第一个非空字符串值。追加 `"image_caption"` 到列表末尾，同时兼容 RAGFlow adapter（使用 `caption`/`figure_caption`）和 MinerU adapter（使用 `image_caption`）。

---

## 第二阶段：MinerU v3.2.3 响应结构兼容（本次修复）

### 根因诊断

通过直接调用 `POST /file_parse` 并打印响应结构，发现 MinerU v3.2.3 的真实返回格式：

```json
{
  "task_id": "...",
  "status": "completed",
  "backend": "pipeline",
  "results": {
    "filename.pdf": {
      "md_content": "# Title\n\n...",
      "content_list": "[{\"type\":\"text\",\"text\":\"...\",\"page_idx\":0}]"
    }
  }
}
```

**两个关键问题**：

| 问题 | 旧代码预期 | 实际 MinerU v3.2.3 |
|------|-----------|-------------------|
| 位置 | 顶层 `content_list` / `markdown` 或 `data.*` | `results.{filename}.content_list` / `md_content` |
| 类型 | `content_list` 是 `list[dict]` | `content_list` 是 **JSON-encoded string** |

旧代码的 `_find_content_list()` 只查找 list 类型的值，`_find_markdown()` 只递归 `data` 键，两者都找不到实际返回的内容，导致 fallback。

### 修改

### 4. `backend/app/services/document_parser/mineru_adapter.py`（第二阶段）

#### 4.1 新增 `_find_file_result()` 函数

统一导航函数，处理 MinerU v2/v3 多种响应包裹格式：

- `results.{filename}.{md_content, content_list}` — MinerU v3.2.x 主格式
- `result.{filename}.{...}` — 旧版本格式
- `data.results.{filename}.{...}` — 嵌套包裹
- 顶层 `{content_list, markdown}` — 向后兼容

#### 4.2 重写 `_find_content_list()` — 支持 JSON 字符串解析

```python
# 旧代码: 只查找 list 类型的值
if isinstance(value, list): ...

# 新代码: 先尝试将 JSON 字符串解析为 list
if isinstance(value, str) and value.strip().startswith("["):
    value = json.loads(value)
if isinstance(value, list): ...
```

#### 4.3 重写 `_find_markdown()` — 通过 `_find_file_result()` 查找

不再仅搜索顶层和 `data` 键，改为先通过 `_find_file_result()` 定位文件结果，再在其中查找 `md_content` / `markdown` 等键。

#### 4.4 修复 `_page_from_block()` — Python falsy 值 bug

```python
# 旧代码: 0 是 falsy → 0 or None → None
page_idx = _to_int(block.get("page_idx") or block.get("page_index"))

# 新代码: 显式 None 检查
pi_raw = block.get("page_idx")
if pi_raw is None:
    pi_raw = block.get("page_index")
page_idx = _to_int(pi_raw)
```

导致 `page_idx=0`（第一页）被误判为无页码，block 丢失 `page_no`。

#### 4.5 新增诊断日志

`parse_document_with_mineru()` 现在输出响应结构摘要：

```
MinerU response top_keys=['task_id', 'status', 'results', ...]
MinerU file_result keys=['md_content', 'content_list']
  md_content: str(len=10345)
  content_list: str(len=16122)
```

错误信息也从简单的 "did not contain usable content" 升级为包含响应结构摘要。

---

## 对齐前后对比

### 表格 block

| 字段 | 对齐前 | 对齐后 |
|------|--------|--------|
| MinerU 输出 | `table_body="\|A\|B\|\n\|--\|--\|\n\|1\|2\|"` | 同左 |
| `table_html` | `""` | `<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>` |
| `metadata.caption` | 不存在 | `"表格标题"` (如有) |
| 下游表格 chunk 内容 | 空或 Markdown 原文 | `"A\tB\n1\t2"` (HTML→文本) |

### 图片 block

| 字段 | 对齐前 | 对齐后 |
|------|--------|--------|
| MinerU 输出 | `image_caption="图1: 架构图"` | 同左 |
| `metadata.caption` | 不存在 | `"图1: 架构图"` |
| `metadata.figure_caption` | 不存在 | `"图1: 架构图"` |
| 下游 `图片标题` | 空 | `"图1: 架构图"` |

### OCR 增强

| 场景 | 对齐前 | 对齐后 |
|------|--------|--------|
| MinerU 自带 OCR 文本 | block 有 `ocr_raw_text` → 跳过外部 OCR ✓ | 同左 ✓ |
| MinerU 无 OCR 文本 | source 不在白名单 → **不触发 OCR** ✗ | source 在白名单 → **触发 OCR** ✓ |

### 响应解析（第二阶段核心修复）

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| `results.{fn}.content_list` (JSON string) | 找不到 → fallback | `json.loads()` 解析 → 成功 ✓ |
| `results.{fn}.md_content` | 找不到 → fallback | `_find_file_result()` 定位 → 成功 ✓ |
| `page_idx=0` | `0 or None → None` 丢失 | 显式 None 检查 → `page_no=1` ✓ |
| 错误信息 | `"did not contain usable content_list or markdown"` | 包含 `top_keys` + `file_result_keys` 摘要 |

---

## 验证

```bash
# 全部测试: 73 passed, 2 failed (2 个预存失败，与本次无关)
docker compose exec backend python -m pytest tests/ -v

# 前端构建通过
docker compose exec frontend npm run build
# ✓ built in 1.35s
```

## 未修改文件（不需要改）

- `backend/app/services/document_parser/types.py` — `ParsedBlock` 结构不依赖具体字段名
- `backend/app/services/document_parser/block_reflow.py` — 通过 `ocr_status`/`source` 识别 MinerU block
- `backend/app/plugins/parse_eval/` — 通过 `document.metadata_json` 读取，不直接依赖 block 字段名
- `backend/app/db/models.py` — 无 schema 变更



