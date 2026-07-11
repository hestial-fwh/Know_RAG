import unittest
from types import SimpleNamespace

from app.services.embedding_service import build_embedding_content


class EmbeddingStructureContentTests(unittest.TestCase):
    def test_directory_tree_embedding_is_semantic(self):
        chunk = SimpleNamespace(
            chunk_type="text",
            context_header="Know_RAG 后端服务层目录结构",
            content="""│ ├── embedding_service.py # 向量嵌入
│ ├── retrieval_service.py # 向量检索 + 重排序
""",
            metadata_json={"content_kind": "directory_tree"},
            flags={"structure_like": True},
            image_info={},
        )
        document = SimpleNamespace(title="Know_RAG 架构说明", file_name="arch.md")
        content = build_embedding_content(chunk, document)
        self.assertIn("Know_RAG 架构说明", content)
        self.assertIn("Know_RAG 后端服务层目录结构", content)
        self.assertIn("embedding_service.py：向量嵌入", content)
        self.assertIn("retrieval_service.py：向量检索 + 重排序", content)
        self.assertLess(content.count("│"), 2)


if __name__ == "__main__":
    unittest.main()
