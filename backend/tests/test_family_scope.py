"""检索家族契约：A 关系/社区，B 事件；索引由家族推导。"""

import pytest
from pydantic import ValidationError

from app.schemas.retrieval_strategy import RetrievalProfile, family_scope_of, indexes_for_family
from app.services.knowledge_base_service import normalize_retrieval_profile


def test_indexes_are_derived_from_family():
    assert indexes_for_family("a") == ["entities", "relations", "communities"]
    assert indexes_for_family("b") == ["entities", "events"]


def test_family_a_overrides_client_events():
    profile = RetrievalProfile.model_validate(
        {"family_scope": "a", "build_indexes": ["entities", "events"]}
    )
    assert profile.build_indexes == ["entities", "relations", "communities"]


def test_family_b_overrides_client_relations():
    profile = RetrievalProfile.model_validate(
        {"family_scope": "b", "build_indexes": ["entities", "relations", "communities"]}
    )
    assert profile.build_indexes == ["entities", "events"]


def test_family_a_rejects_event_strategy():
    with pytest.raises(ValidationError):
        RetrievalProfile.model_validate(
            {"family_scope": "a", "strategy": {"family": "event_graph"}}
        )


def test_family_b_rejects_graph_strategy():
    with pytest.raises(ValidationError):
        RetrievalProfile.model_validate(
            {"family_scope": "b", "strategy": {"family": "tiered", "tier": "graph", "graph_method": "local"}}
        )


def test_family_a_fusion_turns_event_channel_off():
    profile = RetrievalProfile.model_validate(
        {"family_scope": "a", "strategy": {"family": "tiered", "tier": "fusion"}}
    )
    assert profile.strategy.fusion.event_policy == "off"
    assert profile.strategy.fusion.include_event_timeline is False


def test_normalize_requires_family():
    with pytest.raises(ValueError):
        normalize_retrieval_profile(None)
    with pytest.raises(ValueError):
        normalize_retrieval_profile({"version": "new_profile_v2"})


def test_normalize_derives_indexes_and_keeps_other_settings():
    profile = normalize_retrieval_profile(
        {
            "family_scope": "b",
            "build_indexes": ["entities", "relations", "communities"],
            "community": {"provider": "native", "max_communities": 3},
        }
    )
    assert profile["family_scope"] == "b"
    assert profile["build_indexes"] == ["entities", "events"]
    assert profile["community"]["max_communities"] == 3
    assert profile["strategy"]["family"] == "tiered"
    assert profile["strategy"]["tier"] == "auto"


def test_update_without_family_inherits_existing_scope():
    existing = {"version": "new_profile_v2", "family_scope": "a", "build_indexes": ["entities"]}
    profile = normalize_retrieval_profile({"community": {"max_communities": 2}}, existing)
    assert profile["family_scope"] == "a"
    assert profile["build_indexes"] == ["entities", "relations", "communities"]


def test_legacy_profile_without_family_remains_unscoped():
    profile = RetrievalProfile.model_validate(
        {"version": "legacy_v1", "build_indexes": ["entities", "relations", "events", "communities"]}
    )
    assert profile.family_scope is None
    assert family_scope_of(profile) is None
    assert profile.build_indexes == ["entities", "relations", "events", "communities"]

# --- 路由矩阵：家族作用域先于索引就绪度 ---

from app.schemas.retrieval_strategy import TieredStrategy
from app.services.retrieval.auto_router import route_auto
from app.services.retrieval.policy_resolver import resolve_policy


def route(query: str, scope: str, **capabilities):
    strategy = TieredStrategy(tier="auto")
    caps = {"family_scope": scope, **capabilities}
    plan, routing = route_auto(query, strategy, caps)
    return plan, routing


def test_family_a_event_degrades_to_hybrid():
    plan, routing = route("学校什么时候签约", "a", events=True)
    assert routing["intent"] == "event"
    assert plan.family == "tiered" and plan.tier == "hybrid"
    assert "未启用事件" in routing["reason"]


def test_family_b_relationship_degrades_to_hybrid():
    plan, routing = route("甲和乙的合作关系", "b", relations=True, entity_matches=2)
    assert routing["intent"] == "relationship"
    assert plan.tier == "hybrid"
    assert "未启用关系" in routing["reason"]


def test_family_b_event_uses_event_graph():
    plan, routing = route("学校什么时候签约", "b", events=True)
    assert routing["intent"] == "event"
    assert plan.family == "event_graph"


def test_family_a_relationship_uses_local():
    plan, routing = route("甲和乙的合作关系", "a", relations=True, entity_matches=2)
    assert plan.tier == "graph" and plan.graph_method == "local"


def test_family_b_overview_and_exploration_degrade():
    for query in ("全库主要主题", "深入探索甲校"):
        plan, routing = route(query, "b", **{"global": True}, relations=True)
        assert plan.tier == "hybrid"
        assert "未启用社区" in routing["reason"]


def test_family_a_overview_keeps_fusion_background():
    plan, routing = route("全库主要主题", "a", **{"global": True}, relations=True)
    assert plan.tier == "fusion"
    assert plan.fusion.include_global is True


def test_family_a_dual_intent_uses_relations_only():
    plan, routing = route("学校签约合作项目", "a", relations=True, events=True, entity_matches=2)
    assert routing["intent"] == "fusion"
    assert plan.tier == "graph" and plan.graph_method == "local"


def test_family_b_dual_intent_uses_event_graph():
    plan, routing = route("学校签约合作项目", "b", relations=True, events=True, entity_matches=2)
    assert routing["intent"] == "fusion"
    assert plan.family == "event_graph"


def test_policy_resolver_blocks_cross_family_explicit_request():
    profile = RetrievalProfile.model_validate({"family_scope": "b"}).model_dump()
    plan = resolve_policy(
        {"strategy": {"family": "tiered", "tier": "graph", "graph_method": "local"}},
        profile,
        {"relations": True, "family_scope": "b"},
    )
    assert plan.degraded is True
    assert plan.executed["tier"] == "hybrid"
    assert "未启用关系" in plan.reason


def test_policy_resolver_filters_events_for_family_a_fusion():
    profile = RetrievalProfile.model_validate({"family_scope": "a"}).model_dump()
    plan = resolve_policy(
        {"strategy": {"family": "tiered", "tier": "fusion"}},
        profile,
        {"relations": True, "events": True, "family_scope": "a"},
    )
    assert plan.payload["_association_kinds"] == ["relations"]
    assert plan.payload["_fusion_settings"]["event_policy"] == "off"


def test_family_cannot_change_after_creation(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.models import KnowledgeBase
    from app.services import knowledge_base_service as kb_service

    engine = create_engine("sqlite://")
    KnowledgeBase.__table__.create(engine)
    monkeypatch.setattr(kb_service, "record_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.knowledge_index.service.reconcile", lambda *args, **kwargs: None)
    with Session(engine) as db:
        created = kb_service.create_knowledge_base(
            db, None, {"name": "关系库", "retrieval_profile": {"family_scope": "a"}}
        )
        assert created["success"], created
        kb_id = created["data"]["id"]

        switched = kb_service.update_knowledge_base(
            db, None, kb_id, {"retrieval_profile": {"family_scope": "b"}}
        )
        assert not switched["success"]
        assert "不可更改" in switched["message"]
        assert db.get(KnowledgeBase, kb_id).retrieval_profile["family_scope"] == "a"

        renamed = kb_service.update_knowledge_base(db, None, kb_id, {"name": "新名字"})
        assert renamed["success"], renamed
        assert renamed["data"]["retrieval_profile"]["family_scope"] == "a"
