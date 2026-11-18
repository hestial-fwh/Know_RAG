"""TypeSafe Jev structured intent client.

The SDK is imported lazily so ``INTENT_PROVIDER=rules`` does not require the
optional cloud dependency at import time.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.integrations.jev.prompts import CRITERIA, INSTRUCTIONS, INTENTS


class JevUnavailable(RuntimeError):
    """The remote decision model cannot be used for this request."""


@dataclass(frozen=True)
class JevPrediction:
    intent: str
    probabilities: dict[str, float]
    confidence: float
    margin: float
    clear: float | None
    model: str
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None


class JevIntentClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_s: float = 1.5,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._timeout_s = timeout_s
        self._client = None
        self._client_lock = asyncio.Lock()

    async def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise JevUnavailable("missing API key")
        try:
            from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise JevUnavailable("typesafe-sdk is not installed") from exc

        async with self._client_lock:
            if self._client is not None:
                return self._client
            # 超时不重试：线上最多等 1.5 s 就回退规则；只对连接/5xx 做一次轻量重试。
            client = AsyncTypeSafeClient(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout_s,
                retry=RetryPolicy(
                    max_retries=1,
                    backoff_initial=0.2,
                    timeout=self._timeout_s,
                    api_timeout_error=False,
                ),
            )
            try:
                await client.__aenter__()
            except Exception as exc:
                raise JevUnavailable(f"cannot open TypeSafe client: {exc}") from exc
            self._client = client
            return client

    async def classify(self, query: str, *, timeout_s: float | None = None) -> JevPrediction:
        client = await self._ensure_client()
        request_timeout = timeout_s or self._timeout_s
        from typesafe_sdk import Choice, Noul

        questions = {
            "intent": Choice(
                criteria=CRITERIA,
                instructions=INSTRUCTIONS,
            ),
            "clear": Noul(
                instructions="Is the question clear enough to assign exactly one intent?",
                criteria={
                    "true": "One intent is clearly the best fit.",
                    "false": "The question is ambiguous or mixes intents.",
                },
            ),
        }
        started = time.perf_counter()
        # state 只包含问题文本；不发送知识库文档、实体或用户信息。
        try:
            response = await asyncio.wait_for(
                client.system_one(
                    {"query": query},
                    questions,
                    model=self._model,
                    timeout=request_timeout,
                ),
                timeout=request_timeout + 0.25,
            )
        except asyncio.TimeoutError as exc:
            raise JevUnavailable("timeout") from exc
        except Exception as exc:
            raise JevUnavailable(f"{type(exc).__name__}: {exc}") from exc

        answer = response.answers.get("intent")
        if answer is None or not answer.probabilities:
            raise JevUnavailable("missing intent probabilities")
        probabilities = {key: float(value) for key, value in answer.probabilities.items()}
        ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
        predicted = ranked[0][0]
        if predicted not in INTENTS:
            raise JevUnavailable(f"unknown intent: {predicted}")
        margin = ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0.0)
        clear_answer = response.answers.get("clear")
        usage = getattr(response, "usage", None)
        return JevPrediction(
            intent=predicted,
            probabilities=probabilities,
            confidence=ranked[0][1],
            margin=margin,
            clear=clear_answer.noul if clear_answer else None,
            model=getattr(response, "model", "") or self._model,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.__aexit__(None, None, None)
        except Exception:  # pragma: no cover - best-effort shutdown
            pass