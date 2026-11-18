"""规则保底 + Jev 兜底的意图解析。

设计要点：
- 强单信号走规则，零信号/多信号/过宽触发才调用 Jev；
- Jev 超时、失败、低置信、非法输出一律退回规则；
- 默认 ``INTENT_PROVIDER=rules``，不产生任何外部请求；
- ``shadow`` 只记录，不影响请求；
- 只发送问题文本，不发送知识库内容。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import unicodedata
from dataclasses import dataclass, replace
from typing import Any

from app.core.config import settings
from app.integrations.jev.prompts import PROMPT_VERSION
from app.services.retrieval.auto_router import RuleSignals, classify_rule_signals

logger = logging.getLogger(__name__)

# 只有真实模型结果（含缓存）才允许覆盖规则；回退结果仍然走规则。
_JEV_SOURCES = {"jev", "jev_cache"}


@dataclass(frozen=True)
class IntentDecision:
    intent: str
    source: str
    confidence: float | None = None
    margin: float | None = None
    matched: tuple[str, ...] = ()
    weak: bool = False
    short: bool = False
    latency_ms: float | None = None
    model: str | None = None
    fallback_reason: str | None = None
    model_calls: int = 0
    shadow: bool = False
    cache_hit: bool = False

    @property
    def use_hint(self) -> bool:
        return self.source in _JEV_SOURCES

    def meta(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "intent_source": self.source,
            "intent_confidence": self.confidence,
            "intent_margin": self.margin,
            "intent_signals": {
                "matched": list(self.matched),
                "weak": self.weak,
                "short": self.short,
            },
            "intent_latency_ms": self.latency_ms,
            "intent_model": self.model,
            "intent_fallback_reason": self.fallback_reason,
            "intent_model_calls": self.model_calls,
            "intent_shadow": self.shadow,
            "intent_cache_hit": self.cache_hit,
        }


class _Breaker:
    def __init__(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def available(self) -> bool:
        return time.monotonic() >= self.open_until

    def success(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def fail(self) -> None:
        self.failures += 1
        if self.failures >= max(1, settings.intent_jev_breaker_failures):
            self.open_until = time.monotonic() + max(
                1, settings.intent_jev_breaker_cooldown_s
            )
            self.failures = 0
            logger.warning(
                "intent_jev_breaker_open cooldown_s=%s",
                settings.intent_jev_breaker_cooldown_s,
            )


_breaker = _Breaker()
_client = None
_client_lock = asyncio.Lock()
_cache_client = None
_semaphore: asyncio.Semaphore | None = None
_shadow_queue: asyncio.Queue | None = None
_shadow_workers: list[asyncio.Task] = []
_shadow_lock = asyncio.Lock()
_shadow_worker_count = 2
_shadow_maxsize = 500


def normalize_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).casefold().split())


def query_hash(query: str) -> str:
    return hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()[:12]


def cache_key(query: str) -> str:
    digest = hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()
    return f"intent:jev:{PROMPT_VERSION}:{settings.intent_jev_model}:{digest}"


def kb_allowed(kb_id: str | None) -> bool:
    if not kb_id:
        return False
    allowed = {
        item.strip()
        for item in (settings.intent_jev_kb_ids or "").split(",")
        if item.strip()
    }
    return "*" in allowed or kb_id in allowed


def should_attempt(kb_id: str | None) -> bool:
    provider = (settings.intent_provider or "rules").lower()
    return provider in {"hybrid", "shadow"} and kb_allowed(kb_id)


def _rule_decision(signals: RuleSignals, *, source: str = "rules") -> IntentDecision:
    return IntentDecision(
        intent=signals.winner,
        source=source,
        matched=tuple(sorted(signals.matched)),
        weak=signals.weak,
        short=signals.short,
    )


def gate_to_jev(signals: RuleSignals, *, dual_signal_to_rules: bool | None = None) -> bool:
    """H2 门控；dual_signal_to_rules=True 时是 H4 变体。

    H2 只在“规则没有把握”时调用 Jev：零命中、多个意图同时命中、或命中了
    已知过宽触发词。强单信号（例如明确的“多少/对比/签约”）继续走规则，
    这样 P0 里规则 100% 判对的题不会被模型改写。
    """
    if signals.short:
        # 短查询没有足够语义，规则直接回退 general，调用模型不划算。
        return False
    if len(signals.matched) == 1 and not signals.weak:
        return False
    if dual_signal_to_rules is None:
        dual_signal_to_rules = bool(settings.intent_dual_signal_to_rules)
    if dual_signal_to_rules and signals.matched == frozenset({"relationship", "event"}):
        # H4：关系+事件同时出现时信任规则的 fusion 优先级。默认关闭，仅作影子对照。
        return False
    return True


async def _get_cache_client():
    global _cache_client
    if _cache_client is not None:
        return _cache_client
    try:
        import redis.asyncio as redis

        _cache_client = redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
    except Exception as exc:  # pragma: no cover - cache is optional
        logger.warning("intent_jev_cache_unavailable: %s", exc)
        _cache_client = None
    return _cache_client


async def _cache_get(key: str) -> dict | None:
    if settings.intent_jev_cache_ttl_s <= 0:
        return None
    client = await _get_cache_client()
    if client is None:
        return None
    try:
        raw = await client.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.debug("intent_jev_cache_read_failed: %s", exc)
        return None


async def _cache_set(key: str, value: dict) -> None:
    if settings.intent_jev_cache_ttl_s <= 0:
        return
    client = await _get_cache_client()
    if client is None:
        return
    try:
        await client.set(key, json.dumps(value, ensure_ascii=False), ex=settings.intent_jev_cache_ttl_s)
    except Exception as exc:
        logger.debug("intent_jev_cache_write_failed: %s", exc)


async def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, settings.intent_jev_concurrency))
    return _semaphore


async def _get_client():
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            from app.integrations.jev.client import JevIntentClient

            _client = JevIntentClient(
                api_key=settings.intent_jev_api_key,
                base_url=settings.intent_jev_base_url,
                model=settings.intent_jev_model,
                timeout_s=max(0.1, settings.intent_jev_timeout_ms / 1000),
            )
    return _client


def _fallback(
    signals: RuleSignals,
    reason: str,
    *,
    latency_ms: float | None = None,
    model: str | None = None,
    model_calls: int = 0,
) -> IntentDecision:
    # model_calls 只统计真正发出去的请求：缺 key/熔断是 0，超时或低置信是 1。
    return replace(
        _rule_decision(signals),
        source="rules_fallback",
        fallback_reason=reason,
        latency_ms=latency_ms,
        model=model,
        model_calls=model_calls,
    )


async def _resolve_with_jev(
    query: str,
    signals: RuleSignals,
    *,
    client=None,
    use_cache: bool,
) -> IntentDecision:
    if client is None and not settings.intent_jev_api_key:
        # 没有凭据时不要构造 SDK client，直接保留今天的规则结果。
        return _fallback(signals, "missing_api_key")

    # 先查缓存再进熔断：缓存命中的请求不应该被一次网络故障挡住。
    key = cache_key(query)
    if use_cache:
        cached = await _cache_get(key)
        if cached and cached.get("intent"):
            return IntentDecision(
                intent=cached["intent"],
                source="jev_cache",
                confidence=cached.get("confidence"),
                margin=cached.get("margin"),
                matched=tuple(sorted(signals.matched)),
                weak=signals.weak,
                short=signals.short,
                latency_ms=0.0,
                model=cached.get("model") or settings.intent_jev_model,
                cache_hit=True,
            )

    if not _breaker.available():
        # 熔断打开期间不再尝试远程调用，避免每个请求都等超时。
        return _fallback(signals, "breaker_open")

    semaphore = await _get_semaphore()
    try:
        async with semaphore:
            active_client = client or await _get_client()
            prediction = await active_client.classify(query)
    except Exception as exc:
        _breaker.fail()
        logger.warning(
            "intent_jev_failed hash=%s error=%s",
            query_hash(query),
            f"{type(exc).__name__}: {exc}"[:200],
        )
        return _fallback(signals, f"jev_error:{type(exc).__name__}", model_calls=1)

    _breaker.success()
    if (
        prediction.confidence < settings.intent_jev_min_confidence
        or prediction.margin < settings.intent_jev_min_margin
    ):
        return _fallback(
            signals,
            "low_confidence",
            latency_ms=prediction.latency_ms,
            model=prediction.model,
            model_calls=1,
        )

    if use_cache:
        await _cache_set(
            key,
            {
                "intent": prediction.intent,
                "confidence": prediction.confidence,
                "margin": prediction.margin,
                "model": prediction.model,
                "prompt_version": PROMPT_VERSION,
            },
        )
    return IntentDecision(
        intent=prediction.intent,
        source="jev",
        confidence=prediction.confidence,
        margin=prediction.margin,
        matched=tuple(sorted(signals.matched)),
        weak=signals.weak,
        short=signals.short,
        latency_ms=prediction.latency_ms,
        model=prediction.model,
        model_calls=1,
    )


def _log_decision(query: str, kb_id: str | None, decision: IntentDecision) -> None:
    logger.info(
        "intent_route kb=%s source=%s intent=%s matched=%s weak=%s short=%s "
        "confidence=%s latency_ms=%s model=%s fallback=%s shadow=%s hash=%s",
        kb_id,
        decision.source,
        decision.intent,
        ",".join(decision.matched) or "-",
        decision.weak,
        decision.short,
        f"{decision.confidence:.3f}" if decision.confidence is not None else "-",
        f"{decision.latency_ms:.1f}" if decision.latency_ms is not None else "-",
        decision.model or "-",
        decision.fallback_reason or "-",
        decision.shadow,
        query_hash(query),
    )


async def resolve_intent(
    query: str,
    kb_id: str | None,
    *,
    client=None,
    use_cache: bool = True,
) -> IntentDecision:
    signals = classify_rule_signals(query)
    rules = _rule_decision(signals)
    provider = (settings.intent_provider or "rules").lower()
    if provider not in {"hybrid", "shadow"} or not kb_allowed(kb_id):
        return rules

    if provider == "shadow":
        await _schedule_shadow(query, signals, rules, kb_id)
        decision = replace(rules, source="rules_shadow", shadow=True)
        _log_decision(query, kb_id, decision)
        return decision

    if not gate_to_jev(signals):
        _log_decision(query, kb_id, rules)
        return rules

    decision = await _resolve_with_jev(
        query, signals, client=client, use_cache=use_cache
    )
    _log_decision(query, kb_id, decision)
    return decision


# ── shadow mode: log-only, never on the request path ─────────────────────


async def _ensure_shadow_workers() -> asyncio.Queue:
    global _shadow_queue
    if _shadow_workers and _shadow_queue is not None:
        return _shadow_queue
    async with _shadow_lock:
        if not _shadow_workers:
            _shadow_queue = asyncio.Queue(maxsize=_shadow_maxsize)
            for index in range(_shadow_worker_count):
                _shadow_workers.append(
                    asyncio.create_task(_shadow_worker(_shadow_queue, index))
                )
    return _shadow_queue


async def _shadow_worker(queue: asyncio.Queue, index: int) -> None:
    while True:
        item = await queue.get()
        try:
            await _run_shadow(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - shadow must never break
            logger.warning("intent_shadow_failed worker=%s error=%s", index, exc)
        finally:
            queue.task_done()


async def _run_shadow(item: dict) -> None:
    query: str = item["query"]
    kb_id: str | None = item["kb_id"]
    signals: RuleSignals = item["signals"]
    rules: IntentDecision = item["rules"]
    if not settings.intent_jev_api_key:
        return
    try:
        client = await _get_client()
        prediction = await client.classify(query, timeout_s=5.0)
    except Exception as exc:
        logger.info(
            "intent_shadow_error hash=%s error=%s",
            query_hash(query),
            f"{type(exc).__name__}: {exc}"[:160],
        )
        return
    # 影子模式只调用一次模型，同时记录 H2 和 H4 两种门控结果，方便之后对比。
    h2 = prediction.intent if gate_to_jev(signals, dual_signal_to_rules=False) else rules.intent
    h4 = prediction.intent if gate_to_jev(signals, dual_signal_to_rules=True) else rules.intent
    logger.info(
        "intent_shadow kb=%s rule=%s jev=%s h2=%s h4=%s agree=%s confidence=%.3f "
        "latency_ms=%.1f matched=%s weak=%s hash=%s",
        kb_id,
        rules.intent,
        prediction.intent,
        h2,
        h4,
        rules.intent == prediction.intent,
        prediction.confidence,
        prediction.latency_ms,
        ",".join(sorted(signals.matched)) or "-",
        signals.weak,
        query_hash(query),
    )


async def _schedule_shadow(
    query: str,
    signals: RuleSignals,
    rules: IntentDecision,
    kb_id: str | None,
) -> None:
    rate = max(0.0, min(1.0, settings.intent_jev_shadow_rate))
    if rate <= 0 or random.random() > rate:
        return
    queue = await _ensure_shadow_workers()
    try:
        queue.put_nowait(
            {"query": query, "kb_id": kb_id, "signals": signals, "rules": rules}
        )
    except asyncio.QueueFull:
        logger.warning("intent_shadow_queue_full hash=%s", query_hash(query))


# ── lifecycle ────────────────────────────────────────────────────────────


async def warmup() -> None:
    """One background call so the first gated request does not pay cold start."""
    if (settings.intent_provider or "rules").lower() not in {"hybrid", "shadow"}:
        return
    if not settings.intent_jev_api_key:
        return
    try:
        client = await _get_client()
        # warmup 用比线上请求更长的超时，专门吃掉冷启动；失败也不影响启动。
        prediction = await client.classify(
            "学校在籍培养是什么意思？",
            timeout_s=max(20.0, settings.intent_jev_timeout_ms / 1000),
        )
        logger.info(
            "intent_jev_warmup_ok model=%s latency_ms=%.1f",
            prediction.model,
            prediction.latency_ms,
        )
    except Exception as exc:
        logger.info("intent_jev_warmup_failed: %s", f"{type(exc).__name__}: {exc}"[:160])


def schedule_warmup() -> None:
    if not settings.intent_jev_warmup:
        return
    if (settings.intent_provider or "rules").lower() not in {"hybrid", "shadow"}:
        return
    if not (settings.intent_jev_kb_ids or "").strip():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(warmup())


async def close() -> None:
    global _client, _cache_client, _shadow_queue
    for task in list(_shadow_workers):
        task.cancel()
    if _shadow_workers:
        await asyncio.gather(*_shadow_workers, return_exceptions=True)
    _shadow_workers.clear()
    _shadow_queue = None

    client, _client = _client, None
    if client is not None:
        await client.close()

    cache, _cache_client = _cache_client, None
    if cache is not None:
        try:
            await cache.aclose()
        except Exception:
            try:
                await cache.close()
            except Exception:
                pass


def reset_state_for_tests() -> None:
    """Clear module singletons between tests."""
    global _client, _cache_client, _semaphore, _shadow_queue, _breaker
    _client = None
    _cache_client = None
    _semaphore = None
    _shadow_queue = None
    _shadow_workers.clear()
    _breaker = _Breaker()