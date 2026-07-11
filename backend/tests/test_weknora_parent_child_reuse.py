import unittest
from collections import Counter
from types import SimpleNamespace

from app.services.chunker.models import ChunkerConfig
from app.services.chunker.parent_child_splitter import (
    merge_breadcrumbs,
    split_parent_child_result,
)
from app.services.chunk_service import (
    _enforce_parent_child_integrity,
    _rebuild_markdown_weknora_parent_child,
)
from app.services.document_parser.types import ParsedDocument


class WeKnoraParentChildReuseTests(unittest.TestCase):
    def _cfg(self, *, parent_size: int = 800, child_size: int = 180) -> ChunkerConfig:
        return ChunkerConfig(
            chunk_size=512,
            chunk_overlap=80,
            strategy="auto",
            separators=["\n\n", "\n", "。"],
            enable_parent_child=True,
            parent_chunk_size=parent_size,
            child_chunk_size=child_size,
            child_chunk_overlap=64,  # WeKnora derives actual child overlap as size/5.
        )

    def test_short_candidate_is_not_materialized_as_parent(self):
        text = "# 简短说明\n\n这是一段完整且足够表达语义的短内容。"
        result = split_parent_child_result(text, self._cfg(parent_size=2000, child_size=500))
        self.assertEqual(len(result.parents), 0)
        self.assertEqual(len(result.children), 1)
        self.assertIsNone(result.children[0].parent_index)
        self.assertFalse(result.children[0].metadata["parent_materialized"])

    def test_materialized_parents_always_have_multiple_children(self):
        text = (
            "# 系统说明\n\n"
            "## 第一章\n\n" + "第一章正文内容。" * 180 + "\n\n"
            "## 第二章\n\n" + "第二章正文内容。" * 180 + "\n\n"
            "## 第三章\n\n" + "第三章正文内容。" * 180
        )
        result = split_parent_child_result(text, self._cfg(parent_size=900, child_size=220))
        counts = Counter(child.parent_index for child in result.children if child.parent_index is not None)
        self.assertGreater(len(result.parents), 0)
        self.assertTrue(all(counts[index] >= 2 for index in range(len(result.parents))))

    def test_two_pass_split_does_not_create_parent_per_subheading(self):
        sections = []
        for chapter in range(1, 5):
            sections.append(f"## 第 {chapter} 章\n\n")
            for subsection in range(1, 6):
                sections.append(f"### {chapter}.{subsection} 小节\n\n")
                sections.append("这是小节正文内容。" * 12 + "\n\n")
        result = split_parent_child_result("# 项目文档\n\n" + "".join(sections), self._cfg(parent_size=1400, child_size=260))
        # 20 H3 subheadings must not become 20 parents. WeKnora first builds
        # large parent windows, then re-splits each window into child chunks.
        self.assertLess(len(result.parents), 20)
        counts = Counter(child.parent_index for child in result.children if child.parent_index is not None)
        self.assertTrue(all(value >= 2 for value in counts.values()))

    def test_breadcrumb_merge_deduplicates_seam(self):
        self.assertEqual(
            merge_breadcrumbs("# Top\n## A", "## A\n### A1"),
            "# Top\n## A\n### A1",
        )

    def test_markdown_runtime_preserves_code_and_tree_layout(self):
        text = (
            "# 开发指南\n\n"
            "```python\n"
            "def demo():\n"
            "    value = 1\n"
            "    return value\n"
            "```\n\n"
            "```text\n"
            "root/\n"
            "├── app/\n"
            "│   ├── services/\n"
            "│   └── api/\n"
            "└── tests/\n"
            "```\n\n"
            + "正文内容。" * 120
        )
        document = SimpleNamespace(id="doc-layout", knowledge_base_id="kb-layout")
        cfg = SimpleNamespace(
            chunk_size=512,
            chunk_overlap=80,
            strategy="auto",
            separators=["\n\n", "\n", "。"],
            parent_chunk_size=500,
            child_chunk_size=220,
            child_chunk_overlap=64,
            token_limit=0,
            languages=[],
            enable_parent_child=True,
        )
        chunks, _ = _rebuild_markdown_weknora_parent_child(
            document,
            ParsedDocument.from_text(text),
            "md",
            cfg,
        )
        self.assertTrue(any("    return value" in chunk.content for chunk in chunks))
        self.assertTrue(any("│   ├── services/" in chunk.content for chunk in chunks))

    def test_markdown_rebuild_has_no_zero_or_one_child_parent(self):
        text = (
            "# 开发指南\n\n"
            "## OCR 与视觉增强\n\n" + "OCR 正文说明。" * 160 + "\n\n"
            "### OCR 模板\n\n" + "模板规则说明。" * 100 + "\n\n"
            "## 对象存储\n\n" + "对象存储说明。" * 160 + "\n\n"
            "### MinIO 对象键命名规范\n\n" + "键命名规则。" * 120
        )
        document = SimpleNamespace(id="doc-test", knowledge_base_id="kb-test")
        cfg = SimpleNamespace(
            chunk_size=512,
            chunk_overlap=80,
            strategy="auto",
            separators=["\n\n", "\n", "。"],
            parent_chunk_size=1200,
            child_chunk_size=260,
            child_chunk_overlap=64,
            token_limit=0,
            languages=[],
            enable_parent_child=True,
        )
        chunks, _ = _rebuild_markdown_weknora_parent_child(
            document,
            ParsedDocument.from_text(text),
            "md",
            cfg,
        )
        chunks = _enforce_parent_child_integrity(chunks)
        counts = Counter(
            chunk.parent_chunk_id
            for chunk in chunks
            if chunk.chunk_type != "parent_text" and chunk.parent_chunk_id
        )
        parents = [chunk for chunk in chunks if chunk.chunk_type == "parent_text"]
        self.assertTrue(parents)
        self.assertTrue(all(counts[parent.id] >= 2 for parent in parents))


if __name__ == "__main__":
    unittest.main()
