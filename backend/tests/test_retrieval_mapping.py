import unittest
from types import SimpleNamespace

from app.services.retrieval_service import _row_to_result


class RetrievalMappingTests(unittest.TestCase):
    def make_row(self):
        return SimpleNamespace(
            chunk_id="chunk_1",
            document_id="doc_1",
            knowledge_id="doc_1",
            knowledge_base_id="kb_1",
            title="文档标题",
            file_name="demo.md",
            file_hash="sha256-demo",
            knowledge_source="file",
            knowledge_channel="local",
            parse_status="parsed",
            content="原始切片内容" * 20,
            matched_content="文档标题：文档标题\n\n正文：命中文本",
            chunk_index=3,
            start_at=10,
            end_at=100,
            pre_chunk_id=None,
            next_chunk_id=None,
            chunk_type="text",
            parent_chunk_id=None,
            context_header="第一章",
            content_hash="hash",
            image_info={},
            metadata_json={"heading_path": ["第一章"]},
            vector_score=0.8,
            keyword_score=0.6,
        )

    def test_result_contains_weknora_compatible_fields(self):
        result = _row_to_result(self.make_row(), "vector", True, "vector_score")
        self.assertEqual(result["knowledge_id"], "doc_1")
        self.assertEqual(result["file_hash"], "sha256-demo")
        self.assertEqual(result["knowledge_title"], "文档标题")
        self.assertIn("matched_content", result)
        self.assertIn("metadata_json", result)
        self.assertNotIn("embedding", result)

    def test_include_content_false_omits_full_content(self):
        result = _row_to_result(self.make_row(), "keyword", False, "keyword_score")
        self.assertNotIn("content", result)
        self.assertIn("preview", result)
        self.assertIn("matched_content", result)


if __name__ == "__main__":
    unittest.main()
