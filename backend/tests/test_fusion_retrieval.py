"""融合检索：结构通道组合、事件独立政策、保留位、诊断与降级。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_retrieval_strategy import search_backend, strategy
from test_association_retrieval import foundation, indexed  # noqa: F401  (pytest fixtures)
from app.schemas.retrieval_strategy import AssociationBudget, FusionSettings
from app.services import retrieval_service as service
from app.services.retrieval import associations


def path(chunk_id, hop=1):
    return {"support_chunk_ids": [chunk_id], "steps": [], "hop": hop}


def relation_candidate(chunk_id="r"):
    return {
        "chunk_id": chunk_id, "id": chunk_id, "score": 0.1,
        "retrieval_sources": ["relation"], "association_paths": [path(chunk_id)],
    }


def event_candidate(chunk_id="e", relevance=0.9, direct_hit=False, specificity=0.8):
    event_path = {
        "support_chunk_ids": [chunk_id], "steps": [], "hop": 1,
        "event_ids": ["ev"], "event_relevance": relevance,
        "specificity_norm": specificity, "direct_hit": direct_hit,
    }
    return {
        "chunk_id": chunk_id, "id": chunk_id, "score": 0.1,
        "retrieval_sources": ["event"], "event_ids": ["ev"],
        "event_relevance": relevance, "specificity_norm": specificity,
        "direct_hit": direct_hit, "association_paths": [event_path],
    }


def channel_meta(kind, paths):
    event = kind == "events"
    return {
        "provider": "test", "kind": kind, "seed_count": 1,
        "candidate_count": len(paths), "budget": {}, "budget_truncated": False,
        "coverage": {"ready": 1, "partial": 0, "total": 1},
        "paths": paths, "coverage_incomplete": False, "timeout": False,
        "source_changed_during_query": False, "aggregation_supported": False,
        "elapsed_ms": 1.0, "events_scored": len(paths) if event else 0,
        "direct_overlap_count": 0, "top_event_relevance": 0.9 if event else 0.0,
    }


def structure_stub(relation=(), event=(), called=None):
    def run(reader, query, direct, kinds, budget, include_content, event_threshold=0.0):
        if called is not None:
            called.append({"kinds": list(kinds), "threshold": event_threshold})
        batches, metas = {}, {}
        if "relations" in kinds:
            batches["relations"] = list(relation)
            metas["relations"] = channel_meta("relations", [path("r")] if relation else [])
        if "events" in kinds:
            batches["events"] = list(event)
            metas["events"] = channel_meta(
                "events", [path(item["chunk_id"]) for item in event],
            )
        return batches, metas, {}
    return run


def fusion_payload(**overrides):
    payload = {
        "query": "甲校有哪些实践资源",
        "strategy": strategy("fusion"),
        "_association_kinds": ["relations"],
        "_association_budget": AssociationBudget().model_dump(),
        "_fusion_settings": FusionSettings().model_dump(),
        "_association_reader": object(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_fusion_executes_relation_channel_and_reports_diagnostics(search_backend, monkeypatch):
    called = []
    monkeypatch.setattr(
        associations, "search_structure_channels",
        structure_stub(relation=[relation_candidate()], called=called),
    )
    response = await service._execute_chunk_search(search_backend.db, None, "kb", fusion_payload())
    assert response["success"], response
    meta = response["data"]["meta"]
    fusion = meta["_fusion"]
    assert fusion["mode"] == {"rank_mode": "rrf", "event_policy": "independent"}
    assert fusion["executed"] == ["vector", "keyword", "relation"]
    assert fusion["channel_status"]["relation"] == "ready"
    assert fusion["channel_status"]["event"] == "skipped_not_requested"
    assert fusion["candidate_counts"]["relation"] == 1
    assert fusion["structure_paths"]["relation"] == 1
    assert meta["_executed_channels"] == ["vector", "keyword", "relation"]
    assert meta["_association"]["kind"] == "fusion"
    assert called[0]["kinds"] == ["relations"]


@pytest.mark.asyncio
async def test_event_policy_off_skips_event_executor(search_backend, monkeypatch):
    called = []
    settings = FusionSettings(event_policy="off").model_dump()
    monkeypatch.setattr(
        associations, "search_structure_channels",
        structure_stub(relation=[relation_candidate()], called=called),
    )
    response = await service._execute_chunk_search(
        search_backend.db, None, "kb",
        fusion_payload(
            _association_kinds=["relations", "events"], _fusion_settings=settings,
        ),
    )
    assert response["success"], response
    assert called[0]["kinds"] == ["relations"]
    fusion = response["data"]["meta"]["_fusion"]
    assert fusion["channel_status"]["event"] == "skipped_policy_off"
    assert "event" not in fusion["executed"]


@pytest.mark.asyncio
async def test_event_policy_independent_reserves_slot_for_event_only_evidence(search_backend, monkeypatch):
    settings = FusionSettings(event_policy="independent", event_reserved_slots=1).model_dump()
    monkeypatch.setattr(
        associations, "search_structure_channels",
        structure_stub(relation=[], event=[event_candidate("e", 0.9)]),
    )
    response = await service._execute_chunk_search(
        search_backend.db, None, "kb",
        fusion_payload(
            top_k=2, _association_kinds=["relations", "events"], _fusion_settings=settings,
        ),
    )
    assert response["success"], response
    chunk_ids = [item["chunk_id"] for item in response["data"]["results"]]
    # 基础候选占满 top_k 时，事件独有证据仍然通过保留位进入。
    assert "e" in chunk_ids
    fusion = response["data"]["meta"]["_fusion"]
    assert fusion["event"]["reserved_slots_used"] == 1
    assert fusion["candidate_counts"]["event_only"] == 1
    assert fusion["channel_status"]["event"] == "ready"
    assert "event" in fusion["executed"]


@pytest.mark.asyncio
async def test_event_policy_fused_merges_event_into_main_ranking(search_backend, monkeypatch):
    settings = FusionSettings(event_policy="fused").model_dump()
    monkeypatch.setattr(
        associations, "search_structure_channels",
        structure_stub(relation=[], event=[event_candidate("e", 0.9)]),
    )
    response = await service._execute_chunk_search(
        search_backend.db, None, "kb",
        fusion_payload(top_k=3, _association_kinds=["relations", "events"], _fusion_settings=settings),
    )
    assert response["success"], response
    chunk_ids = [item["chunk_id"] for item in response["data"]["results"]]
    assert "e" in chunk_ids
    assert response["data"]["meta"]["_fusion"]["event"]["reserved_slots_used"] == 0


@pytest.mark.asyncio
async def test_fusion_evidence_chain_survives_selection(search_backend, monkeypatch):
    chain = {"support_chunk_ids": ["r", "k"], "steps": [], "hop": 1}
    candidate = relation_candidate()
    candidate["association_paths"] = [chain]
    monkeypatch.setattr(
        associations, "search_structure_channels",
        structure_stub(relation=[candidate]),
    )
    response = await service._execute_chunk_search(
        search_backend.db, None, "kb", fusion_payload(top_k=2),
    )
    assert response["success"], response
    assert {item["chunk_id"] for item in response["data"]["results"]} == {"r", "k"}
    returned = {item["chunk_id"] for item in response["data"]["results"]}
    assert response["data"]["meta"]["_association"]["evidence_budget_truncated"] is False
    assert all(
        set(p["support_chunk_ids"]) <= returned
        for p in response["data"]["meta"]["_association"]["paths"]
    )


@pytest.mark.asyncio
async def test_fusion_strict_mode_fails_when_structural_channel_fails(search_backend, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(associations, "search_structure_channels", boom)
    response = await service._execute_chunk_search(
        search_backend.db, None, "kb", fusion_payload(_strict_channels=True),
    )
    assert response["success"] is False


@pytest.mark.asyncio
async def test_fusion_degrades_to_hybrid_when_structural_channel_fails(search_backend, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(associations, "search_structure_channels", boom)
    response = await service._execute_chunk_search(search_backend.db, None, "kb", fusion_payload())
    assert response["success"], response
    meta = response["data"]["meta"]
    assert meta["_association"] == {"executed": False, "kind": "fusion"}
    assert meta["_fusion"]["coverage_incomplete"] is True
    assert meta["_executed_channels"] == ["vector", "keyword"]


@pytest.mark.asyncio
async def test_orchestrator_builds_evidence_reader_once_for_fusion(search_backend, monkeypatch):
    built = []

    class FakeReader:
        def __init__(self, db, kb_id):
            built.append((db, kb_id))

        def capabilities(self):
            return {"relations": True, "events": True}

    search_backend.db.get.return_value = SimpleNamespace(retrieval_profile={
        "version": "new_profile_v2",
        "build_indexes": ["relations", "events"],
    })
    monkeypatch.setattr(associations, "EvidenceReader", FakeReader)
    monkeypatch.setattr(
        associations, "search_structure_channels",
        structure_stub(relation=[relation_candidate()]),
    )
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "甲校有哪些实践资源", "strategy": strategy("fusion"),
    })
    assert response["success"], response
    assert len(built) == 1
    retrieval = response["data"]["meta"]["retrieval"]
    assert retrieval["executed_strategy"]["tier"] == "fusion"
    assert retrieval["fusion"]["channel_status"]["relation"] == "ready"
    assert retrieval["fusion"]["channel_status"]["event"] == "ready"
    assert retrieval["channels"] == ["vector", "keyword", "relation", "event"]
    assert retrieval["association"]["kind"] == "fusion"


def test_event_relevance_ignores_direct_hit_and_rewards_specificity_and_time():
    row = SimpleNamespace(
        id="e1", text="甲校与乙实验室在2025年签订实践基地协议",
        qualifiers={"time": "2025年"},
    )
    terms = ["甲校", "实践"]
    specific = associations._event_relevance(
        row, {"a", "b"}, {"a", "b"}, terms, {"a": 1, "b": 1}, "甲校合作时间",
    )
    generic = associations._event_relevance(
        row, {"a", "b"}, {"a", "b"}, terms, {"a": 30, "b": 30}, "甲校合作时间",
    )
    assert specific > generic
    assert specific > 0.9 and generic >= 0.7
    # 公式不包含 direct_hit；同一事件无论是否已被基础命中，得分一致。
    assert specific == associations._event_relevance(
        row, {"a", "b"}, {"a", "b"}, terms, {"a": 1, "b": 1}, "甲校合作时间",
    )


def test_rank_events_uses_direct_hit_only_as_tiebreaker():
    a = event_candidate("a", 0.9, direct_hit=False)
    b = event_candidate("b", 0.9, direct_hit=True)
    c = event_candidate("c", 0.95, direct_hit=False)
    assert [item["chunk_id"] for item in associations.rank_events([a, b])] == ["b", "a"]
    assert associations.rank_events([a, c])[0]["chunk_id"] == "c"


def test_select_fusion_evidence_protects_complete_chains_and_returns_unused_slots():
    chain_item = {**relation_candidate("x"), "association_paths": [
        {"support_chunk_ids": ["v", "x"], "steps": [], "hop": 1},
    ]}
    ordered = [{"chunk_id": "v"}, {"chunk_id": "k"}, chain_item]
    selected, used = associations.select_fusion_evidence(
        ordered, 3, {"v"}, event_pool=[event_candidate("e", 0.9)],
        event_reserved_slots=1, event_threshold=0.5,
    )
    ids = {item["chunk_id"] for item in selected}
    assert "e" in ids and "x" in ids and "v" in ids
    assert "k" not in ids  # 只替换不受完整链保护的通用候选
    assert used["event"] == 1
    selected, used = associations.select_fusion_evidence(
        ordered, 3, {"v"}, event_pool=[event_candidate("e", 0.2)],
        event_reserved_slots=1, event_threshold=0.5,
    )
    assert "e" not in {item["chunk_id"] for item in selected}
    assert used["event"] == 0

@pytest.mark.asyncio
async def test_structure_channels_share_seeds_once_and_isolate_quotas(indexed, monkeypatch):
    seed_calls, budgets = [], {}
    original_seeds = associations._seeds
    original_local = associations._local
    original_events = associations._events

    def counting_seeds(reader, query, direct, limit):
        seed_calls.append(limit)
        return original_seeds(reader, query, direct, limit)

    def local(reader, seeds, budget, deadline=None):
        budgets["relation"] = budget["max_candidates"]
        return original_local(reader, seeds, budget, deadline=deadline)

    def events(reader, seeds, query, direct, budget, deadline=None):
        budgets["event"] = budget["max_candidates"]
        return original_events(reader, seeds, query, direct, budget, deadline=deadline)

    monkeypatch.setattr(associations, "_seeds", counting_seeds)
    monkeypatch.setattr(associations, "_local", local)
    monkeypatch.setattr(associations, "_events", events)
    with indexed.sessions() as db:
        reader = associations.EvidenceReader(db, "kb")
        budget = AssociationBudget(
            relation_quota=2, event_quota=1, max_structure_candidates=50,
        ).model_dump()
        batches, metas, _seeds = associations.search_structure_channels(
            reader, "甲校有哪些实践资源", [{"chunk_id": "doc-chunk"}],
            ["relations", "events"], budget, True,
        )
    assert len(seed_calls) == 1
    assert budgets == {"relation": 2, "event": 1}
    assert "relations" in batches and "events" in batches
    assert metas["relations"]["provider"] == "know_rag_local_v1"
    assert metas["events"]["provider"] == "know_rag_event_sql_v1"


def test_two_level_rrf_keeps_structure_as_one_ranked_unit():
    relation = [relation_candidate("a"), relation_candidate("b")]
    event = [event_candidate("c", 0.9), event_candidate("d", 0.8)]
    structure = associations.rank_structure_channels(relation, event, 60)
    by_id = {item["chunk_id"]: item for item in structure}
    assert by_id["a"]["structure_score"] == by_id["c"]["structure_score"]
    assert by_id["a"]["structure_score"] > by_id["b"]["structure_score"]
    assert [item["structure_rank"] for item in structure] == [1, 2, 3, 4]

    base = [{"chunk_id": f"v{i}", "score": 1.0} for i in range(5)]
    fused = associations.rank_fusion_channels(base, structure, 60)
    fused_by_id = {item["chunk_id"]: item for item in fused}
    # 基础第 1 名 1/61 仍高于结构第 1 名 0.8/61；结构优势由保留位补充，而不是抬高权重。
    assert fused_by_id["v0"]["score"] == 1 / 61
    assert fused_by_id["a"]["score"] == 0.8 / 61
    assert fused[0]["chunk_id"] == "v0"


def test_graph_features_reorder_only_candidates_already_in_pool():
    base = [{"chunk_id": "v0", "score": 1 / 61}, {"chunk_id": "v1", "score": 1 / 62}]
    relation = [relation_candidate("v1")]
    event = [event_candidate("v1", 0.9), event_candidate("e", 0.95)]
    ordered = associations.apply_graph_features(
        base, relation, event, graph_weight=0.15, event_weight=0.25, rrf_k=60,
    )
    ids = [item["chunk_id"] for item in ordered]
    assert set(ids) == {"v0", "v1"}  # 图独有候选不被特征模式新增
    assert ids[0] == "v1"
    assert next(item for item in ordered if item["chunk_id"] == "v1")["graph_feature"] > 0
    assert next(item for item in ordered if item["chunk_id"] == "v0")["graph_feature"] == 0


@pytest.mark.asyncio
async def test_feature_mode_reserves_relation_only_evidence(search_backend, monkeypatch):
    settings = FusionSettings(rank_mode="feature", relation_reserved_slots=1).model_dump()
    monkeypatch.setattr(
        associations, "search_structure_channels",
        structure_stub(relation=[relation_candidate("r")], event=[]),
    )
    response = await service._execute_chunk_search(
        search_backend.db, None, "kb",
        fusion_payload(top_k=2, _association_kinds=["relations"], _fusion_settings=settings),
    )
    assert response["success"], response
    ids = [item["chunk_id"] for item in response["data"]["results"]]
    assert "r" in ids
    fusion = response["data"]["meta"]["_fusion"]
    assert fusion["mode"]["rank_mode"] == "feature"
    assert fusion["relation_reserved_slots_used"] == 1


@pytest.mark.asyncio
async def test_structure_candidate_cap_is_shared_across_channels(monkeypatch):
    monkeypatch.setattr(associations, "_seeds", lambda *args, **kwargs: {})
    relation_rows = [relation_candidate(f"r{i}") for i in range(40)]

    def relation_stub(reader, query, direct, budget, include_content, seeds):
        return relation_rows, {
            "paths": [], "coverage_incomplete": False, "timeout": False,
            "elapsed_ms": 0.0, "seed_count": 0,
        }

    captured = {}

    def event_stub(reader, query, direct, budget, include_content, seeds, threshold=0.0, deadline=None):
        captured["event_cap"] = budget["max_candidates"]
        return [], {
            "paths": [], "coverage_incomplete": False, "timeout": False,
            "elapsed_ms": 0.0, "seed_count": 0, "events_scored": 0,
            "direct_overlap_count": 0, "top_event_relevance": 0.0,
        }

    monkeypatch.setattr(associations, "search_relation_channel", relation_stub)
    monkeypatch.setattr(associations, "search_event_channel", event_stub)
    fake_reader = SimpleNamespace(
        coverage={"relations": {"ready": 0, "total": 0}, "events": {"ready": 0, "total": 0}},
        truncated=False,
    )
    budget = AssociationBudget(
        relation_quota=40, event_quota=40, max_structure_candidates=50,
    ).model_dump()
    batches, metas, _ = associations.search_structure_channels(
        fake_reader, "问题", [], ["relations", "events"], budget, True,
    )
    # 关系已用满 40，事件只剩 10 个结构总名额，不能被 40 的通道配额穿透。
    assert captured["event_cap"] == 10
    assert len(batches["relations"]) == 40


def test_structure_channels_vote_once_per_chunk_and_merge_sources():
    structure = associations.rank_structure_channels(
        [relation_candidate("x")], [event_candidate("x", 0.9)], 60,
    )
    assert len(structure) == 1
    assert structure[0]["score"] == 1 / 61 + 1 / 61
    assert set(structure[0]["retrieval_sources"]) == {"relation", "event"}
    assert structure[0]["event_relevance"] == 0.9


def _background_retrieval():
    return {
        "results": [
            {
                "chunk_id": "c1", "chunk_type": "text",
                "content": "甲校与乙实验室在2025年签订实践基地协议。", "score": 0.9,
            }
        ],
        "parent_groups": [],
        "background": [{
            "summary_id": "community:1:0", "text": "多份材料提到实践基地合作。",
            "report_ids": ["community:1"],
            "evidence": [{"chunk_id": "c1", "quote": "签订实践基地协议",
                          "returned_chunk_id": "c1"}],
            "derived": True, "citation_allowed": False,
        }],
        "event_lines": [{
            "event_id": "e1", "text": "签订实践基地协议", "time": "2025年",
            "chunk_id": "c1", "support_chunk_ids": ["c1"],
            "derived": True, "citation_allowed": False,
        }],
    }


def test_background_renders_before_sources_and_does_not_enter_evidence():
    from app.services.llm_context_builder import (
        build_evidence_groups_payload, build_llm_context_payload,
    )

    data = _background_retrieval()
    payload = build_llm_context_payload(data)
    text = payload["context_text"]
    assert text.index("【背景摘要") < text.index("【事件线") < text.index("【资料 S1】")
    assert "B1." in text and "E1." in text
    assert [source["source_id"] for source in payload["sources"]] == ["S1"]
    assert payload["background"][0]["citation_allowed"] is False

    groups = build_evidence_groups_payload(data)
    assert groups["background"][0]["summary_id"] == "community:1:0"
    assert groups["event_lines"][0]["event_id"] == "e1"
    assert not any("多份材料提到" in (chunk.get("content") or "") for chunk in groups["chunks"])


def test_background_with_invalid_citation_is_dropped_with_warning():
    from app.services.context.knowledge_context_service import KnowledgeContextService
    from app.services.llm_context_builder import build_llm_context_payload

    data = _background_retrieval()
    data["background"][0]["evidence"][0]["quote"] = "原文中不存在的句子"
    payload = build_llm_context_payload(data)
    assert payload["background"] == []
    assert payload["meta"]["background_withheld"] == 1
    assert "【背景摘要" not in payload["context_text"]

    KnowledgeContextService._attach_retrieval_meta(payload, {"meta": {}})
    assert any("背景摘要" in str(item) for item in payload["warnings"])


def test_event_lines_only_reference_returned_chunks():
    from app.services.llm_context_builder import build_event_lines

    results = [{
        "chunk_id": "c1",
        "association_paths": [{
            "support_chunk_ids": ["c1", "c2"], "hop": 1,
            "steps": [{
                "kind": "event_seed", "event_id": "e1", "event_text": "签订协议",
                "event_time": "2025年", "chunk_id": "c1",
            }],
        }],
    }]
    lines = build_event_lines(results)
    assert lines[0]["support_chunk_ids"] == ["c1"]
    assert lines[0]["citation_allowed"] is False


@pytest.mark.asyncio
async def test_orchestrator_appends_background_support_without_polluting_evidence(search_backend, monkeypatch):
    class FakeReader:
        def __init__(self, db, kb_id):
            pass

        def capabilities(self):
            return {"relations": True, "events": True}

    monkeypatch.setattr(associations, "EvidenceReader", FakeReader)
    from app.services.knowledge_index import communities
    from app.services.retrieval import global_search

    search_backend.db.get.return_value = SimpleNamespace(retrieval_profile={
        "version": "new_profile_v2",
        "build_indexes": ["relations", "events", "communities"],
    })
    monkeypatch.setattr(communities, "current_reports", lambda db, kb_id: (
        [{"report_id": "community:1"}], {"state": "ready"},
    ))
    monkeypatch.setattr(associations, "EvidenceReader", FakeReader)
    monkeypatch.setattr(
        associations, "search_structure_channels",
        structure_stub(relation=[relation_candidate()]),
    )
    summary = {"summary_id": "community:1:0", "text": "背景总结", "report_ids": ["community:1"],
               "evidence_chunk_ids": ["bg1"], "evidence": [{"chunk_id": "bg1", "quote": "支持原文"}],
               "derived": True, "citation_allowed": False}
    support = {"chunk_id": "bg1", "content": "支持原文", "score": 0.0,
               "match_type": "global_support", "retrieval_sources": ["global_support"]}
    monkeypatch.setattr(
        global_search, "retrieve_global_background",
        AsyncMock(return_value=([summary], [support], {
            "executed": True, "status": "ready", "model_calls": 2,
            "coverage_incomplete": False,
        })),
    )
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "甲校有哪些实践合作资源",
        "strategy": strategy("fusion", fusion={"include_global": True}),
    })
    assert response["success"], response
    data = response["data"]
    assert data["background"] == [summary]
    assert "bg1" in {item["chunk_id"] for item in data["results"]}
    assert summary["text"] not in {item.get("content") for item in data["results"]}
    retrieval = data["meta"]["retrieval"]
    assert retrieval["global_background"]["status"] == "ready"
    assert retrieval["fusion"]["channel_status"]["global"] == "ready"


@pytest.mark.asyncio
async def test_global_background_failure_does_not_change_evidence(search_backend, monkeypatch):
    class FakeReader:
        def __init__(self, db, kb_id):
            pass

        def capabilities(self):
            return {"relations": True, "events": True}

    monkeypatch.setattr(associations, "EvidenceReader", FakeReader)
    from app.services.knowledge_index import communities
    from app.services.retrieval import global_search

    search_backend.db.get.return_value = SimpleNamespace(retrieval_profile={
        "version": "new_profile_v2",
        "build_indexes": ["relations", "events", "communities"],
    })
    monkeypatch.setattr(communities, "current_reports", lambda db, kb_id: (
        [{"report_id": "community:1"}], {"state": "ready"},
    ))
    monkeypatch.setattr(associations, "EvidenceReader", FakeReader)
    monkeypatch.setattr(
        associations, "search_structure_channels",
        structure_stub(relation=[relation_candidate()]),
    )
    monkeypatch.setattr(
        global_search, "retrieve_global_background",
        AsyncMock(return_value=([], [], {"executed": False, "status": "timeout",
                                         "coverage_incomplete": True})),
    )
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "strategy": strategy("fusion", fusion={"include_global": True}),
    })
    assert response["success"], response
    assert response["data"]["background"] == []
    assert "bg1" not in {item["chunk_id"] for item in response["data"]["results"]}
    assert response["data"]["meta"]["retrieval"]["global_background"]["status"] == "timeout"


@pytest.mark.asyncio
async def test_fusion_end_to_end_with_real_relation_and_event_indexes(indexed, monkeypatch):
    async def vector(db, kb_id, query, top_k, threshold, include_content):
        return [{"chunk_id": "doc-chunk", "score": 0.9}], ""

    monkeypatch.setattr(service, "vector_search", vector)
    monkeypatch.setattr(service, "keyword_search", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "get_or_create_retrieval_config",
                        lambda db: SimpleNamespace(**service.DEFAULT_RETRIEVAL_CONFIG))
    monkeypatch.setattr(service, "record_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "attach_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "build_parent_groups", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "_expand_figure_bindings", lambda db, results, **kwargs: results)
    monkeypatch.setattr(service, "get_embedding_runtime",
                        lambda db: SimpleNamespace(provider="test", model_name="test"))
    with indexed.sessions() as db:
        response = await service.hybrid_search(db, None, "kb", {
            "query": "甲校有哪些实践资源", "top_k": 2,
            "strategy": strategy("fusion", fusion={"event_policy": "independent"}),
        })
    assert response["success"], response
    fusion = response["data"]["meta"]["retrieval"]["fusion"]
    assert fusion["channel_status"]["relation"] == "ready"
    assert fusion["channel_status"]["event"] == "ready"
    assert fusion["candidate_counts"]["relation"] >= 1
    assert set(response["data"]["meta"]["retrieval"]["channels"]) == {
        "vector", "keyword", "relation", "event",
    }


def test_background_is_dropped_when_support_falls_outside_returned_chunks():
    from app.services.llm_context_builder import build_evidence_groups_payload

    data = _background_retrieval()
    data["results"].append({
        "chunk_id": "c2", "chunk_type": "text",
        "content": "另一份支持材料提到实践基地协议。", "score": 0.5,
    })
    data["background"][0]["evidence"] = [{
        "chunk_id": "c2", "returned_chunk_id": "c2", "quote": "实践基地协议",
    }]
    data["background"][0]["evidence_chunk_ids"] = ["c2"]
    payload = build_evidence_groups_payload(data, {"match_count": 1})
    returned = {chunk["chunk_id"] for chunk in payload["chunks"]}
    assert "c2" not in returned
    assert payload["background"] == []
    assert payload["meta"]["background_withheld"] >= 1


def _fake_reader():
    return SimpleNamespace(
        coverage={"relations": {"ready": 1, "total": 1}, "events": {"ready": 1, "total": 1}},
        truncated=False,
    )


def test_single_structural_channel_failure_keeps_the_other(monkeypatch):
    monkeypatch.setattr(associations, "_seeds", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        associations, "search_relation_channel",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("relation db down")),
    )
    monkeypatch.setattr(
        associations, "search_event_channel",
        lambda *args, **kwargs: ([event_candidate("e", 0.9)], {
            "paths": [path("e")], "coverage_incomplete": False, "timeout": False,
            "elapsed_ms": 0.0, "seed_count": 0, "events_scored": 1,
            "direct_overlap_count": 0, "top_event_relevance": 0.9,
        }),
    )
    batches, metas, _ = associations.search_structure_channels(
        _fake_reader(), "甲校的合作事件", [], ["relations", "events"],
        AssociationBudget().model_dump(), True,
    )
    assert metas["relations"]["failed"] is True
    assert metas["events"].get("failed") is None
    assert [item["chunk_id"] for item in batches["events"]] == ["e"]

    monkeypatch.setattr(
        associations, "search_relation_channel",
        lambda *args, **kwargs: ([relation_candidate("r")], {
            "paths": [path("r")], "coverage_incomplete": False, "timeout": False,
            "elapsed_ms": 0.0, "seed_count": 0,
        }),
    )
    monkeypatch.setattr(
        associations, "search_event_channel",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("event db down")),
    )
    batches, metas, _ = associations.search_structure_channels(
        _fake_reader(), "甲校的合作事件", [], ["relations", "events"],
        AssociationBudget().model_dump(), True,
    )
    assert metas["events"]["failed"] is True
    assert [item["chunk_id"] for item in batches["relations"]] == ["r"]


@pytest.mark.asyncio
async def test_fusion_reports_single_channel_failure_without_degrading(search_backend, monkeypatch):
    def failed_relation_stub(reader, query, direct, kinds, budget, include_content, event_threshold=0.0):
        batches = {"relations": [], "events": [event_candidate("e", 0.9)]}
        metas = {
            "relations": {**channel_meta("relations", []), "failed": True,
                          "coverage_incomplete": True},
            "events": channel_meta("events", [path("e")]),
        }
        return batches, metas, {}

    monkeypatch.setattr(associations, "search_structure_channels", failed_relation_stub)
    response = await service._execute_chunk_search(
        search_backend.db, None, "kb",
        fusion_payload(top_k=2, _association_kinds=["relations", "events"]),
    )
    assert response["success"], response
    fusion = response["data"]["meta"]["_fusion"]
    assert fusion["channel_status"]["relation"] == "failed"
    assert fusion["channel_status"]["event"] == "ready"
    assert fusion["coverage_incomplete"] is True
    assert "e" in {item["chunk_id"] for item in response["data"]["results"]}
    assert fusion["executed"] == ["vector", "keyword", "event"]


@pytest.mark.asyncio
async def test_strict_fusion_rejects_partial_structural_failure(search_backend, monkeypatch):
    def failed_relation_stub(reader, query, direct, kinds, budget, include_content, event_threshold=0.0):
        return (
            {"relations": [], "events": [event_candidate("e", 0.9)]},
            {"relations": {**channel_meta("relations", []), "failed": True},
             "events": channel_meta("events", [path("e")])},
            {},
        )

    monkeypatch.setattr(associations, "search_structure_channels", failed_relation_stub)
    response = await service._execute_chunk_search(
        search_backend.db, None, "kb",
        fusion_payload(top_k=2, _association_kinds=["relations", "events"], _strict_channels=True),
    )
    assert response["success"] is False
