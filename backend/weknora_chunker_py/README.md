# WeKnora Chunker Python Reference

这个包是把 WeKnora 的文档切分思路整理成 Python 参考实现，方便后续交给 Codex / Claude Code 修改 Know_RAG。

## 文件

- `weknora_chunker_reference.py`：核心 Python 参考实现。
- `example_usage.py`：最小调用示例。
- `CODE_MAP.md`：WeKnora Go 源码与 Python 参考实现的对应关系，以及迁移到 Know_RAG 的建议。

## 核心思想

WeKnora 的切分链路不是单纯父子切分，而是：

```text
MarkdownContent
  -> auto / heading / heuristic / legacy 策略选择
  -> protected span，保护表格、图片、链接、公式、代码块
  -> ContextHeader 标题路径增强
  -> child 入向量，parent 只入库
  -> 检索命中 child 后回填 parent 内容
```

## 命令行测试

```bash
python weknora_chunker_reference.py input.md --strategy auto
python weknora_chunker_reference.py input.md --parent-child --json
```

## Know_RAG 改造建议

1. MinerU 输出后先做 `block_merge / reflow`，整理成 Markdown-like text。
2. chunker 输入不要是零散 OCR 行，而应该是 MarkdownContent。
3. 切分时保护 Markdown 表格、图片、公式、代码块。
4. 向量化文本使用：`document_title + context_header/heading_path + chunk.content`。
5. 父子切分时只索引 child，parent 只入库。
6. 检索后通过 child.parent_id 回填 parent.content 给 LLM。
7. 构造 parent/child config 时要透传 `strategy/token_limit/languages`，不要只传 size/overlap。
