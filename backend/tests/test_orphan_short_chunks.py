import unittest
from types import SimpleNamespace

from app.services.chunker.markdown_splitter import is_orphan_short_text_chunk
from app.services.chunker.models import ChunkerConfig
from app.services.chunker.service import split_document
from app.services.embedding_service import build_embedding_content


class OrphanShortChunkTests(unittest.TestCase):
    def test_short_lead_in_merges_into_fenced_code_block(self):
        text = """约 20 行，核心模块：

```python
def build_embedding_content(chunk, document):
    return chunk.content
```
"""
        chunks = split_document(text, "md", ChunkerConfig(chunk_size=36, chunk_overlap=0, strategy="recursive"))
        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        self.assertEqual(chunk.metadata.get("content_kind"), "code_block")
        self.assertEqual(chunk.metadata.get("lead_in_text"), "约 20 行，核心模块：")
        self.assertIn("def build_embedding_content", chunk.content)
        self.assertNotEqual(chunk.content.strip(), "约 20 行，核心模块：")

    def test_short_tail_note_merges_into_fenced_code_block(self):
        text = """```python
print("ok")
```

以上为核心逻辑。
"""
        chunks = split_document(text, "md", ChunkerConfig(chunk_size=28, chunk_overlap=0, strategy="recursive"))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].metadata.get("tail_note_text"), "以上为核心逻辑。")

    def test_heading_and_captions_are_not_orphan_short_chunks(self):
        self.assertFalse(is_orphan_short_text_chunk("第一章 绪论", {}))
        self.assertFalse(is_orphan_short_text_chunk("图 1 系统结构", {}))
        self.assertFalse(is_orphan_short_text_chunk("表 1 实验结果", {}))
        self.assertTrue(is_orphan_short_text_chunk("约 20 行，核心模块：", {}))

    def test_code_block_embedding_includes_lead_in(self):
        chunk = SimpleNamespace(
            chunk_type="text",
            context_header="核心服务",
            content='```python\nprint("ok")\n```',
            metadata_json={"content_kind": "code_block", "lead_in_text": "约 20 行，核心模块："},
            flags={},
            image_info={},
        )
        document = SimpleNamespace(title="Know_RAG 架构", file_name="arch.md")
        content = build_embedding_content(chunk, document)
        self.assertIn("Know_RAG 架构", content)
        self.assertIn("核心服务", content)
        self.assertIn("约 20 行，核心模块：", content)
        self.assertIn('print("ok")', content)

    def test_low_information_orphan_short_chunk_is_not_embedded(self):
        chunk = SimpleNamespace(
            chunk_type="text",
            context_header="",
            content="约 20 行，核心模块：",
            metadata_json={
                "orphan_short_chunk": True,
                "skip_embedding_recommended": True,
                "skip_reason": "orphan_short_chunk",
            },
            flags={"low_information": True},
            image_info={},
        )
        document = SimpleNamespace(title="Know_RAG", file_name="")
        self.assertEqual(build_embedding_content(chunk, document), "")


if __name__ == "__main__":
    unittest.main()
