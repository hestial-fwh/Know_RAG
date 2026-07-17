import unittest
from types import SimpleNamespace

from app.services.embedding_service import build_embedding_content


class EmbeddingContentTests(unittest.TestCase):
    def test_build_embedding_content_joins_title_context_and_content(self):
        body = "这是正文，包含足够的信息用于向量化检索。" * 8
        chunk = SimpleNamespace(context_header="第一章 > 第二节", content=body, chunk_type="text", flags={}, metadata_json={})
        document = SimpleNamespace(title="测试文档", file_name="test.md")
        content = build_embedding_content(chunk, document)
        self.assertIn("测试文档", content)
        self.assertIn("第一章 > 第二节", content)
        self.assertIn("这是正文", content)

    def test_empty_context_header_does_not_emit_context_section(self):
        chunk = SimpleNamespace(context_header="", content="正文内容足够完整，用于验证无上下文时仍保留标题和正文。" * 6, chunk_type="text", flags={}, metadata_json={})
        document = SimpleNamespace(title="标题", file_name="")
        content = build_embedding_content(chunk, document)
        self.assertNotIn("上下文：", content)
        self.assertIn("标题", content)
        self.assertIn("正文", content)

    def test_nul_bytes_are_sanitized(self):
        chunk = SimpleNamespace(context_header="", content=("abc\x00def " * 30), chunk_type="text", flags={}, metadata_json={})
        document = SimpleNamespace(title="标题\x00", file_name="")
        content = build_embedding_content(chunk, document)
        self.assertNotIn("\x00", content)
        self.assertIn("abcdef", content)

    def test_long_body_is_truncated_but_keeps_title(self):
        chunk = SimpleNamespace(context_header="上下文", content="x" * 20000, chunk_type="text", flags={}, metadata_json={})
        document = SimpleNamespace(title="标题", file_name="")
        content = build_embedding_content(chunk, document)
        self.assertLessEqual(len(content), 6000)
        self.assertTrue(content.startswith("标题"))
        self.assertIn("上下文", content)

    def test_parent_text_is_not_embedded(self):
        chunk = SimpleNamespace(context_header="章", content="父块正文", chunk_type="parent_text", flags={}, metadata_json={})
        document = SimpleNamespace(title="标题", file_name="")
        self.assertEqual(build_embedding_content(chunk, document), "")

    def test_presentation_html_is_removed_from_text_embedding(self):
        chunk = SimpleNamespace(
            context_header="",
            content=(
                "H<sub>2</sub>O 与 x<sup>2</sup> 的结果，"
                "<span class='ocr'>这是有效正文。</span>"
                "统计结果 p &lt; 0.05，且 a < b > c。"
                "<script>\nwindow.secret = true\n</script>"
            )
            * 8,
            chunk_type="text",
            flags={},
            metadata_json={"content_kind": "text"},
        )
        content = build_embedding_content(chunk, SimpleNamespace(title="实验", file_name=""))

        self.assertIn("H2O", content)
        self.assertIn("x2", content)
        self.assertIn("这是有效正文", content)
        self.assertIn("p < 0.05", content)
        self.assertIn("a < b > c", content)
        self.assertNotIn("<sub>", content)
        self.assertNotIn("<span", content)
        self.assertNotIn("window.secret", content)

    def test_xml_tags_are_preserved_for_code_block_embedding(self):
        chunk = SimpleNamespace(
            context_header="## 配置示例",
            content='<field name="title"><token>value</token></field>',
            chunk_type="text",
            flags={"content_kind": "code_block"},
            metadata_json={"content_kind": "code_block"},
        )
        content = build_embedding_content(
            chunk,
            SimpleNamespace(title="Schema 配置", file_name=""),
        )

        self.assertIn('<field name="title">', content)
        self.assertIn("<token>value</token>", content)


if __name__ == "__main__":
    unittest.main()
