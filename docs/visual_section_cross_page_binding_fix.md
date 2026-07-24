#视觉类文档跨页绑定修复

## 问题

`_build_visual_section_parents()` (Phase 0-4) 存在通用设计缺陷：Phase 3 孤儿绑定只在**同页内**搜索保留父块，无法处理书画类文档常见的**文字页/图片页交替**结构。

### 实际文档结构（diskfcl87_fcl87_05-页面.pdf）

```
Page A（文字页）: 王羲之介绍 (text child) + 作品图 (image child)   → 1 个父块
Page B（纯图页）: 只有作品图 (image child)，无 text child          → 纯图页，独立父块 ❌
Page C（文字页）: 赵孟頫介绍 (text child) + 作品图 (image child)   → 1 个父块
Page D（纯图页）: 只有作品图 (image child)，无 text child          → 纯图页，独立父块 ❌
...
纯图页（B/D...）没有 text 类型 child，应该向上绑定到最近的前一个有文字页。
注意：分类和绑定完全基于内容（有无语义文本），与页号无关。这里用 A/B/C/D 描述交替模式。
```

### 根因

```
Phase 2: _section_has_semantic_text() 只检查 text child → 纯图页返回 False
         → 偶数页全部分类为"溶解"
Fallback: 当前页无保留父块 → 提升第一个溶解段为页面父块（line 2304-2310）
         → 偶数页图片仍然独立成一个父块 ❌

Phase 3: _nearest_preceding_kept() 只在同页 sorted_keys 内搜索
         → 偶数页图片无法跨页找到前一页的文字父块 ❌
```

## 通用场景

不只奇偶交替，以下场景都有问题：

```
// 一个作者多页作品
Page 3: 作者A介绍 (text) ─┐
Page 4: 作品图1 (无text)   │
Page 5: 作品图2 (无text)   ├─ 应该全归到作者A的父块
Page 6: 作品图3 (无text) ─┘
Page 7: 作者B介绍 (text) ─┐
Page 8: 作品图4 (无text) ─┘

// 页首纯图
Page 1: 封面图 (无text)
Page 2: 封面图 (无text)
Page 3: 序言 (text)        ← 前两页应向下绑定到此
```

## 修改方案

### 核心思路

在 Phase 3 和 Phase 4 之间增加 **Pass 2: 跨页绑定**：

```
Pass 1 (Phase 2+3): 逐页分类 + 同页内绑定（不物化）
    ↓
Pass 2 (NEW): 跨页绑定 — 纯图页 children → 最近前一个文字页
    ↓
Pass 3 (Phase 4): 统一物化
```

### 跨页绑定规则

1. 遍历所有页，找出"无语义文本"的页
2. 对该页的每个 child，找到**最近的前一个**有语义文本的页
3. 绑定到该文字页的**最后一个**保留段（向下兼容：若前方无文字页，绑定到最近的后一个文字页的第一个保留段）
4. 绑定后，纯图页不出现在 Phase 4 物化中
5. 纯图页全部在前面（文档开头）且后方也无文字页 → fallback 到页面级父块

### 改动文件

| 文件 | 改动 |
|------|------|
| `backend/app/services/chunker/canonical_parent_child.py` | `_build_visual_section_parents()` 重构为三 Pass |
| `backend/tests/test_visual_page_section_scope.py` | 新增跨页绑定测试 |
| `docs/visual_section_cross_page_binding_fix.md` | 本文档 |

### 不改动

- `_build_visual_page_parents()` — flag=off 时保持不变
- `_split_dcp3_policy_segments()` — 切分逻辑不变
- `split_parent_child_result()` — 切分器不变
- 所有 DCP 策略分类逻辑 — child 的 `parent_index` 和 `visual_page_no` 元数据不变

### 兼容性

- 同页内多文字段的场景：Pass 2 不触发（该页已有 kept sections），行为不变
- 纯文字文档：`visual_page_ranges` 为空，不进此分支
- singleton page_primary_visual：Pass 1 已保留（Phase 2 判定），不受 Pass 2 影响
- flag=off：走 `_build_visual_page_parents()`，完全不受影响

## 验证

1. `test_visual_page_section_scope.py` 现有 7 个测试继续通过
2. 新增测试：
   - `test_cross_page_images_bind_to_preceding_text` — 连续两页纯图绑定到前一页文字
   - `test_multi_image_pages_bind_to_same_text` — 多页作品图全绑定到同一个作者文字
   - `test_leading_pure_images_bind_downward` — 页首纯图向下绑定
   - `test_flag_off_no_cross_page_binding` — flag=off 保持旧行为
3. 全量回归：`docker compose exec backend python -m pytest tests/ -v`
