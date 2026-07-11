"""HTTP client used by the MCP stdio server.

The MCP process is deliberately decoupled from Know_RAG internals.  It talks to
Know_RAG through the existing HTTP API, so it can run:
- on the host machine;
- inside the backend container;
- as a standalone container later.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class KnowRagMcpClientError(RuntimeError):
    """Raised when the Know_RAG HTTP API returns an error."""


class KnowRagHttpClient:
    """Small async client for the Know_RAG API.

    Auth strategy:
    1. Use KNOW_RAG_API_TOKEN if provided.
    2. Otherwise login with KNOW_RAG_USERNAME / KNOW_RAG_PASSWORD.
    3. Retry once on 401 after refreshing the token.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("KNOW_RAG_API_BASE") or "http://127.0.0.1:8000").rstrip("/")
        self.token = token or os.getenv("KNOW_RAG_API_TOKEN") or ""
        self.username = username or os.getenv("KNOW_RAG_USERNAME") or os.getenv("ADMIN_USERNAME") or "admin"
        self.password = password or os.getenv("KNOW_RAG_PASSWORD") or os.getenv("ADMIN_PASSWORD") or "admin123"
        self.timeout_seconds = float(timeout_seconds or os.getenv("KNOW_RAG_API_TIMEOUT_SECONDS") or 60)
        self._client = httpx.AsyncClient(timeout=self.timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def login(self) -> str:
        response = await self._client.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": self.username, "password": self.password},
        )
        data = self._decode_response(response)
        token = ((data.get("data") or {}).get("access_token") or "").strip()
        if not token:
            raise KnowRagMcpClientError("登录成功但响应中没有 access_token")
        self.token = token
        return token

    async def list_knowledge_bases(self, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v1/knowledge-bases",
            params={"page": page, "page_size": page_size},
        )

    async def build_llm_context(self, *, knowledge_base_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/knowledge-bases/{knowledge_base_id}/llm-context",
            json=payload,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.token:
            await self.login()

        response = await self._client.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(),
            **kwargs,
        )
        if response.status_code == 401:
            await self.login()
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                **kwargs,
            )
        return self._decode_response(response)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _decode_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except Exception as exc:
            raise KnowRagMcpClientError(
                f"Know_RAG API 返回非 JSON 响应：HTTP {response.status_code} {response.text[:300]}"
            ) from exc

        if response.status_code >= 400:
            message = data.get("message") if isinstance(data, dict) else None
            raise KnowRagMcpClientError(f"Know_RAG API 调用失败：HTTP {response.status_code} {message or data}")

        if isinstance(data, dict) and data.get("success") is False:
            raise KnowRagMcpClientError(f"Know_RAG API 调用失败：{data.get('message') or data}")
        return data


_default_client: KnowRagHttpClient | None = None


def get_default_client() -> KnowRagHttpClient:
    global _default_client
    if _default_client is None:
        _default_client = KnowRagHttpClient()
    return _default_client
