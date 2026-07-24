# 文档类型判断全链路

> 状态：Current
> 维护日期：2026-07-24
> 适用范围：`build_canonical_parent_child_plan` 及上下游分类逻辑

## 概述

文档解析链路通过**两级分类系统**判断文档类型：先在 PDF 原始文件层面分析页面视觉特征，再在 CanonicalDocument 层面进行页面级语义分类，最终决策使用哪种父块切分策略。

---

## 完整链路

```
PDF 原始文件
  │
  ▼
① PDF 页面视觉分析 (page_visual_profile.py)
  │  用 pdfplumber 直接检查 PDF，计算每页嵌入图片占页面面积比
  │
  ├─ 嵌入图 ≥70% 且解析器已有对应图片块 → already_present
  ├─ 嵌入图 ≥70% 但解析器没有对应图片块 → preserve（需要渲染补图）
  ├─ 嵌入图 ~55%-70%                       → ambiguous
  └─ 无主图                                 → normal
  │
  ▼
② 注入 page_primary_visual 标签 (page_visual_enricher.py)
  │  "already_present" 页 → 给解析器产出的最大图片块打上 page_primary_visual
  │  "preserve" 页       → 渲染 PDF 页面为新图块，打上 page_primary_visual
  │
  ▼
③ 构建 CanonicalDocument (canonical_document.py)
  │  ParsedDocument → CanonicalMarkdown + 资产侧车
  │
  ▼
④ 页面级分类 (document_profiler.py)
  │  每页按优先级分类：
  │
  ├─ table_dense      — 表格面积≥35% 或表格文字比≥60%
  ├─ visual_dominant  — 有 page_primary_visual 或嵌入图≥65%
  │   └─ scanned_prose — 有连续正文但无原生可选文字（扫描书）
  ├─ slide            — 有标题+图，文字≤100字符（幻灯片）
  ├─ native_text      — 有原生文字
  └─ unknown
  │
  │  文档级聚合：取最多的页面类型作为 primary_kind
  │
  ▼
⑤ 策略映射 (chunk_policy_resolver.py)

  page_kind           → policy_id              → boundary_strategy
  ─────────────────────────────────────────────────────────────────
  table_dense         → table-row-group-v1      → 表格行切分
  scanned_prose       → scanned-prose-v1        → 子句/段落切分
  visual_dominant
    + visual_core_text → poster-page-v1         → 页面文字区域切分
    + 其他             → visual-collection-v1   → 页面描述切分
  slide               → slide-page-v1           → 幻灯片切分
  native_text         → code-technical-v1       → 标题/代码块切分
  (default)           → structured-text-v1      → 标题/段落切分
  │
  ▼
⑥ 分叉点：三条路径 (canonical_parent_child.py)

  ┌─ 路径A: DCP-3 活跃策略 (has_active_dcp3_policy)
  │   按策略独立切分每一页
  │
  ├─ 路径B: visual_page_ranges 非空 (≥3 视觉页 AND ≥60% 占比)
  │   ├─ flag=true  → _build_visual_section_parents()  ← 段落级+跨页绑定
  │   └─ flag=false → _build_visual_page_parents()     ← 页面级父块
  │
  └─ 路径C: 普通文本路径
      标准 split_parent_child_result()
```

---

## 各阶段详解

### 阶段①：PDF 页面视觉分析

**文件**: [page_visual_profile.py](../backend/app/services/document_parser/page_visual_profile.py)
**函数**: `profile_pdf_pages()`

使用 `pdfplumber` 直接读取 PDF 原始文件，检查每页中嵌入图片的尺寸和位置。核心判断依据：

| 阈值 | 默认值 | 作用 |
|------|--------|------|
| `dominant_image_threshold` | 0.70 | 嵌入图覆盖面积≥70% 判定为"主图" |
| `existing_image_threshold` | 0.65 | 解析器产出图片块覆盖≥65% 视为"已有" |

四种决策结果：

| 决策 | 条件 | 含义 |
|------|------|------|
| `already_present` | 主图≥70% **且** 解析器已有覆盖≥65%的图片块 | 解析器已产出，不需要补充 |
| `preserve` | 主图≥70% **但** 解析器没有对应图片块 | 需渲染 PDF 页面为图片补充 |
| `ambiguous` | 嵌入图在 55%-70% 之间 | 不明确，不做处理 |
| `normal` | 无主图 | 普通页面，不需要处理 |

结果存储在 `parsed_document.metadata["page_visual_profile"]["decisions"]`。

### 阶段②：注入 page_primary_visual 标签

**文件**: [page_visual_enricher.py](../backend/app/services/document_parser/page_visual_enricher.py)

控制开关：`DOCUMENT_PAGE_VISUAL_PRESERVATION_ENABLED`（默认 `false`）

- **`already_present` 页**：`_mark_existing_page_visuals()` 给解析器产出的最大图片块打上 `semantic_role: "page_primary_visual"` 和 `visual_role: "page_primary_visual"`，同时设置 `exclude_from_asset_group: true`
- **`preserve` 页**：`enrich_pdf_page_visuals()` 渲染 PDF 页面为图片，作为新 `ParsedBlock` 注入，同样打上 `page_primary_visual` 标签

### 阶段③：构建 CanonicalDocument

**文件**: [canonical_document.py](../backend/app/services/document_parser/canonical_document.py)

将 `ParsedDocument` 转换为 `CanonicalDocument`：文本变为 CanonicalMarkdown，图片/表格变为 `asset://` 引用，元数据（包括 `visual_role`、`semantic_role`）保留在 `CanonicalAsset` 上。

### 阶段④：页面级分类

**文件**: [document_profiler.py](../backend/app/services/document_parser/document_profiler.py)
**函数**: `profile_canonical_document()` → `_page_kind()`

按优先级对每页分类：

```
1. table_dense     ← 表格面积≥35% 或 表格文字比≥60%
2. visual_dominant ← page_primary_visual_count > 0 或 嵌入图比≥65%
   └─ scanned_prose ← visual_dominant + 连续正文 + 原生文字字符=0
3. slide           ← 有图 + 有标题 + 正文≤100字符
4. native_text     ← 有原生文字（canonical_text_chars > 0）
5. unknown         ← 兜底
```

`visual_dominant` 的 OCR 子分类：

| 子类型 | 条件 |
|--------|------|
| `visual_core_text` | 原生文字=0，canonical文字 30-400 字符，解析器文字面积≤35% |
| `visual_candidate` | 原生文字=0，不在 core_text 范围 |
| `continuous_prose` | scanned_prose 类型 |

**文档级聚合**：取出现最多的 `page_kind` 作为 `primary_kind`。若各类别占比接近则标记为 `"mixed"`。

分类映射到类别：

| page_kind | profile_category |
|-----------|-----------------|
| `native_text` + 有代码块 | `code_or_technical` |
| `native_text` + 无代码块 | `structured_text` |
| `scanned_prose` | `scanned_prose` |
| `visual_dominant` | `visual_collection` |
| `table_dense` | `table_dense` |
| `slide` | `presentation` |

### 阶段⑤：策略映射

**文件**: [chunk_policy_resolver.py](../backend/app/services/chunker/chunk_policy_resolver.py)
**函数**: `resolve_chunk_policies()` → `_policy_id()`

将每页的 `page_kind` 映射为具体切分策略：

| page_kind | policy_id | boundary_strategy | 说明 |
|-----------|-----------|-------------------|------|
| `table_dense` | `table-row-group-v1` | `table_header_row_group` | 按表头行分组切分 |
| `scanned_prose` | `scanned-prose-v1` | `clause_paragraph_page` | 按子句/段落切分 |
| `visual_dominant` + `visual_core_text` | `poster-page-v1` | `page_text_region` | 海报文字区域切分 |
| `visual_dominant` (其他) | `visual-collection-v1` | `page_description` | 画册/视觉集 |
| `slide` | `slide-page-v1` | `slide_title_bullet_group` | 幻灯片标题+要点 |
| `native_text` + 代码 | `code-technical-v1` | `heading_code_fence_line` | 标题+代码块 |
| 默认 | `structured-text-v1` | `heading_paragraph_sentence` | 标题+段落+句子 |

**活跃策略 vs 影子策略**：`DCP3_ACTIVE_POLICY_IDS = {"poster-page-v1", "scanned-prose-v1", "table-row-group-v1", "visual-collection-v1"}` 这四种策略是"活跃"的（`shadow_only=False`），会触发路径 A 的按策略分页切分。其他策略仅作为元数据标签（`shadow_only=True`）。

### 阶段⑥：三条切分路径

**文件**: [canonical_parent_child.py](../backend/app/services/chunker/canonical_parent_child.py)
**函数**: `build_canonical_parent_child_plan()`

#### 关键判断：visual_page_ranges

`_visual_page_ranges()` 函数的触发条件：

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `minimum_visual_pages` | 3 | 至少 3 页有 `page_primary_visual` 资产 |
| `minimum_visual_ratio` | 0.60 | 视觉页占总页数的 60% 以上 |

**两个条件同时满足**才返回非空 `visual_page_ranges`。

#### 路径 A：DCP-3 活跃策略

触发条件：`has_active_dcp3_policy = True`（即至少有一页匹配 DCP3_ACTIVE_POLICY_IDS 中的策略）且 `page_ranges` 非空。

行为：每一页用其对应的策略独立切分，不同页可以使用不同的 `boundary_strategy`。

#### 路径 B：视觉页面路径

触发条件：`visual_page_ranges` 非空（≥3 视觉页且 ≥60% 占比）且没有活跃 DCP-3 策略。

按页边界切分后，父块物化分支：

| 开关 | 函数 | 行为 |
|------|------|------|
| `DOCUMENT_VISUAL_SECTION_PARENT_ENABLED=true` | `_build_visual_section_parents()` | 段落级父块 + 跨页图片绑定 |
| `DOCUMENT_VISUAL_SECTION_PARENT_ENABLED=false` | `_build_visual_page_parents()` | 每页一个父块（旧行为） |

#### 路径 C：普通文本路径

触发条件：既不满足路径 A 也不满足路径 B。

使用标准 `split_parent_child_result()` 在整个文档上切分，父块边界来自切分器自身。

---

## 后处理（所有路径共享）

无论走哪条路径，最终都经过相同的后处理：

1. `deduplicate_repeated_asset_children` — 去重重叠区的重复图片 children
2. `deduplicate_overlap_shadow_children` — 去重完全被相邻 child 包含的 text children
3. `merge_overlap_tail_text_children` — 合并短重叠尾部
4. `_apply_chunk_policy_metadata` — 在 parents/children 上标记策略元数据

---

## 典型文档示例

### 书法画册 PDF（文字页/图片页交替）

```
Page A: 王羲之介绍 (text) + 作品图    → native_text
Page B: 只有作品图                     → visual_dominant (preserve)
Page C: 赵孟頫介绍 (text) + 作品图    → native_text
Page D: 只有作品图                     → visual_dominant (preserve)
...
```

> **注意**：分类和绑定完全基于**内容**（有无语义文本、有无主图），与页号无关。
> 这里 A/B/C/D 只是示意交替模式，实际页号可以是任意编号。

- 50% 视觉页 → **不满足 60% 阈值** → `visual_page_ranges` 为空
- 有 `visual-collection-v1` 活跃策略 → 走 **路径 A**（DCP-3 按策略切分）
- 纯图页（B/D）的 `page_primary_visual` 图块通过策略保留；跨页绑定时会向上绑定到最近的前一个有文字页（B→A, D→C）

### 纯画册 PDF（每页都是大图）

```
Page 1-20: 全是整页作品图
```

- 100% 视觉页 → 满足阈值 → `visual_page_ranges` 非空
- 全部 `visual-collection-v1` → 有活跃策略 → 走 **路径 A**
- 如果 DCP-3 未启用 → 走 **路径 B**（visual section/page parents）

### 普通技术文档 PDF

```
Page 1-50: 全是文本+少量小图
```

- 0% 视觉页 → `visual_page_ranges` 为空
- 全部 `structured-text-v1` → 无活跃策略 → 走 **路径 C**（标准文本切分）

---

## 关键开关汇总

| 环境变量 | 默认值 | 作用 |
|----------|--------|------|
| `DOCUMENT_PAGE_VISUAL_PRESERVATION_ENABLED` | `false` | 控制阶段②：是否分析 PDF 并注入 page_primary_visual |
| `DOCUMENT_ADAPTIVE_CHUNK_POLICY_ENABLED` | `false` | 控制阶段⑤：是否启用 DCP 策略映射 |
| `DOCUMENT_VISUAL_SECTION_PARENT_ENABLED` | `false` | 控制阶段⑥路径B：段落级父块 vs 页面级父块 |
| `DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED` | `false` | 控制视觉页 OCR 文本是否隔离（不参与 embedding） |

## 相关文件索引

| 文件 | 职责 |
|------|------|
| [page_visual_profile.py](../backend/app/services/document_parser/page_visual_profile.py) | 阶段①：PDF 页面视觉分析 |
| [page_visual_enricher.py](../backend/app/services/document_parser/page_visual_enricher.py) | 阶段②：注入 page_primary_visual |
| [canonical_document.py](../backend/app/services/document_parser/canonical_document.py) | 阶段③：ParsedDocument → CanonicalDocument |
| [document_profiler.py](../backend/app/services/document_parser/document_profiler.py) | 阶段④：页面级分类与聚合 |
| [chunk_policy_resolver.py](../backend/app/services/chunker/chunk_policy_resolver.py) | 阶段⑤：策略映射 |
| [canonical_parent_child.py](../backend/app/services/chunker/canonical_parent_child.py) | 阶段⑥：三条切分路径与后处理 |
| [document_profile_parse_chunk_plan.md](../document_profile_parse_chunk_plan.md) | DCP 整体方案文档 |
| [visual_section_cross_page_binding_fix.md](../visual_section_cross_page_binding_fix.md) | 跨页绑定修复方案 |
