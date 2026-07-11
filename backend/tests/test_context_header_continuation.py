import unittest
from types import SimpleNamespace

from app.services.chunker.heading_utils import (
    make_continuation_header,
    normalize_context_header,
    strip_continuation_suffix,
)
from app.services.chunker.models import ChunkerConfig
from app.services.chunker.parent_child_splitter import split_parent_child
from app.services.chunker.recursive_splitter import split_recursive
from app.services.chunk_service import _build_parent_content
from app.services.document_parser.types import ParsedBlock
from app.services.embedding_service import build_embedding_content


class ContextHeaderContinuationTests(unittest.TestCase):
    def test_continuation_helpers_are_idempotent(self):
        header = "文档内容"
        self.assertEqual(strip_continuation_suffix("文档内容（续）（续）"), "文档内容")
        for _ in range(5):
            header = make_continuation_header(header)
            self.assertEqual(header, "文档内容（续）")
        self.assertEqual(normalize_context_header("文档内容（续）（续）（续）"), "文档内容（续）")

    def test_recursive_split_uses_single_continuation_suffix(self):
        text = "这是一个较长段落，用于触发多块切分。" * 20
        chunks = split_recursive(
            text,
            ChunkerConfig(chunk_size=55, chunk_overlap=0, strategy="recursive"),
            context_header="文档内容",
        )
        self.assertGreaterEqual(len(chunks), 3)
        self.assertEqual(chunks[0].context_header, "文档内容")
        self.assertTrue(all(chunk.context_header == "文档内容（续）" for chunk in chunks[1:]))

    def test_parent_child_split_does_not_stack_continuation_suffix(self):
        chunks = split_parent_child(
            "正文内容。" * 80,
            ChunkerConfig(
                chunk_size=80,
                chunk_overlap=0,
                strategy="recursive",
                enable_parent_child=True,
                parent_chunk_size=120,
                child_chunk_size=40,
                child_chunk_overlap=0,
            ),
        )
        for chunk in chunks:
            chunk.context_header = normalize_context_header("文档内容（续）（续）")
        normalized = [normalize_context_header(chunk.context_header) for chunk in chunks]
        self.assertTrue(all(header == "文档内容（续）" for header in normalized))

    def test_embedding_header_is_normalized_and_content_is_not_rewritten(self):
        chunk = SimpleNamespace(
            chunk_type="text",
            context_header="文档内容（续）（续）",
            content="正文内容没有重复标题。" * 8,
            metadata_json={},
            flags={},
            image_info={},
        )
        document = SimpleNamespace(title="测试文档", file_name="")
        embedding_text = build_embedding_content(chunk, document)
        self.assertIn("文档内容（续）", embedding_text)
        self.assertNotIn("文档内容（续）（续）", embedding_text)
        self.assertNotIn("文档内容", chunk.content[:4])

    def test_parent_content_does_not_duplicate_context_header(self):
        section = {
            "title": "文档内容（续）（续）",
            "heading_path": ["文档内容（续）（续）"],
            "blocks": [
                ParsedBlock(block_type="text", text="文档内容（续）（续）\n正文主体内容足够长。", order_index=0)
            ],
        }
        parent = _build_parent_content(section, "md", document=None)
        self.assertFalse(parent.startswith("文档内容（续）（续）"))
        self.assertTrue(parent.startswith("正文主体内容"))


if __name__ == "__main__":
    unittest.main()
