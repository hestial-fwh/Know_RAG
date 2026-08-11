# Daily Report 2026-08-11

## Phase 1: 孤立子块 parent_chunk_id=NULL 排查与 Standalone Root 语义修复

### 背景

用户发现文档 `ec08e751-b787-490b-ae4c-2a3fd0a0c09f` (SODIUM ION BATTERY, BATTERY AND ELECTRIC DEVICE) 解析后存在三个连续孤立子块（chunk_index 276, 277, 278），`parent_chunk_id=NULL` 但 `chunk_role=child`。

### 根因确认

三个 chunk 并非数据库写丢 parent，而是 Canonical Parent-Child pipeline 中 **Standalone Orphan Resolution** 阶段（`canonical_parent_child.py:3749-3766`）**主动标记**为 `standalone_root_child = true`，之后因为 `parent_index = None` 持久化为 `parent_chunk_id = NULL`。

具体原因：
- Chunk 276: page 25, content="20" — 页码噪声，skip_embedding_recommended
- Chunk 277: page 26, 国际检索报告专利族表格 — 整页唯一内容
- Chunk 278: page 27, "## EP 4 517 993 A1 ## REFERENCES..." — 最后一页唯一内容

每个 chunk 在各自的 DCP3 segment 中都是唯一的子块，所有 parent assignment 阶段均未找到合适的 parent owner。

### 修复内容

#### 1. Standalone Root 分类（`canonical_parent_child.py`）

新增 `_classify_unassigned_child()` 函数，将 unassigned child 分类为：
- `"standalone_root"` — 内容完整，合法独立块（如表格、References）
- `"skipped_noise"` — 低质量噪声（如页码 "20"）

新增 `standalone_root_category` 字段记录分类结果。

噪声检测规则：
- `is_page_noise_text()` 匹配
- 内容 ≤3 字符
- 纯数字且 ≤5 字符

#### 2. 语义 Helper 函数

新增 `is_standalone_root(metadata, parent_chunk_id)` 和 `is_true_orphan(metadata, parent_chunk_id, chunk_type)` 函数，供 API/UI/检索层统一判断。

#### 3. Metadata 保留

Persistence 路径已通过 `**(child.metadata or {})` 保留所有 `standalone_root_child`、`parent_assignment_state`、`standalone_root_category` 等字段。

### 三态模型

| State | 条件 | parent_chunk_id | standalone_root_child | chunk_role |
|---|---|---|---|---|
| **parented_child** | 有真实 section/page/table owner | != NULL | false | child |
| **standalone_root** | 无 parent owner，内容完整可独立检索 | NULL | true | child（保留现状） |
| **skipped_noise** | 低质量噪声（页码等） | NULL | true | child（skip_embedding） |

判断方式：
- `is_standalone_root()` → 合法独立块
- `is_true_orphan()` → 结构异常（不应出现）
- `parent_chunk_id IS NULL AND chunk_type != 'parent_text'` → 包含以上所有情况

### 数据库诊断

对 `ec08e751-b787-490b-ae4c-2a3fd0a0c09f` 的当前 chunks：

| Category | Count |
|---|---|
| All NULL parent (non-parent_text) | 3 |
| Standalone roots | 3 |
| True orphans | 0 |

### 修改文件

| 文件 | 改动 | 理由 |
|---|---|---|
| `backend/app/services/chunker/canonical_parent_child.py` | 重构 standalone orphan resolution，添加分类和 helper | 核心修复 |
| `backend/tests/test_canonical_parent_child_unified.py` | 新增 9 个测试 | 覆盖所有 case |
| `docs/index.md` | 更新维护日期 | 文档维护 |
| `docs/daily_report_2026-08-11.md` | 新建日报 | 文档维护 |

### 测试结果

- `test_canonical_parent_child_unified.py`: 32 passed (23 原有 + 9 新增)
- `test_document_profile_dcp31.py`: 8 passed
- `test_media_parent_assignment.py`: 26 passed
- `test_visual_page_section_scope.py`: 19 passed
- `test_document_relation_phase2.py`: 19 passed
- `test_document_relation_phase3.py`: 8 passed

### 验收标准

- [x] 明确解释 Chunk 276/277/278 为什么 parent=NULL
- [x] 区分 standalone root 和 true orphan
- [x] 合法 standalone root 不再被当成异常
- [x] 低质量噪声不创建 Parent
- [x] 独立 table 不创建无意义 1:1 Parent
- [x] 独立 References 不创建无意义 1:1 Parent
- [x] 不影响 Final Media Parent Assignment
- [x] 不影响正常 Parent-Child
- [x] 不引入大量 1:1 Parent
- [x] 专项测试全部通过
- [x] 相关测试无新增失败

## Phase 2: 状态互斥与 structural_role 收尾

### 解决的问题

1. **skipped_noise / standalone_root 语义重叠**：`is_standalone_root()` 对所有 unassigned child 返回 True（因为它们都有 `standalone_root_child=true`），导致 `skipped_noise` 也被统计为 `standalone_root`。

2. **UI/API 仍显示为"子块（无父块）"**：前端 ChunkTreeView 把所有 `parent_chunk_id=NULL` 的 chunk 放入 `orphan_children` 并显示为"未挂载父块的子块"。

3. **缺少真实重解析验证**：数据库是旧解析数据，没有 `standalone_root_category` 字段。

### 修复内容

#### 1. 新增 `is_skipped_noise()` helper

检测规则（向后兼容）：
- `standalone_root_category == "skipped_noise"` — 新数据
- `skip_embedding_recommended == True` + `standalone_root_child == True` — 旧数据

#### 2. 修复 `is_standalone_root()` 互斥

排除 `is_skipped_noise()` 为 True 的 chunk：
- 旧数据无 category → `is_standalone_root=True`（向后兼容）
- 新数据 `category=skipped_noise` → `is_standalone_root=False`

#### 3. 修复 `is_true_orphan()` 互斥

现在显式排除 `is_standalone_root()` 和 `is_skipped_noise()`。

#### 4. 新增 `structural_role_for()` 统一函数

返回五值之一：`parent` / `child` / `standalone_root` / `skipped_noise` / `true_orphan`

#### 5. API 层：`chunk_to_dict()` 新增 `structural_role` 字段

在 `chunk_to_dict()` 中调用 `structural_role_for()` 计算并返回 `structural_role`，前端无需理解内部 metadata。

#### 6. API 层：`get_chunk_tree()` 四态分类

- `orphan_children` → 仅 true_orphan
- 新增 `standalone_roots` 列表
- 新增 `skipped_noise` 列表
- 新增 `structural_role_counts` 统计

#### 7. 前端：`ChunkListView.vue` 使用 `structural_role`

- 新增 `structuralRoleLabel()` / `structuralRoleClass()` helper
- 列表视图：显示 structural_role 标签
- 树视图：独立块（绿色）、低质量块（灰色）、孤立子块（橙色）分开展示
- 树视图统计：`父块 X · 独立 X · 噪声 X · 异常孤儿 X`

#### 8. 前端：TypeScript 类型更新

- `ChunkItem` 新增 `structural_role?: string`
- `ChunkTreeResponse` 新增 `standalone_roots`, `skipped_noise`, `standalone_root_count`, `skipped_noise_count`, `structural_role_counts`

### 四态互斥模型

| State | parent_chunk_id | standalone_root_child | standalone_root_category | structural_role |
|---|---|---|---|---|
| **parented_child** | != NULL | - | - | child |
| **standalone_root** | NULL | true | standalone_root | standalone_root |
| **skipped_noise** | NULL | true | skipped_noise | skipped_noise |
| **true_orphan** | NULL | false/absent | - | true_orphan |

判断优先级：`parent_text` → `parented_child` → `skipped_noise` → `standalone_root` → `true_orphan`

### 真实重新解析结果

对 `ec08e751-b787-490b-ae4c-2a3fd0a0c09f` (SODIUM ION BATTERY) 完整重解析：

| 原 chunk | 新 chunk | 内容 | structural_role |
|---|---|---|---|
| 276 | 270 | "20" | skipped_noise |
| 277 | 271 | patent family table | standalone_root |
| 278 | 272 | REFERENCES CITED... | standalone_root |

### 数据库统计

```
parent:           29
child (parented): 241
standalone_root:   2
skipped_noise:     1
true_orphan:       0
Total:            273  (Mutual exclusivity: PASS)
```

### 检索验证

- **Standalone Table** (chunk 271): hybrid search "patent family members international search report" → **#1 hit**, score=0.0164, parent=NULL ✅
- **Standalone References** (chunk 272): hybrid search "REFERENCES CITED IN THE DESCRIPTION EP 4 517 993" → **#1 hit**, score=0.0164, parent=NULL ✅
- **Skipped Noise** (chunk 270, "20"): 未进入检索结果 ✅

### LLM Context

Standalone root hit (`parent_chunk_id=NULL`) → `context_policy.expanded=False, reason="no_parent_chunk"`。Hit 自身内容完整保留在 `flat` 输出中，不丢内容，不抛异常。

### 测试结果

- **Backend 专项** (test_canonical_parent_child_unified): **49 passed** (32 original + 17 new)
- **Backend 全量**: **945 passed, 3 failed** (3 failures all pre-existing, confirmed via git stash)
- **Frontend**: **build passed** (vue-tsc --noEmit + vite build)
- **新增回归**: 0

### 修改文件

| 文件 | 改动 |
|---|---|
| `backend/app/services/chunker/canonical_parent_child.py` | 新增 `is_skipped_noise()`, `structural_role_for()`；修复 `is_standalone_root()`, `is_true_orphan()` 互斥 |
| `backend/app/services/chunk_service.py` | `chunk_to_dict()` 新增 `structural_role`；`get_chunk_tree()` 四态分类 |
| `backend/tests/test_canonical_parent_child_unified.py` | 新增 17 个测试（互斥、向后兼容、structural_role 全覆盖） |
| `frontend/src/api/chunks.ts` | `ChunkItem` 新增 `structural_role`；`ChunkTreeResponse` 新增 standalone/skipped 字段 |
| `frontend/src/views/ChunkListView.vue` | 列表/树视图使用 `structural_role` 标签；独立块/噪声/异常分开展示 |
| `docs/index.md` | 更新维护日期 |
| `docs/daily_report_2026-08-11.md` | Phase 2 记录 |
