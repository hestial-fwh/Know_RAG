"""规则保底 + Jev 兜底的门控、回退和 hint 映射。"""

import pytest

from app.core.config import settings
from app.integrations.jev.client import JevPrediction, JevUnavailable
from app.schemas.retrieval_strategy import TieredStrategy
from app.services.retrieval import intent_resolver as resolver
from app.services.retrieval.auto_router import (
    classify_rule_signals,
    route_auto,
)


class FakeClient:
    def __init__(self, prediction=None, error: Exception | None = None):
        self.prediction = prediction
        self.error = error
        self.calls = 0

    async def classify(self, query: str):
        self.calls += 1
        if self.error:
            raise self.error
        return self.prediction


def prediction(intent="event", confidence=0.9, margin=0.7) -> JevPrediction:
    return JevPrediction(
        intent=intent,
        probabilities={intent: confidence},
        confidence=confidence,
        margin=margin,
        clear=0.8,
        model="typesafe/jev-1.14-20261014",
        latency_ms=12.0,
        input_tokens=100,
        output_tokens=10,
    )


@pytest.fixture(autouse=True)
def _reset_resolver_state():
    resolver.reset_state_for_tests()
    yield
    resolver.reset_state_for_tests()


def route(query: str, scope: str, **capabilities):
    plan, routing = route_auto(
        query, TieredStrategy(tier="auto"), {"family_scope": scope, **capabilities}
    )
    return plan, routing


def test_rule_signals_keep_all_matches():
    signals = classify_rule_signals("整体来看，P05和E02的合作关系如何？")
    assert signals.matched == frozenset({"overview", "relationship"})
    assert signals.winner == "overview"
    assert signals.weak is False

    signals = classify_rule_signals("P06是什么时候停止招生的？")
    assert signals.matched == frozenset({"definition"})
    assert signals.winner == "definition"
    assert signals.weak is True

    signals = classify_rule_signals("L02")
    assert signals.short is True
    assert signals.winner == "general"


def test_gate_h2_and_h4():
    rules = classify_rule_signals("学校一共有多少名教师？")
    assert resolver.gate_to_jev(rules) is False

    zero = classify_rule_signals("什么是订单班？")
    assert resolver.gate_to_jev(zero) is True

    multi = classify_rule_signals("P05与E02的合作关系在2025年签约后有哪些变化？")
    assert resolver.gate_to_jev(multi) is True

    weak = classify_rule_signals("T05的联系方式是什么？")
    assert resolver.gate_to_jev(weak) is True

    dual = classify_rule_signals("P05与E02的合作涉及哪些实验室和协议事件？")
    assert resolver.gate_to_jev(dual, dual_signal_to_rules=False) is True
    assert resolver.gate_to_jev(dual, dual_signal_to_rules=True) is False


def test_route_auto_uses_hint_relationship_over_event_keyword():
    plan, routing = route(
        "学校签约合作项目",
        "a",
        relations=True,
        entity_matches=2,
        intent_hint="relationship",
        intent_source="jev",
        intent_model_calls=1,
    )
    assert routing["intent"] == "relationship"
    assert plan.tier == "graph" and plan.graph_method == "local"
    assert routing["reason"].startswith("Jev 判定为 relationship；")
    assert routing["model_calls"] == 1
    assert routing["intent_source"] == "jev"


def test_route_auto_uses_hint_event_in_family_b():
    plan, routing = route(
        "学校签约合作项目",
        "b",
        events=True,
        intent_hint="event",
        intent_source="jev",
        intent_model_calls=1,
    )
    assert routing["intent"] == "event"
    assert plan.family == "event_graph"


def test_route_auto_hint_fusion_degrades_by_family():
    plan_a, routing_a = route(
        "学校签约合作项目", "a", relations=True, entity_matches=2,
        intent_hint="fusion", intent_source="jev", intent_model_calls=1,
    )
    plan_b, routing_b = route(
        "学校签约合作项目", "b", events=True,
        intent_hint="fusion", intent_source="jev", intent_model_calls=1,
    )
    assert routing_a["intent"] == "fusion" and plan_a.tier == "graph"
    assert plan_a.graph_method == "local"
    assert routing_b["intent"] == "fusion" and plan_b.family == "event_graph"


def test_route_auto_hint_general_and_no_hint_unchanged():
    plan, routing = route(
        "学校签约合作项目", "a", relations=True, entity_matches=2,
        intent_hint="general", intent_source="jev",
    )
    assert routing["intent"] == "general"
    assert plan.tier == "hybrid"

    # 无 hint 时仍然按双关键词判 fusion。
    plan, routing = route(
        "学校签约合作项目", "a", relations=True, events=True, entity_matches=2
    )
    assert routing["intent"] == "fusion"
    assert routing["intent_source"] == "rules"


def test_route_auto_without_hint_keeps_existing_degradation():
    plan, routing = route("学校什么时候签约", "a", events=True)
    assert routing["intent"] == "event"
    assert plan.tier == "hybrid"
    assert "未启用事件" in routing["reason"]

    plan, routing = route("甲和乙的合作关系", "b", relations=True, entity_matches=2)
    assert routing["intent"] == "relationship"
    assert plan.tier == "hybrid"
    assert "未启用关系" in routing["reason"]


async def test_resolve_intent_calls_jev_for_zero_signal(monkeypatch):
    monkeypatch.setattr(settings, "intent_provider", "hybrid")
    monkeypatch.setattr(settings, "intent_jev_kb_ids", "kb1")
    monkeypatch.setattr(settings, "intent_jev_api_key", "test-key")
    client = FakeClient(prediction("event", 0.9, 0.7))
    decision = await resolver.resolve_intent(
        "2026年6月20日学校公布了什么专业调整？", "kb1", client=client, use_cache=False
    )
    assert decision.intent == "event"
    assert decision.source == "jev"
    assert decision.use_hint is True
    assert decision.model_calls == 1
    assert client.calls == 1


async def test_resolve_intent_keeps_rules_for_strong_single(monkeypatch):
    monkeypatch.setattr(settings, "intent_provider", "hybrid")
    monkeypatch.setattr(settings, "intent_jev_kb_ids", "kb1")
    client = FakeClient(prediction("event"))
    decision = await resolver.resolve_intent(
        "学校一共有多少名教师？", "kb1", client=client, use_cache=False
    )
    assert decision.intent == "aggregation"
    assert decision.source == "rules"
    assert decision.use_hint is False
    assert client.calls == 0


async def test_resolve_intent_falls_back_on_error(monkeypatch):
    monkeypatch.setattr(settings, "intent_provider", "hybrid")
    monkeypatch.setattr(settings, "intent_jev_kb_ids", "kb1")
    client = FakeClient(error=JevUnavailable("timeout"))
    decision = await resolver.resolve_intent(
        "什么是订单班？", "kb1", client=client, use_cache=False
    )
    assert decision.intent == "general"
    assert decision.source == "rules_fallback"
    assert decision.fallback_reason.startswith("jev_error:")
    assert decision.model_calls == 1


async def test_resolve_intent_rejects_low_confidence(monkeypatch):
    monkeypatch.setattr(settings, "intent_provider", "hybrid")
    monkeypatch.setattr(settings, "intent_jev_kb_ids", "kb1")
    client = FakeClient(prediction("event", 0.2, 0.02))
    decision = await resolver.resolve_intent(
        "什么是订单班？", "kb1", client=client, use_cache=False
    )
    assert decision.source == "rules_fallback"
    assert decision.fallback_reason == "low_confidence"


async def test_resolve_intent_kb_not_allowed(monkeypatch):
    monkeypatch.setattr(settings, "intent_provider", "hybrid")
    monkeypatch.setattr(settings, "intent_jev_kb_ids", "other")
    client = FakeClient(prediction("event"))
    decision = await resolver.resolve_intent(
        "什么是订单班？", "kb1", client=client, use_cache=False
    )
    assert decision.source == "rules"
    assert client.calls == 0


async def test_resolve_intent_cache_hit(monkeypatch):
    monkeypatch.setattr(settings, "intent_provider", "hybrid")
    monkeypatch.setattr(settings, "intent_jev_kb_ids", "kb1")
    monkeypatch.setattr(settings, "intent_jev_api_key", "test-key")
    monkeypatch.setattr(settings, "intent_jev_cache_ttl_s", 60)

    async def fake_get(key):
        return {
            "intent": "event",
            "confidence": 0.88,
            "margin": 0.6,
            "model": "typesafe/jev-1.14-20261014",
        }

    monkeypatch.setattr(resolver, "_cache_get", fake_get)
    client = FakeClient(error=AssertionError("must not call"))
    decision = await resolver.resolve_intent(
        "什么是订单班？", "kb1", client=client, use_cache=True
    )
    assert decision.intent == "event"
    assert decision.source == "jev_cache"
    assert decision.cache_hit is True
    assert decision.model_calls == 0
    assert client.calls == 0


async def test_resolve_intent_breaker_opens(monkeypatch):
    monkeypatch.setattr(settings, "intent_provider", "hybrid")
    monkeypatch.setattr(settings, "intent_jev_kb_ids", "kb1")
    monkeypatch.setattr(settings, "intent_jev_api_key", "test-key")
    monkeypatch.setattr(settings, "intent_jev_breaker_failures", 2)
    monkeypatch.setattr(settings, "intent_jev_breaker_cooldown_s", 60)
    client = FakeClient(error=JevUnavailable("boom"))

    await resolver.resolve_intent("什么是订单班？", "kb1", client=client, use_cache=False)
    await resolver.resolve_intent("什么是订单班？", "kb1", client=client, use_cache=False)
    decision = await resolver.resolve_intent(
        "什么是订单班？", "kb1", client=client, use_cache=False
    )
    assert client.calls == 2
    assert decision.source == "rules_fallback"
    assert decision.fallback_reason == "breaker_open"


async def test_resolve_intent_shadow_never_hints(monkeypatch):
    monkeypatch.setattr(settings, "intent_provider", "shadow")
    monkeypatch.setattr(settings, "intent_jev_kb_ids", "kb1")
    monkeypatch.setattr(settings, "intent_jev_shadow_rate", 0.0)
    decision = await resolver.resolve_intent("什么是订单班？", "kb1")
    assert decision.source == "rules_shadow"
    assert decision.shadow is True
    assert decision.use_hint is False

async def test_execute_search_applies_intent_hint(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.models import KnowledgeBase
    from app.services.retrieval import orchestrator

    engine = create_engine("sqlite://")
    KnowledgeBase.__table__.create(engine)

    async def fake_search(db, actor, kb_id, payload):
        return {"success": True, "data": {"results": [], "meta": {}}}

    monkeypatch.setattr("app.services.retrieval_service._execute_chunk_search", fake_search)
    monkeypatch.setattr(settings, "intent_provider", "hybrid")

    async def fake_resolve(query, kb_id, **kwargs):
        return resolver.IntentDecision(
            intent="general",
            source="jev",
            confidence=0.91,
            margin=0.6,
            model_calls=1,
            model="typesafe/jev-1.14-20261014",
        )

    monkeypatch.setattr(resolver, "resolve_intent", fake_resolve)

    with Session(engine) as db:
        db.add(
            KnowledgeBase(
                id="kb1",
                name="测试库",
                retrieval_profile={
                    "version": "new_profile_v2",
                    "family_scope": "a",
                    "build_indexes": [],
                    "strategy": {"family": "tiered", "tier": "auto"},
                },
            )
        )
        db.commit()
        response = await orchestrator.execute_search(
            db, None, "kb1", {"query_text": "什么是订单班？"}
        )

    routing = response["data"]["meta"]["retrieval"]["routing"]
    assert routing["intent"] == "general"
    assert routing["intent_source"] == "jev"
    assert routing["model_calls"] == 1
