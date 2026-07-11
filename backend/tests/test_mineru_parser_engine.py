"""Tests for parser engine dispatch and MinerU adapter integration.

These tests verify:
- Parser engine resolution (auto / mineru / deepdoc / ragflow)
- MinerU format support checking
- MinerU health check
- Fallback metadata recording
- Non-MinerU providers unchanged
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.services.document_parser.mineru_adapter import (
    MinerUPdfConfig,
    is_mineru_supported_format,
    get_mineru_pdf_config,
)
from app.services.parser_service import resolve_parser_engine


class TestResolveParserEngine(unittest.TestCase):
    """Test resolve_parser_engine() dispatch logic."""

    def _mock_settings(self, *, engine: str, mineru_url: str = "", extensions: str = ""):
        return {
            "document_parser_engine": engine,
            "mineru_base_url": mineru_url,
            "mineru_supported_extensions": extensions or "pdf,png,jpg,jpeg,webp,doc,docx,ppt,pptx,xls,xlsx,html,txt,md,json,epub",
        }

    @patch("app.services.parser_service.getattr")
    @patch("app.services.parser_service.get_mineru_pdf_config")
    def test_engine_mineru_with_supported_format(self, mock_get_config, mock_getattr):
        """DOCUMENT_PARSER_ENGINE=mineru + supported format → mineru"""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "document_parser_engine": "mineru",
        }.get(name, default)
        mock_get_config.return_value = MinerUPdfConfig(base_url="http://mineru:8000")

        with patch("app.services.parser_service.is_mineru_supported_format", return_value=True):
            result = resolve_parser_engine("pdf")
        self.assertEqual(result, "mineru")

    @patch("app.services.parser_service.getattr")
    @patch("app.services.parser_service.get_mineru_pdf_config")
    def test_engine_mineru_unsupported_format(self, mock_get_config, mock_getattr):
        """DOCUMENT_PARSER_ENGINE=mineru + unsupported format → ragflow"""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "document_parser_engine": "mineru",
        }.get(name, default)
        mock_get_config.return_value = MinerUPdfConfig(base_url="http://mineru:8000")

        with patch("app.services.parser_service.is_mineru_supported_format", return_value=False):
            result = resolve_parser_engine("exe")
        self.assertEqual(result, "ragflow")

    @patch("app.services.parser_service.getattr")
    @patch("app.services.parser_service.get_mineru_pdf_config")
    def test_engine_auto_with_mineru_configured(self, mock_get_config, mock_getattr):
        """DOCUMENT_PARSER_ENGINE=auto + mineru configured + supported → mineru"""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "document_parser_engine": "auto",
        }.get(name, default)
        mock_get_config.return_value = MinerUPdfConfig(base_url="http://mineru:8000")

        with patch("app.services.parser_service.is_mineru_supported_format", return_value=True):
            result = resolve_parser_engine("pdf")
        self.assertEqual(result, "mineru")

    @patch("app.services.parser_service.getattr")
    @patch("app.services.parser_service.get_mineru_pdf_config")
    def test_engine_auto_without_mineru(self, mock_get_config, mock_getattr):
        """DOCUMENT_PARSER_ENGINE=auto + mineru not configured → ragflow"""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "document_parser_engine": "auto",
        }.get(name, default)
        mock_get_config.return_value = None

        result = resolve_parser_engine("pdf")
        self.assertEqual(result, "ragflow")

    @patch("app.services.parser_service.getattr")
    @patch("app.services.parser_service.get_mineru_pdf_config")
    def test_engine_ragflow(self, mock_get_config, mock_getattr):
        """DOCUMENT_PARSER_ENGINE=ragflow → ragflow regardless of MinerU"""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "document_parser_engine": "ragflow",
        }.get(name, default)
        mock_get_config.return_value = MinerUPdfConfig(base_url="http://mineru:8000")

        with patch("app.services.parser_service.is_mineru_supported_format", return_value=True):
            result = resolve_parser_engine("pdf")
        self.assertEqual(result, "ragflow")

    @patch("app.services.parser_service.getattr")
    @patch("app.services.parser_service.get_mineru_pdf_config")
    def test_engine_deepdoc(self, mock_get_config, mock_getattr):
        """DOCUMENT_PARSER_ENGINE=deepdoc → ragflow (alias)"""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "document_parser_engine": "deepdoc",
        }.get(name, default)
        mock_get_config.return_value = MinerUPdfConfig(base_url="http://mineru:8000")

        with patch("app.services.parser_service.is_mineru_supported_format", return_value=True):
            result = resolve_parser_engine("pdf")
        self.assertEqual(result, "ragflow")


class TestMinerUSupportedFormat(unittest.TestCase):
    """Test is_mineru_supported_format() with various configs."""

    @patch("app.services.document_parser.mineru_adapter.getattr")
    def test_default_extensions(self, mock_getattr):
        """Default supported extensions include pdf, docx, pptx, xlsx, images."""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "mineru_supported_extensions": "pdf,png,jpg,jpeg,webp,doc,docx,ppt,pptx,xls,xlsx,html,txt,md,json,epub",
        }.get(name, default)

        self.assertTrue(is_mineru_supported_format("pdf"))
        self.assertTrue(is_mineru_supported_format("docx"))
        self.assertTrue(is_mineru_supported_format("pptx"))
        self.assertTrue(is_mineru_supported_format("xlsx"))
        self.assertTrue(is_mineru_supported_format("png"))
        self.assertTrue(is_mineru_supported_format("jpg"))
        self.assertTrue(is_mineru_supported_format("html"))
        self.assertTrue(is_mineru_supported_format("epub"))

    @patch("app.services.document_parser.mineru_adapter.getattr")
    def test_unsupported_format(self, mock_getattr):
        """Unsupported formats return False."""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "mineru_supported_extensions": "pdf,png,docx",
        }.get(name, default)

        self.assertFalse(is_mineru_supported_format("exe"))
        self.assertFalse(is_mineru_supported_format("zip"))
        self.assertFalse(is_mineru_supported_format("csv"))

    @patch("app.services.document_parser.mineru_adapter.getattr")
    def test_empty_config(self, mock_getattr):
        """Empty MINERU_SUPPORTED_EXTENSIONS returns False."""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "mineru_supported_extensions": "",
        }.get(name, default)

        self.assertFalse(is_mineru_supported_format("pdf"))


class TestMinerUHealthCheck(unittest.TestCase):
    """Test MinerU health check logic (model_config_service._test_vision)."""

    @patch("app.services.model_config_service.httpx.AsyncClient")
    async def test_mineru_health_check_success(self, mock_client_class):
        """GET /health returns 200 + healthy → success."""
        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "healthy"}
        mock_client.get.return_value = mock_resp
        mock_client_class.return_value = mock_client

        from app.services.model_config_service import _test_vision
        cfg = {"provider": "mineru", "model_name": "mineru", "base_url": "http://mineru:8000"}
        result = await _test_vision(cfg, ocr=True)
        self.assertTrue(result.get("available"))
        self.assertIn("MinerU API healthy", result.get("response_preview", ""))

    @patch("app.services.model_config_service.httpx.AsyncClient")
    async def test_mineru_health_check_connection_error(self, mock_client_class):
        """Connection error → raises RuntimeError."""
        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client_class.return_value = mock_client

        from app.services.model_config_service import _test_vision
        cfg = {"provider": "mineru", "model_name": "mineru", "base_url": "http://mineru:8000"}
        with self.assertRaises(RuntimeError) as ctx:
            await _test_vision(cfg, ocr=True)
        self.assertIn("MinerU API unavailable", str(ctx.exception))

    @patch("app.services.model_config_service.httpx.AsyncClient")
    async def test_mineru_health_check_unhealthy(self, mock_client_class):
        """GET /health returns 200 but status != healthy → raises."""
        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "starting"}
        mock_client.get.return_value = mock_resp
        mock_client_class.return_value = mock_client

        from app.services.model_config_service import _test_vision
        cfg = {"provider": "mineru", "model_name": "mineru", "base_url": "http://mineru:8000"}
        with self.assertRaises(RuntimeError) as ctx:
            await _test_vision(cfg, ocr=True)
        self.assertIn("MinerU API unhealthy", str(ctx.exception))

    @patch("app.services.model_config_service.httpx.AsyncClient")
    async def test_mineru_health_check_404(self, mock_client_class):
        """GET /health returns 404 → raises unavailable."""
        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client.get.return_value = mock_resp
        mock_client_class.return_value = mock_client

        from app.services.model_config_service import _test_vision
        cfg = {"provider": "mineru", "model_name": "mineru", "base_url": "http://mineru:8000"}
        with self.assertRaises(RuntimeError) as ctx:
            await _test_vision(cfg, ocr=True)
        self.assertIn("MinerU API unavailable", str(ctx.exception))

    async def test_mineru_health_missing_base_url(self):
        """Empty base_url → raises ValueError."""
        from app.services.model_config_service import _test_vision
        cfg = {"provider": "mineru", "model_name": "mineru", "base_url": ""}
        with self.assertRaises(ValueError) as ctx:
            await _test_vision(cfg, ocr=True)
        self.assertIn("Base URL 不能为空", str(ctx.exception))


class TestMinerUListModels(unittest.TestCase):
    """Test list_available_models() for MinerU provider."""

    async def test_mineru_list_models_no_http(self):
        """MinerU returns no models list, makes no HTTP call."""
        from app.services.model_config_service import list_available_models
        cfg = {"provider": "mineru", "model_name": "mineru", "base_url": "http://mineru:8000"}
        result = await list_available_models(cfg, "ocr")
        self.assertFalse(result.get("list_available"))
        self.assertEqual(result.get("provider"), "mineru")
        self.assertIn("暂无模型列表接口", result.get("message", ""))


class TestFallbackMetadata(unittest.TestCase):
    """Test that fallback metadata is correctly recorded."""

    @patch("app.services.parser_service.getattr")
    def test_fallback_metadata_structure(self, mock_getattr):
        """Verify fallback metadata fields are present."""
        mock_getattr.side_effect = lambda obj, name, default=None: {
            "document_parser_engine": "auto",
            "mineru_fallback_to_deepdoc": True,
            "mineru_supported_extensions": "pdf,png,docx",
        }.get(name, default)

        # Test that _fallback_to_ragflow produces correct metadata
        from app.services.parser_service import _fallback_to_ragflow
        from pathlib import Path

        # Create a minimal mock document
        doc = type("D", (), {})()
        doc.knowledge_base_id = "test-kb"
        doc.id = "test-doc"
        doc.storage_path = "/tmp/test.pdf"
        doc.file_type = "pdf"

        with patch("app.services.parser_service._parse_with_ragflow") as mock_parse:
            from app.services.document_parser.types import ParsedDocument, ParsedBlock
            mock_parse.return_value = ParsedDocument(
                content="test content",
                blocks=[ParsedBlock(block_type="text", text="test", order_index=0)],
                metadata={"parser": "ragflow"},
            )

            result = _fallback_to_ragflow(
                Path("/tmp/test.pdf"), doc, "pdf",
                requested="mineru",
                reason="MinerU returned 500",
                mineru_base_url="http://mineru:8000",
            )

            self.assertEqual(result.metadata.get("parser_requested"), "mineru")
            self.assertEqual(result.metadata.get("parser_actual"), "ragflow")
            self.assertTrue(result.metadata.get("fallback_used"))
            self.assertEqual(result.metadata.get("fallback_reason"), "MinerU returned 500")
            self.assertEqual(result.metadata.get("parser_attempted"), "mineru")
            self.assertFalse(result.metadata.get("mineru_pdf_enabled"))
            self.assertEqual(result.metadata.get("mineru_base_url"), "http://mineru:8000")


if __name__ == "__main__":
    unittest.main()