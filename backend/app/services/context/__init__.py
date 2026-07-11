"""Unified context service layer for Know_RAG.

This package is the stable application-service boundary used by:
- FastAPI /llm-context endpoints
- future MCP tools
- future RAG chat APIs

Adapters should call KnowledgeContextService instead of duplicating retrieval +
LLM context builder logic.
"""

from app.services.context.knowledge_context_service import (
    KnowledgeContextService,
    build_knowledge_context,
)

__all__ = ["KnowledgeContextService", "build_knowledge_context"]
