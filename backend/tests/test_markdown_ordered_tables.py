import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


from app.services.document_parser.ragflow_format_adapter import parse_markdown
from app.services.document_parser.markdown_ordered import (
    is_markdown_table_header,
    split_markdown_preserving_tables,
)


class MarkdownOrderedTableTests(unittest.TestCase):
    def test_eof_table_without_trailing_newline_is_detected(self):
        text = "# 标题\n\n前文\n\n| A | B |\n| --- | --- |\n| 1 | 2 |"
        segments = split_markdown_preserving_tables(text)
        self.assertEqual([segment.kind for segment in segments], ["text", "table"])
        self.assertEqual(segments[-1].table_format, "pipe")
        self.assertIn("| 1 | 2 |", segments[-1].content)

    def test_tables_keep_original_order_between_sections(self):
        text = (
            "# 第一章\n\n第一段。\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
            "## 第二章\n\n第二段。\n\n"
            "| C | D |\n| --- | --- |\n| 3 | 4 |"
        )
        segments = split_markdown_preserving_tables(text)
        self.assertEqual([segment.kind for segment in segments], ["text", "table", "text", "table"])
        self.assertIn("第一章", segments[0].content)
        self.assertIn("| A | B |", segments[1].content)
        self.assertIn("第二章", segments[2].content)
        self.assertIn("| C | D |", segments[3].content)

    def test_table_like_content_inside_fenced_code_is_not_extracted(self):
        text = "```md\n| A | B |\n| --- | --- |\n| 1 | 2 |\n```\n"
        segments = split_markdown_preserving_tables(text)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].kind, "text")

    def test_table_without_outer_pipes_is_supported(self):
        text = "A | B\n--- | ---\n1 | 2"
        segments = split_markdown_preserving_tables(text)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].kind, "table")
        self.assertTrue(is_markdown_table_header("A | B", "--- | ---"))

    def test_raw_html_table_keeps_position(self):
        text = "前文\n\n<table>\n<tr><td>A</td></tr>\n</table>\n\n后文"
        segments = split_markdown_preserving_tables(text)
        self.assertEqual([segment.kind for segment in segments], ["text", "table", "text"])
        self.assertEqual(segments[1].table_format, "html")
        self.assertIn("后文", segments[2].content)

    def test_escaped_pipe_in_header_does_not_create_extra_column(self):
        text = "| A \\| alias | B |\n| --- | --- |\n| x | y |"
        segments = split_markdown_preserving_tables(text)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].kind, "table")

    def test_parse_markdown_emits_ordered_table_blocks_and_heading_metadata(self):
        text = (
            "# 第一章\n\n第一段。\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
            "## 第二章\n\n第二段。\n\n"
            "| C | D |\n| --- | --- |\n| 3 | 4 |"
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "case.md"
            path.write_text(text, encoding="utf-8")
            document = parse_markdown(path)

        self.assertEqual(
            [block.block_type for block in document.blocks],
            ["text", "text", "table", "text", "text", "table"],
        )
        self.assertEqual(document.blocks[0].metadata.get("raw_block_type"), "heading")
        self.assertEqual(document.blocks[3].metadata.get("level"), 2)
        self.assertEqual(document.blocks[2].metadata.get("start_line"), 4)
        self.assertEqual(document.blocks[5].metadata.get("start_line"), 12)
        self.assertEqual(document.metadata.get("markdown_table_count"), 2)
        self.assertTrue(document.metadata.get("markdown_order_preserved"))
        self.assertEqual(document.metadata.get("parser"), "native_markdown_ordered")



if __name__ == "__main__":
    unittest.main()
