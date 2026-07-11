import unittest
from types import SimpleNamespace

from app.services.chunk_service import (
    _compose_code_block_content,
    _merge_short_text_around_code_blocks,
    is_orphan_short_text,
)
from app.services.document_parser.types import ParsedBlock
from app.services.embedding_service import build_embedding_content


def text_block(text: str, order: int = 0) -> ParsedBlock:
    return ParsedBlock(block_type="text", text=text, order_index=order, context_header="后端服务")


def code_block(code: str, order: int = 0) -> ParsedBlock:
    return ParsedBlock(
        block_type="text",
        text=code,
        order_index=order,
        context_header="后端服务",
        metadata={"raw_block_type": "code_block", "content_kind": "code_block"},
    )


class MarkdownCodeContextMergeTests(unittest.TestCase):
    def test_short_lead_in_merges_into_code_block(self):
        blocks = [
            text_block("约 930 行，本地 ONNX 引擎的完整 PDF 解析管道：", 0),
            code_block("```python\nparse_document_with_mineru(...)\n```", 1),
        ]
        merged = _merge_short_text_around_code_blocks(blocks)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].metadata.get("lead_in_text"), "约 930 行，本地 ONNX 引擎的完整 PDF 解析管道：")
        content = _compose_code_block_content(merged[0])
        self.assertIn("完整 PDF 解析管道", content)
        self.assertIn("parse_document_with_mineru", content)
        self.assertTrue(merged[0].metadata.get("has_lead_in"))

    def test_short_tail_note_merges_into_previous_code_block(self):
        blocks = [
            code_block("```python\nreturn vectors\n```", 0),
            text_block("返回 [vector1, vector2, ...]", 1),
        ]
        merged = _merge_short_text_around_code_blocks(blocks)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].metadata.get("tail_note_text"), "返回 [vector1, vector2, ...]")
        self.assertTrue(merged[0].metadata.get("has_tail_note"))

    def test_multiple_service_notes_merge_to_following_code_blocks(self):
        blocks = [
            text_block("管理向量嵌入的生成和存储，采用批量模式提升效率。", 0),
            code_block("```python\nembed_document(...)\n```", 1),
            text_block("约 614 行，实现向量检索 + Rerank 重排序流水线。", 2),
            code_block("```python\nhybrid_search(...)\n```", 3),
        ]
        merged = _merge_short_text_around_code_blocks(blocks)
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(block.metadata.get("has_lead_in") for block in merged))
        self.assertIn("管理向量嵌入", merged[0].metadata.get("lead_in_text"))
        self.assertIn("Rerank", merged[1].metadata.get("lead_in_text"))
        self.assertFalse(any(is_orphan_short_text(block.text, block.metadata) and not block.metadata.get("content_kind") == "code_block" for block in merged))

    def test_heading_and_captions_are_not_swallowed(self):
        blocks = [
            text_block("1.1 研究背景", 0),
            text_block("图 1 系统架构", 1),
            text_block("表 1 实验结果", 2),
        ]
        merged = _merge_short_text_around_code_blocks(blocks)
        self.assertEqual(len(merged), 3)
        self.assertEqual([block.text for block in merged], ["1.1 研究背景", "图 1 系统架构", "表 1 实验结果"])

    def test_code_block_embedding_contains_lead_in_and_function_name(self):
        chunk = SimpleNamespace(
            chunk_type="text",
            context_header="后端服务",
            content="约 930 行，本地 ONNX 引擎的完整 PDF 解析管道：\n\n```python\nparse_document_with_mineru(...)\n```",
            metadata_json={
                "content_kind": "code_block",
                "has_lead_in": True,
                "lead_in_text": "约 930 行，本地 ONNX 引擎的完整 PDF 解析管道：",
            },
            flags={},
            image_info={},
        )
        document = SimpleNamespace(title="Know_RAG Markdown 解析", file_name="arch.md")
        content = build_embedding_content(chunk, document)
        self.assertIn("Know_RAG Markdown 解析", content)
        self.assertIn("后端服务", content)
        self.assertIn("完整 PDF 解析管道", content)
        self.assertIn("parse_document_with_mineru", content)


if __name__ == "__main__":
    unittest.main()
