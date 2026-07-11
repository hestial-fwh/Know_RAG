import unittest

from app.services.chunker.heading_utils import is_caption_text, looks_like_section_heading
from app.services.chunker.models import ChunkerConfig
from app.services.chunker.parent_child_splitter import split_parent_child
from app.services.chunker.service import split_document
from app.services.chunk_service import _build_parent_content
from app.services.document_parser.types import ParsedBlock


class ChunkerTest(unittest.TestCase):
    def test_markdown_heading_context_header(self):
        chunks = split_document("# 一级标题\n\n## 二级标题\n\n正文内容", "md", ChunkerConfig(chunk_size=50, strategy="auto"))
        self.assertTrue(chunks)
        self.assertIn("一级标题", chunks[-1].context_header)
        self.assertIn("二级标题", chunks[-1].context_header)
        self.assertEqual(chunks[-1].metadata["heading_path"], ["一级标题", "二级标题"])

    def test_markdown_table_is_not_split_when_within_limit(self):
        text = "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
        chunks = split_document(text, "md", ChunkerConfig(chunk_size=80, strategy="recursive"))
        self.assertEqual(len(chunks), 1)
        self.assertIn("| --- | --- |", chunks[0].content)

    def test_fenced_code_block_is_not_split_when_within_limit(self):
        text = '```python\nprint("hello")\nprint("world")\n```'
        chunks = split_document(text, "md", ChunkerConfig(chunk_size=80, strategy="recursive"))
        self.assertEqual(len(chunks), 1)
        self.assertIn('print("hello")', chunks[0].content)

    def test_chunks_have_start_and_end(self):
        chunks = split_document("第一段。\n\n第二段。", "txt", ChunkerConfig(chunk_size=5, chunk_overlap=0, strategy="recursive"))
        self.assertTrue(all(chunk.start is not None and chunk.end is not None for chunk in chunks))

    def test_parent_child_disabled_generates_text_chunks_only(self):
        chunks = split_document("第一段。\n\n第二段。", "txt", ChunkerConfig(chunk_size=20, enable_parent_child=False))
        self.assertTrue(chunks)
        self.assertEqual({chunk.chunk_type for chunk in chunks}, {"text"})

    def test_parent_child_enabled_generates_parent_and_text_chunks(self):
        chunks = split_document(
            "# 标题\n\n" + "正文" * 200,
            "md",
            ChunkerConfig(
                chunk_size=80,
                chunk_overlap=10,
                strategy="heading",
                enable_parent_child=True,
                parent_chunk_size=120,
                child_chunk_size=40,
                child_chunk_overlap=5,
            ),
        )
        self.assertIn("parent_text", {chunk.chunk_type for chunk in chunks})
        self.assertIn("text", {chunk.chunk_type for chunk in chunks})
        self.assertTrue(any(chunk.parent_index is not None for chunk in chunks if chunk.chunk_type == "text"))

    def test_recursive_chinese_sentence_split(self):
        text = "这是第一句。这里有问题吗？没有问题；继续说明，补充背景，然后结束。"
        chunks = split_document(text, "txt", ChunkerConfig(chunk_size=18, chunk_overlap=0, strategy="recursive"))
        self.assertGreaterEqual(len(chunks), 1)
        self.assertTrue(all(chunk.content.strip() for chunk in chunks))
        self.assertIn("这是第一句", "\n".join(chunk.content for chunk in chunks))
        self.assertIn("然后结束", "\n".join(chunk.content for chunk in chunks))

    def test_strict_heading_detection_does_not_treat_captions_as_headings(self):
        self.assertTrue(looks_like_section_heading("第一章 绪论"))
        self.assertTrue(looks_like_section_heading("1.1 研究背景"))
        self.assertFalse(looks_like_section_heading("图 1 系统结构"))
        self.assertFalse(looks_like_section_heading("表 1 实验结果"))
        self.assertTrue(is_caption_text("图 1 系统结构"))
        self.assertTrue(is_caption_text("表 1 实验结果"))

    def test_parent_child_config_propagation(self):
        cfg = ChunkerConfig(
            chunk_size=80,
            chunk_overlap=8,
            strategy="heuristic",
            separators=["\n\n", "。", ""],
            enable_parent_child=True,
            parent_chunk_size=120,
            child_chunk_size=40,
            child_chunk_overlap=4,
            token_limit=2048,
            languages=["zh", "en"],
        )
        chunks = split_parent_child("第一章 绪论\n\n这是正文。" * 8, cfg)
        child = next(chunk for chunk in chunks if chunk.chunk_type == "text")
        self.assertEqual(child.metadata["strategy"], "heuristic")
        self.assertEqual(child.metadata["token_limit"], 2048)
        self.assertEqual(child.metadata["languages"], ["zh", "en"])
        self.assertEqual(child.metadata["separators"], ["\n\n", "。", ""])

    def test_parent_content_keeps_table_and_image_compact(self):
        table_body = "\n".join(f"| row{i} | value{i} | noisy detail {i} |" for i in range(80))
        image_ocr = "OCR 噪声文本 " * 80
        section = {
            "title": "第一章 绪论",
            "heading_path": ["第一章 绪论"],
            "blocks": [
                ParsedBlock(block_type="text", text="这是正文内容，介绍研究背景和方法。" * 8, order_index=1),
                ParsedBlock(
                    block_type="table",
                    text=table_body,
                    order_index=2,
                    metadata={"caption": "表 1 实验结果"},
                ),
                ParsedBlock(
                    block_type="image",
                    text="",
                    order_index=3,
                    page_no=5,
                    metadata={"caption": "图 1 系统结构", "ocr_text": image_ocr},
                ),
            ],
        }
        parent = _build_parent_content(section, "pdf", document=None)
        self.assertIn("表格摘要", parent)
        self.assertIn("图片标题", parent)
        self.assertIn("row0", parent)
        self.assertNotIn("row79", parent)
        self.assertLess(parent.count("OCR 噪声文本"), 25)


if __name__ == "__main__":
    unittest.main()
