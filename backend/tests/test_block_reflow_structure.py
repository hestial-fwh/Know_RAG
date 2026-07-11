import unittest

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


if __name__ == "__main__":
    unittest.main()
