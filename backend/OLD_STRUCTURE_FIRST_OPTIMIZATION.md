# backend_old 保守优化说明

本次以 `backend_old` 为唯一基线，不引入 Now/Hybrid/Exact-WeKnora 的新架构。

## 运行时策略

```text
Canonical Markdown
→ 原 Old two-pass Parent/Child skeleton
→ 全局 heading context 继承
→ Text/Table/Image materialization
→ 精确 overlap shadow 去重
→ Parent 0/1 完整性校验
→ Persist
```

## 明确撤出的运行时行为

`merge_short_text_siblings()` 仍保留为兼容/实验 helper，但不再由生产 `build_canonical_parent_child_plan()` 自动调用。

原因：短块不等于错误。自动 pairwise merge 会以长度优化覆盖 Old 版本最重要的优势——稳定章节和标题边界。

## 新增保护

- `<sup>...</sup>` / `<sub>...</sub>` / 常见 inline HTML
- inline / block LaTeX
- HTML table/pre/code
- HTML entity
- Markdown link/image
- fenced code / Markdown table

超长单行代码保持原子，不再按字符切断。

## 保留的确定性修复

- Markdown 表格保序与 EOF 表格识别
- Parser header/footer/page-number 噪声过滤
- `526 / 524 / 117` 连续页面噪声过滤
- exact overlap shadow 去重
- Parent 0/1 child 完整性校验
- Image/Table/Code 等结构 materialization
