import unittest

from app.services.chunker.models import ChunkerConfig
from app.services.chunker.service import split_document


STRUCTURE_TEXT = """# 项目结构

backend/
├── app/
│ ├── services/
│ │ ├── audit_service.py # 审计日志
│ │ ├── auth_service.py # 登录认证
│ │ ├── chunk_service.py # 分块管理
│ │ ├── embedding_service.py # 向量嵌入
│ │ ├── retrieval_service.py # 向量检索
│ │ └── vision_service.py # 视觉服务
"""


class ChunkerStructureTests(unittest.TestCase):
    def test_directory_tree_chunks_preserve_lines(self):
        chunks = split_document(
            STRUCTURE_TEXT,
            "md",
            ChunkerConfig(chunk_size=95, chunk_overlap=20, strategy="auto"),
        )
        self.assertGreaterEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertFalse(chunk.content.startswith("心)"))
            self.assertFalse(chunk.content.endswith("vision_serv"))
            self.assertFalse(chunk.content.endswith("embedding_serv"))
            first_line = chunk.content.splitlines()[0]
            last_line = chunk.content.splitlines()[-1]
            self.assertEqual(first_line, first_line.strip("\n\r"))
            self.assertEqual(last_line, last_line.strip("\n\r"))
            self.assertIn(chunk.metadata.get("content_kind"), {"directory_tree", "project_structure"})
            self.assertTrue(chunk.metadata.get("structure_like"))
            self.assertTrue(chunk.metadata.get("preserve_line_boundaries"))
            self.assertIn("项目结构", chunk.context_header)

    def test_plain_file_listing_is_structure_like(self):
        text = """app/services/chunk_service.py # 分块管理
app/services/embedding_service.py # 向量嵌入
app/services/retrieval_service.py # 向量检索
app/services/vision_service.py # 视觉服务
"""
        chunks = split_document(text, "txt", ChunkerConfig(chunk_size=80, chunk_overlap=10, strategy="recursive"))
        self.assertTrue(chunks)
        self.assertTrue(all(chunk.metadata.get("structure_like") for chunk in chunks))
        self.assertTrue(all(not chunk.content.endswith("_serv") for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
