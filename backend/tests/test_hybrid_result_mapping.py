import unittest
from types import SimpleNamespace

from app.services.retrieval_service import _row_to_result


class HybridResultMappingTests(unittest.TestCase):
    def row(self):
        return SimpleNamespace(
            chunk_id="chunk_1",
            document_id="doc_1",
            knowledge_id="doc_1",
            knowledge_base_id="kb_1",
            title="文档标题",
            file_name="demo.pdf",
            file_hash="sha256-demo",
            knowledge_source="file",
            knowledge_channel="local",
            parse_status="parsed",
            content="完整内容" * 100,
            matched_content="命中文本",
            chunk_index=1,
            start_at=0,
            end_at=512,
            pre_chunk_id="prev",
            next_chunk_id="next",
            chunk_type="text",
            parent_chunk_id="parent",
            context_header="第一章",
            content_hash="hash",
            image_info={},
            metadata_json={"seq": 1},
            vector_score=0.8,
        )

    def test_mapping_contains_weknora_fields(self):
        result = _row_to_result(self.row(), "vector", True, "vector_score")
        self.assertEqual(result["knowledge_id"], "doc_1")
        self.assertEqual(result["file_hash"], "sha256-demo")
        self.assertEqual(result["knowledge_title"], "文档标题")
        self.assertEqual(result["matched_content"], "命中文本")
        self.assertIn("rrf_score", result)
        self.assertNotIn("embedding", result)

    def test_include_content_false_omits_content(self):
        result = _row_to_result(self.row(), "vector", False, "vector_score")
        self.assertNotIn("content", result)
        self.assertIn("preview", result)


if __name__ == "__main__":
    unittest.main()
