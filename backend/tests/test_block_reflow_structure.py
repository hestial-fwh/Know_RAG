import unittest

from app.services.chunker.heading_utils import is_caption_text
from app.services.document_parser.block_reflow import reflow_blocks
from app.services.document_parser.types import ParsedBlock


class BlockReflowStructureTests(unittest.TestCase):
    def test_directory_tree_block_is_not_merged_with_prose(self):
        blocks = [
            ParsedBlock(block_type="text", text="前一段普通正文还没有结束", order_index=0, page_no=1),
            ParsedBlock(
                block_type="text",
                text="""backend/
├── app/
│ ├── services/
│ │ ├── chunk_service.py # 分块管理
│ │ └── embedding_service.py # 向量嵌入""",
                order_index=1,
                page_no=1,
            ),
            ParsedBlock(block_type="text", text="后一段普通正文继续说明。", order_index=2, page_no=1),
        ]
        output, _ = reflow_blocks(blocks)
        self.assertEqual(len(output), 3)
        self.assertEqual([block.order_index for block in output], [0, 1, 2])
        self.assertTrue(output[1].metadata.get("structure_like"))
        self.assertEqual(output[1].metadata.get("content_kind"), "directory_tree")
        self.assertIn("chunk_service.py", output[1].text)
        self.assertNotIn("前一段普通正文", output[1].text)
        self.assertNotIn("后一段普通正文", output[1].text)


class TableContinuationCaptionTests(unittest.TestCase):
    """Test 1 & 5: Table continuation captions are recognized as captions,
    not as section headings, and do NOT pollute the heading stack."""

    def _reflow_with_heading_metadata(self, text: str, text_level: int = 2) -> tuple:
        """Helper: create a ParsedBlock with heading metadata and reflow it."""
        block = ParsedBlock(
            block_type="text",
            text=text,
            order_index=0,
            page_no=1,
            metadata={"text_level": text_level, "is_heading": True, "parser": "mineru"},
        )
        output, _ = reflow_blocks([block])
        return output

    def test_continued_table_2_recognized_as_caption(self):
        """续表2 is detected as caption, not heading."""
        self.assertTrue(is_caption_text("续表2"))
        self.assertTrue(is_caption_text("续表 2"))
        self.assertTrue(is_caption_text("表2（续）"))
        self.assertTrue(is_caption_text("表 2（续）"))
        self.assertTrue(is_caption_text("Continued Table 2"))
        self.assertTrue(is_caption_text("Table 2 (continued)"))

    def test_regular_table_caption_still_recognized(self):
        """Regular table captions are still detected."""
        self.assertTrue(is_caption_text("表 1 化合物列表"))
        self.assertTrue(is_caption_text("Table 1 Compounds"))
        self.assertTrue(is_caption_text("图 1 结构式"))

    def test_real_section_heading_not_caption(self):
        """Real section headings are NOT classified as captions."""
        self.assertFalse(is_caption_text("2 药理作用"))
        self.assertFalse(is_caption_text("3 结语与展望"))
        self.assertFalse(is_caption_text("1 化学成分"))
        self.assertFalse(is_caption_text("结果详见 Table 2 (continued) 的下一部分"))
        self.assertFalse(is_caption_text("正文引用了续表 2 中的数据"))

    def test_continued_table_caption_does_not_update_heading_stack(self):
        """Test 1 core: '续表2' with text_level=2 must NOT enter heading_stack.

        The block should be marked as caption and inherit the surrounding
        context_header, but its own text must not become part of the
        heading path for subsequent blocks.
        """
        # First, establish a real heading
        heading_block = ParsedBlock(
            block_type="text",
            text="2 药理作用",
            order_index=0,
            page_no=1,
            metadata={"text_level": 2, "is_heading": True, "parser": "mineru"},
        )
        # Body text under that heading
        body_block = ParsedBlock(
            block_type="text",
            text="羧甲基茯苓多糖抗肝纤维",
            order_index=1,
            page_no=1,
        )
        # Table continuation caption mis-tagged as heading by parser
        caption_block = ParsedBlock(
            block_type="text",
            text="续表 2",
            order_index=2,
            page_no=1,
            metadata={"text_level": 2, "is_heading": True, "parser": "mineru"},
        )
        # Continuation text after the floating table
        continuation_block = ParsedBlock(
            block_type="text",
            text="化的研究，结果显示羧甲基茯苓多糖具有保护作用。",
            order_index=3,
            page_no=1,
        )

        output, _ = reflow_blocks(
            [heading_block, body_block, caption_block, continuation_block]
        )

        # Find the caption block in output
        captions = [b for b in output if "续表" in (b.text or "")]
        self.assertEqual(len(captions), 1)
        caption_out = captions[0]
        self.assertTrue(caption_out.metadata.get("is_caption"))
        # Caption must NOT be in the heading path
        heading_path = caption_out.metadata.get("heading_path") or []
        caption_texts = [t for t in heading_path if "续表" in t]
        self.assertEqual(
            len(caption_texts), 0,
            f"Caption text should not appear in heading_path: {heading_path}"
        )

        # Continuation block must inherit "2 药理作用", NOT "续表 2"
        cont_blocks = [b for b in output if "化的研究" in (b.text or "")]
        self.assertEqual(len(cont_blocks), 1)
        cont_out = cont_blocks[0]
        ctx = cont_out.context_header or ""
        self.assertIn("2 药理作用", ctx)
        self.assertNotIn("续表", ctx)

    def test_real_heading_still_updates_stack(self):
        """Real chapter headings like '## 3 结语与展望' still enter heading_stack."""
        heading_2 = ParsedBlock(
            block_type="text",
            text="2 药理作用",
            order_index=0,
            page_no=1,
        )
        heading_3 = ParsedBlock(
            block_type="text",
            text="3 结语与展望",
            order_index=1,
            page_no=1,
            metadata={"text_level": 2, "is_heading": True, "parser": "mineru"},
        )
        body = ParsedBlock(
            block_type="text",
            text="茯苓具有广阔的应用前景。",
            order_index=2,
            page_no=1,
        )

        output, _ = reflow_blocks([heading_2, heading_3, body])

        # Body text should inherit "3 结语与展望"
        body_out = [b for b in output if "应用前景" in (b.text or "")][0]
        ctx = body_out.context_header or ""
        self.assertIn("3 结语与展望", ctx)


if __name__ == "__main__":
    unittest.main()
