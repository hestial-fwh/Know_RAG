"""Tests for MinerU OCR provider handling.

Verifies that:
- MinerU OCR test calls GET /health (not POST / or /ocr)
- /health returning {"status":"healthy"} passes
- Connection errors and unhealthy status raise clear messages
- Non-MinerU providers keep their original behavior
- list_available_models for MinerU returns without HTTP call
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class MinerUHealthCheckTests(unittest.TestCase):
    """Test _test_vision with provider=mineru and ocr=True."""

    def _make_cfg(self, base_url="http://host.docker.internal:8000"):
        return {
            "provider": "mineru",
            "model_name": "mineru",
            "base_url": base_url,
            "api_key": "",
        }

    @patch("app.services.model_config_service.httpx.AsyncClient")
    def test_mineru_health_check_success(self, mock_client_cls):
        """GET /health → 200 + {"status":"healthy"} → success."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "healthy"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.services.model_config_service import _test_vision

        result = _run(_test_vision(self._make_cfg(), ocr=True))

        mock_client.get.assert_called_once_with("http://host.docker.internal:8000/health")
        self.assertIn("healthy", result["response_preview"].lower())
        self.assertTrue(result.get("available"))

    @patch("app.services.model_config_service.httpx.AsyncClient")
    def test_mineru_health_unavailable_connection_error(self, mock_client_cls):
        """Connection refused → raises 'MinerU API unavailable'."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.services.model_config_service import _test_vision

        with self.assertRaises(RuntimeError) as ctx:
            _run(_test_vision(self._make_cfg(), ocr=True))
        self.assertIn("MinerU API unavailable", str(ctx.exception))

    @patch("app.services.model_config_service.httpx.AsyncClient")
    def test_mineru_health_unavailable_404(self, mock_client_cls):
        """GET /health → 404 → raises 'MinerU API unavailable'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.services.model_config_service import _test_vision

        with self.assertRaises(RuntimeError) as ctx:
            _run(_test_vision(self._make_cfg(), ocr=True))
        self.assertIn("MinerU API unavailable", str(ctx.exception))
        self.assertIn("404", str(ctx.exception))

    @patch("app.services.model_config_service.httpx.AsyncClient")
    def test_mineru_health_unhealthy(self, mock_client_cls):
        """GET /health → 200 + {"status":"starting"} → raises 'MinerU API unhealthy'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "starting"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.services.model_config_service import _test_vision

        with self.assertRaises(RuntimeError) as ctx:
            _run(_test_vision(self._make_cfg(), ocr=True))
        self.assertIn("MinerU API unhealthy", str(ctx.exception))

    @patch("app.services.model_config_service.httpx.AsyncClient")
    def test_mineru_health_check_does_not_hit_root(self, mock_client_cls):
        """Health check only calls /health, never the root / path."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "healthy"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.post = AsyncMock()  # should NOT be called
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.services.model_config_service import _test_vision

        _run(_test_vision(self._make_cfg(), ocr=True))

        # Verify only /health was called, not root
        mock_client.get.assert_called_once()
        call_url = mock_client.get.call_args[0][0]
        self.assertTrue(call_url.endswith("/health"), f"Expected /health, got {call_url}")
        mock_client.post.assert_not_called()

    @patch("app.services.model_config_service.httpx.AsyncClient")
    def test_mineru_strips_trailing_slash(self, mock_client_cls):
        """Base URL trailing slash is stripped before appending /health."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "healthy"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.services.model_config_service import _test_vision

        _run(_test_vision(self._make_cfg(base_url="http://host.docker.internal:8000/"), ocr=True))

        call_url = mock_client.get.call_args[0][0]
        self.assertEqual(call_url, "http://host.docker.internal:8000/health")


class MinerUListModelsTests(unittest.TestCase):
    """Test list_available_models with provider=mineru."""

    def test_mineru_returns_no_http_call(self):
        """list_available_models for mineru returns immediately without HTTP."""
        from app.services.model_config_service import list_available_models

        cfg = {
            "provider": "mineru",
            "model_name": "mineru",
            "base_url": "http://host.docker.internal:8000",
            "api_key": "",
        }

        with patch("app.services.model_config_service.httpx.AsyncClient") as mock_cls:
            result = _run(list_available_models(cfg, "ocr"))
            mock_cls.assert_not_called()

        self.assertEqual(result["provider"], "mineru")
        self.assertFalse(result["list_available"])
        self.assertEqual(result["models"], [])
        self.assertIn("暂无模型列表", result.get("message", ""))


class NonMinerUOCRTests(unittest.TestCase):
    """Verify non-MinerU OCR providers keep their original behavior."""

    @patch("app.services.vision_service.httpx.AsyncClient")
    def test_paddleocr_still_calls_ocr_endpoint(self, mock_client_cls):
        """PaddleOCR provider still POSTs to /ocr endpoint."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"text": "hello world"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.services.vision_service import recognize_image_with_config

        config = SimpleNamespace(
            provider="paddleocr",
            model_name="paddleocr",
            base_url="http://host.docker.internal:8868/ocr",
            api_key="",
        )
        # Minimal valid base64 PNG (1x1 white pixel)
        import base64
        from io import BytesIO
        from PIL import Image
        img = Image.new("RGB", (1, 1), "white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        result = _run(recognize_image_with_config(config, b64))

        # Verify POST was called (to /ocr endpoint)
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        self.assertIn("ocr", call_url.lower())
        self.assertEqual(result, "hello world")


if __name__ == "__main__":
    unittest.main()
