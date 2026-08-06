"""Unit tests for knowledge_context_service.py — _normalize_payload,
return mode dispatch, and KnowledgeContextService semantics."""

import pytest

from app.services.context.knowledge_context_service import KnowledgeContextService


class TestNormalizePayload:
    """Tests for KnowledgeContextService._normalize_payload."""

    def test_empty_payload_gets_defaults(self):
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload({})
        assert result["include_context"] is True
        assert result["include_parent_context"] is True
        assert result["merge_hits_by_parent"] is True
        assert result["return_mode"] == "chunks_with_selective_context"
        assert result["include_images"] is True
        assert result["include_image_urls"] is False

    def test_preserves_provided_values(self):
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload(
            {
                "return_mode": "chunks",
                "include_context": False,
                "include_image_urls": True,
            }
        )
        assert result["return_mode"] == "chunks"
        assert result["include_context"] is False
        assert result["include_image_urls"] is True

    def test_none_payload_gets_defaults(self):
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload(None)
        assert result["return_mode"] == "chunks_with_selective_context"
        assert result["include_context"] is True

    def test_query_text_populates_query(self):
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload({"query_text": "我的问题"})
        assert result["query_text"] == "我的问题"
        assert result["query"] == "我的问题"

    def test_query_populates_query_text(self):
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload({"query": "我的问题"})
        assert result["query"] == "我的问题"
        assert result["query_text"] == "我的问题"

    def test_query_and_query_text_both_present(self):
        """When both present, neither should overwrite the other."""
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload(
            {"query": "Q1", "query_text": "Q2"}
        )
        assert result["query"] == "Q1"
        assert result["query_text"] == "Q2"

    def test_default_return_mode_for_mcp(self):
        """MCP callers should get chunks_with_selective_context by default."""
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload({})
        assert result["return_mode"] == "chunks_with_selective_context"

    def test_debug_mode(self):
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload({"return_mode": "debug"})
        assert result["return_mode"] == "debug"

    def test_legacy_mode(self):
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload({"return_mode": "legacy"})
        assert result["return_mode"] == "legacy"

    def test_include_multimedia_defaults(self):
        service = KnowledgeContextService(db=None, actor=None)
        result = service._normalize_payload({})
        assert result["include_images"] is True
        assert result["include_image_urls"] is False
