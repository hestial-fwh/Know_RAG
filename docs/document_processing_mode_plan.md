# 文档处理模式：标准 / 画册书法 双路径方案

> 状态：Current（标准模式 + 画册模式双路径已实现；enricher 已隔离；默认标准模式）
> 维护日期：2026-08-10
> 范围：`_page_kind` 页面分类 → 切分策略分配链路 + parse-time enricher 隔离
> 关联文档：[[document_profile_parse_chunk_plan.md]]

## 1. 背景与动机

### 1.1 现有问题

`_page_kind`（[document_profiler.py](../backend/app/services/document_parser/document_profiler.py#L141-L211)）的 `visual_dominant` 分类依赖 pdfplumber 的 `dominant_image_ratio` 信号：

```python
visual_dominant = bool(primary_visual_count or dominant_image_ratio >= 0.65)
```

`dominant_image_ratio >= 0.65` 这个门控对专利/技术文档产生大量误报：
- 英文专利：大图 + 成段英文文字 → 被 pdfplumber 判为嵌入式图片占 65%+ → `visual_dominant`
- 中文专利（born-digital PDF）：嵌入式字体导致 pdfplumber 抽不出文字层，`dominant_image_ratio = 1.00` → `visual_dominant` 或 `scanned_prose`

为了修复这些误报，我们先后加入了 3 个逃逸分支（2026-08-06 ~ 2026-08-07），与 pdfplumber 信号源对抗。这些分支逻辑复杂，且本质上是在修正一个不可靠的外部信号。

### 1.2 核心洞察

**MinerU 自身的信号是可靠的**。`primary_visual_count`（MinerU 显式标记的页面主视觉）比 pdfplumber 的嵌入式图片面积检测更准确。`canonical_text_chars`（MinerU 解析出的文字量）直接反映页面实际内容。

问题根源：我们让 pdfplumber 的不可靠信号参与了分类决策。

### 1.3 方案思路

**让用户在导入时显式声明文档类型**。默认"标准文档"模式下，页面分类只信任 MinerU 的信号，不使用 pdfplumber 的 `dominant_image_ratio`。只有用户主动选择"画册/书法"模式时，才启用 pdfplumber 后处理和完整的启发式逻辑。

## 2. 设计

### 2.1 用户视角

上传文档时，新增一个"文档类型"选择器：

| 选项 | 值 | 适用场景 |
|------|-----|---------|
| 📄 标准文档（默认） | `standard` | 专利、论文、技术报告、合同、书籍等绝大多数文档 |
| 🎨 画册/书法 | `visual_art` | 书法字帖、画册、艺术作品集——"图片即正文"的特殊文档 |

标准文档默认为所有用户选择，无需额外操作。只有处理画册类文档时才需要手动切换。

### 2.2 数据流

```
上传对话框
  └─ processing_mode: "standard" | "visual_art"
      → POST /api/v1/documents/upload (+ processing_mode Form field)
      → Document.metadata_json["parse_options"]["processing_mode"]
      │
      ├── 解析阶段 (parser_service._maybe_preserve_pdf_page_visuals)
      │   ├─ standard  → 跳过 pdfplumber enricher（不分析、不渲染、不打标签）
      │   └─ visual_art → enrich_pdf_page_visuals()（pdfplumber + 渲染 + 打标签）
      │
      └── 切分阶段 (chunk_service)
          → profile_canonical_document(canonical, processing_mode=...)
          → _page_kind(..., processing_mode=...)
```

存储在 `metadata_json.parse_options.processing_mode` 中，与 `parser_engine`、`enable_ocr` 等现有解析选项并列。不需要 DB 迁移。

**关键隔离点**：`parser_service.py` 的 `_maybe_preserve_pdf_page_visuals()` 在 standard 模式下直接返回原始解析结果，不调用任何 pdfplumber 相关代码。这是真正的源头隔离——不是在下游 `_page_kind()` 里对抗已被污染的信号，而是从解析阶段就不让 pdfplumber 参与。

### 2.3 `_page_kind` 双路径

#### 标准模式（`processing_mode = "standard"`）

**解析阶段**：`parser_service._maybe_preserve_pdf_page_visuals()` 检测到 `processing_mode == "standard"` 后直接返回，**不调用 pdfplumber**。零 pdfplumber 开销，零 `page_primary_visual` 标签污染。

**分类阶段**：`_page_kind()` 中：

```python
# visual_dominant 仅由 primary_visual_count 触发
# 不检查 dominant_image_ratio
# 不检查 native_text_chars
# → 不存在 scanned_prose 路径
#
# 对于新解析文档，primary_visual_count 始终为 0（enricher 已跳过）
# canonical_text_chars >= 300 守卫保护存量文档（旧解析仍有 enricher 标签）
```

| 页面特征 | 分类结果 |
|----------|----------|
| MinerU 有 text 节点，无 primary_visual | `native_text` |
| MinerU 有 text + image 混合节点 | `native_text` |
| MinerU 标记 `page_primary_visual` + 文字 < 300 | `visual_dominant`（存量文档） |
| MinerU 标记 `page_primary_visual` + 文字 >= 300 | `native_text`（守卫推翻） |
| 纯表格页 | `table_dense` |
| 有 heading + 短文字 + 图 | `slide` |
| 真扫描件（无文本层） | `native_text`（见 2.4） |

#### 画册模式（`processing_mode = "visual_art"`）

保持当前完整的启发式逻辑（`dominant_image_ratio >= 0.65` + 3 个逃逸分支），不做任何删减。

### 2.4 取舍：扫描件检测丢失

标准模式下无法区分"真扫描件"和"born-digital PDF"，因为不再检查 `native_text_chars`。后果：

- 扫描件页面被归为 `native_text` → `structured-text-v1` 策略
- **失去页面边界锁定**（`preserve_page_boundary = True`），chunk 可能跨页
- **不再隔离 OCR 产物**（`image_ocr_mode = NORMAL` 而非 `SHADOW_AUXILIARY`）

**评估**：可接受。理由：
1. 主流文档（专利、论文、报告）几乎都是 born-digital PDF
2. 扫描件上传到 RAG 系统是小众场景
3. 页面边界锁定对检索质量的影响远小于 OCR 质量本身
4. 如果用户明确需要扫描件支持，可以后续增加第三档 `"scanned"` 模式

### 2.5 分类行为矩阵

| 场景 | `primary_visual_count` | `dominant_image_ratio` | `canonical_text_chars` | 标准模式 | 画册模式 |
|------|:---:|:---:|:---:|------|------|
| 英文专利（大图+文字） | 0 | 0.75 | 1200 | `native_text` ✅ | `native_text` ✅ |
| 中文专利 born-digital | 0 | 0.90 | 1200 | `native_text` ✅ | `native_text` ✅ |
| 中文专利碎片文字 | 0 | 0.80 | 350 | `native_text` ✅ | `native_text` ✅ |
| 技术报告 | 0 | 0.0 | 3000 | `native_text` ✅ | `native_text` ✅ |
| 书法（碎片文字 <60 chars） | 0 | 0.85 | 30 | `unknown` → 降级 | `visual_dominant` ✅ |
| 书法（MinerU 标记 primary_visual） | 1 | 0.85 | 30 | `visual_dominant` ✅ | `visual_dominant` ✅ |
| 纯图页（无文字） | 0 | 0.90 | 0 | `unknown` → 降级 | `visual_dominant` ✅ |
| 真扫描件 | 0 | 0.85 | 800 | `native_text` ⚠️ | `scanned_prose` ✅ |
| 表格密集型 | N/A | N/A | N/A | `table_dense` ✅ | `table_dense` ✅ |
| 幻灯片 | 0 | 0.30 | 80 | `slide` ✅ | `slide` ✅ |

### 2.6 后续扩展

**第三档 `"scanned"` 模式**：如果标准模式下扫描件处理成为实际问题，可以增加第三档显式声明。届时标准模式的 `_page_kind` 加上 `native_text_chars == 0 and canonical_text_chars > 0` → `scanned_prose` 的检测，但仅在 `processing_mode = "scanned"` 时激活。

## 3. 变更清单

### 3.1 后端

| 文件 | 改动 | 说明 |
|------|------|------|
| `backend/app/api/routes_documents.py` | 新增 `processing_mode: str = Form(default="standard")` | 上传端点参数 |
| `backend/app/services/document_service.py` | `upload_document()` 新增参数，存入 `metadata_json.parse_options.processing_mode` | 持久化 |
| `backend/app/services/parser_service.py` | `_maybe_preserve_pdf_page_visuals()` 检查 `processing_mode`，standard 模式直接返回 | **源头隔离**：跳过 pdfplumber enricher |
| `backend/app/services/chunk_service.py` | 从 document metadata 读取 `processing_mode`，传入 profiler | 数据传递 |
| `backend/app/services/document_parser/document_profiler.py` | `_page_kind()` 和 `profile_canonical_document()` 新增 `processing_mode` 参数；`canonical_text_chars >= 300` 守卫 | 核心逻辑分支 + 存量保护 |
| `backend/tests/test_document_profiler.py` | 新增标准模式测试用例，画册模式用例保持 | 测试覆盖 |

### 3.2 前端

| 文件 | 改动 | 说明 |
|------|------|------|
| 上传对话框组件 | 新增文档类型选择器 | 用户交互 |
| `frontend/src/api/documents.ts` | `uploadDocument()` 新增 `processing_mode` 字段 | API 对接 |

### 3.3 文档

| 文件 | 改动 |
|------|------|
| `docs/document_processing_mode_plan.md` | 本文档（新增） |
| `docs/index.md` | 新增索引条目 |
| `CLAUDE.md` | 补充 processing_mode 说明 |

## 4. 测试设计

见 [test_document_profiler.py](../backend/tests/test_document_profiler.py)，需覆盖：

### 标准模式（8 个用例）

| 测试 | 场景 | 预期分类 |
|------|------|----------|
| `test_standard_patent_with_large_figure` | 大图 + 连续散文 + 文本层 + 无 primary_visual | `native_text` |
| `test_standard_chinese_patent_born_digital` | 大图 + 连续散文 + 无文本层 + 无 primary_visual | `native_text` |
| `test_standard_chinese_patent_fragmented` | 大图 + 碎片文字 + 无 primary_visual | `native_text` |
| `test_standard_primary_visual` | MinerU 显式标记 primary_visual | `visual_dominant` |
| `test_standard_normal_text` | 普通文字页，无图 | `native_text` |
| `test_standard_table_dense` | 表格密集型 | `table_dense` |
| `test_standard_slide` | 幻灯片 | `slide` |
| `test_standard_pure_image_no_text` | 纯图无文字，无 primary_visual | `unknown`（降级） |

### 画册模式（保持现有 12 个用例不变）

现有测试全部以 `processing_mode="visual_art"` 参数化，行为完全不变。

## 5. 切回与兼容

### 5.1 存量文档

已解析的文档不自动更新。如需重新分类，用户需执行 reparse。存量文档无 `processing_mode` 字段时，默认按 `"visual_art"` 处理（保守兼容，保持现有行为）。

### 5.2 环境变量

无新增环境变量。这是文档级别的选项，不需要全局开关。

### 5.3 回滚

如果标准模式出现问题：
1. 前端隐藏文档类型选择器（始终传 `"visual_art"`）
2. 或者后端忽略前端传入的值，强制使用 `"visual_art"`
3. 两种方式都不需要数据迁移

## 6. 实施记录

### 6.1 变更文件

| 文件 | 变更 |
|------|------|
| `backend/app/services/document_parser/document_profiler.py` | `_page_kind()` 和 `profile_canonical_document()` 新增 `processing_mode` 参数；标准模式只检查 `primary_visual_count` + `canonical_text_chars >= 300` 守卫 |
| `backend/app/services/parser_service.py` | `_maybe_preserve_pdf_page_visuals()` 检查 `processing_mode`，standard 模式跳过 pdfplumber enricher（2026-08-10） |
| `backend/app/services/chunk_service.py` | 从 `document.metadata_json.parse_options.processing_mode` 读取模式，传入 profiler |
| `backend/app/services/document_service.py` | `upload_document()` 接受并持久化 `processing_mode` |
| `backend/app/api/routes_documents.py` | 上传端点新增 `processing_mode` Form 参数（默认 `"standard"`） |
| `frontend/src/api/documents.ts` | `uploadDocument()` payload 和 FormData 新增 `processing_mode` |
| `frontend/src/views/DocumentUploadView.vue` | 新增"文档类型"下拉选择器 |
| `backend/tests/test_document_profiler.py` | 新增 `TestStandardModeClassification`（11 个测试） |
| `docs/document_processing_mode_plan.md` | 本文档（状态：Current） |

### 6.2 测试结果

- **新增测试**：11 个标准模式测试全部通过
- **现有测试**：12 个画册模式测试无回归
- **全量测试**（2026-08-10）：876 passed / 3 既存失败（与本次无关）

### 6.3 enricher 隔离（2026-08-10）

**问题**：最初实现只在 `_page_kind()` 层面区分 standard/visual_art，但上游
`page_visual_enricher.py` 对所有 PDF 无差别执行——pdfplumber 分析 + 打
`page_primary_visual` 标签。标准模式下 pdfplumber 的误判通过 enricher 标签
间接泄漏进 profiler。

**修复**：[parser_service.py](../backend/app/services/parser_service.py)
`_maybe_preserve_pdf_page_visuals()` 检查 `processing_mode`：
- `standard` → 直接返回原始解析结果，零 pdfplumber 调用
- `visual_art` → 完整 enricher 流程（profiling + rendering + tagging）

**效果**：
- 新解析的 standard 文档 `primary_visual_count` 始终为 0
- 不再需要靠 `canonical_text_chars >= 300` 守卫对抗 enricher 污染（守卫保留保护存量）
- standard 模式解析速度提升（跳过 pdfplumber 分析和页面渲染）
- 存量文档需 reparse 才能获得完全干净的 profile
