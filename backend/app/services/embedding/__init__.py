"""Embedding generation subpackage.

Modules:
- embedding_content_builder: Build embedding text from chunks (pure, no DB)
"""

from app.services.embedding.embedding_content_builder import build_embedding_content

__all__ = ["build_embedding_content"]
