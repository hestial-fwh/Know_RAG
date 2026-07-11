from weknora_chunker_reference import (
    SplitterConfig,
    build_parent_child_configs,
    split,
    split_parent_child,
    resolve_parent_content,
)

text = """# 第一章 项目背景

这是第一段内容。这里模拟 PDF 解析后的 MarkdownContent。

## 1.1 研究背景

这是第二段内容。WeKnora 会尽量保留标题路径作为 context_header。

| 指标 | 数值 |
| --- | --- |
| A | 10 |
| B | 20 |

# 第二章 方法

这是方法章节内容。"""

base_cfg = SplitterConfig(chunk_size=120, chunk_overlap=20, strategy="auto")

print("普通切分：")
for c in split(text, base_cfg):
    print("---")
    print("seq:", c.seq, "range:", (c.start, c.end))
    print("context_header:", repr(c.context_header))
    print("embedding_text:")
    print(c.embedding_content(document_title="示例文档"))

print("\n父子切分：")
parent_cfg, child_cfg = build_parent_child_configs(base_cfg, parent_chunk_size=220, child_chunk_size=90)
pc = split_parent_child(text, parent_cfg, child_cfg)
print("parents:", len(pc.parents), "children:", len(pc.children))
for child in pc.children:
    print("---")
    print("child seq:", child.seq, "parent_index:", child.parent_index)
    print("retrieval_child:", child.content[:80].replace("\n", " "))
    print("llm_context_after_parent_resolve:", resolve_parent_content(child, pc.parents)[:120].replace("\n", " "))
