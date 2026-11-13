"""候选预算必须真实进入召回、融合和重排，去重不得切断关联证据链。"""

from unittest.mock import AsyncMock

import pytest

from test_retrieval_strategy import search_backend
from app.services import retrieval_service as service
from app.services.retrieval.policy_resolver import resolve_policy
from app.services.retrieval.source_dedup import normalize_channels, remap_path
from app.services.retrieval.associations import select_association_evidence


def item(chunk_id, start=0, doc="doc", revision="rev"):
    return {
        "chunk_id": chunk_id, "id": chunk_id, "knowledge_base_id": "kb", "document_id": doc,
        "chunk_type": "table", "start_at": start, "end_at": start + 20, "content_hash": "same",
        "metadata": {"canonical_parse_revision": revision, "source_position_is_exact": True,
                     "source_node_ids": ["table-node"]}, "score": .9,
    }


@pytest.mark.parametrize("route", ["semantic", "hybrid", "graph", "event_graph"])
def test_every_v2_route_including_fallback_preserves_candidate_budget(route):
    strategy = {"family": "event_graph"} if route == "event_graph" else {"family": "tiered", "tier": route}
    strategy["budget"] = {"candidate_k": 40}
    plan = resolve_policy({"strategy": strategy})
    assert plan.payload["_candidate_k"] == 40
    assert resolve_policy({"use_vector": True}).payload.get("_candidate_k") is None


def test_same_source_votes_once_per_channel_and_shares_canonical_id():
    vector = [{**item("a"), "vector_rank": 1}, {**item("b"), "vector_rank": 2}]
    keyword = [{**item("b"), "keyword_rank": 1}]
    (vectors, keywords), aliases = normalize_channels(vector, keyword)
    assert len(vectors) == len(keywords) == 1
    assert vectors[0]["chunk_id"] == keywords[0]["chunk_id"] == "a"
    assert aliases["b"] == "a"
    assert vector[1]["chunk_id"] == "b"


@pytest.mark.parametrize("other", [item("b", start=50), item("b", doc="other"), item("b", revision="other")])
def test_same_content_at_another_source_is_not_collapsed(other):
    (rows,), _ = normalize_channels([item("a"), other])
    assert len(rows) == 2


def test_missing_source_proof_is_conservatively_kept():
    other = item("b")
    other["metadata"]["source_position_is_exact"] = False
    (rows,), _ = normalize_channels([item("a"), other])
    assert len(rows) == 2


def test_channel_scores_are_not_inherited_from_representative():
    vector = {**item("a"), "vector_rank": 1, "vector_score": .9}
    keyword = {**item("b"), "keyword_rank": 1, "keyword_score": .8}
    (vectors, keywords), _ = normalize_channels([vector], [keyword])
    assert "keyword_rank" not in vectors[0]
    assert "vector_rank" not in keywords[0]


def test_path_aliases_remain_complete_after_normalization():
    path = {"support_chunk_ids": ["duplicate", "other"], "steps": [{"chunk_id": "duplicate"}], "hop": 1}
    mapped = remap_path(path, {"duplicate": "kept"})
    assert mapped["support_chunk_ids"] == ["kept", "other"]
    assert mapped["steps"][0]["source_chunk_id"] == "duplicate"
    assert path["support_chunk_ids"][0] == "duplicate"
    rows = [{"chunk_id": "other", "association_paths": [mapped]}, {"chunk_id": "kept"}]
    assert {row["chunk_id"] for row in select_association_evidence(rows, 2, {"kept"})} == {"kept", "other"}
    assert not any(row.get("association_paths") for row in select_association_evidence(rows, 1, {"kept"}))


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["semantic", "hybrid"])
async def test_recall_twenty_then_rerank_before_selecting_five(search_backend, monkeypatch, route):
    candidates = [{"chunk_id": str(i), "score": 1 / (i + 1)} for i in range(20)]
    search_backend.vector.return_value = (candidates, "")
    search_backend.keyword.return_value = []
    async def rerank(db, query, rows, top_k):
        assert len(rows) == top_k == 20
        # 原本排第20的必要证据必须有机会进入最终结果。
        return list(reversed(rows))
    ranker = AsyncMock(side_effect=rerank)
    monkeypatch.setattr(service, "rerank_results", ranker)
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "top_k": 5, "rerank_enabled": True,
        "strategy": {"family": "tiered", "tier": route, "budget": {"candidate_k": 20}},
    })
    assert response["success"], response
    assert search_backend.vector.call_args.args[3] == 20
    assert [row["chunk_id"] for row in response["data"]["results"]] == ["19", "18", "17", "16", "15"]
    assert response["data"]["meta"]["retrieval"]["candidate_budget"]["selected_count"] == 5


@pytest.mark.asyncio
async def test_legacy_recall_count_is_unchanged(search_backend):
    await service.hybrid_search(search_backend.db, None, "kb", {"query": "问题", "top_k": 5})
    assert search_backend.vector.call_args.args[3] == 5


@pytest.mark.asyncio
async def test_duplicate_tables_do_not_consume_final_slots(search_backend):
    search_backend.vector.return_value = ([item("a"), item("b"), item("c", start=50), item("d", start=100)], "")
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "top_k": 3,
        "strategy": {"family": "tiered", "tier": "semantic", "budget": {"candidate_k": 3}},
    })
    assert [row["chunk_id"] for row in response["data"]["results"]] == ["a", "c", "d"]
    diagnostic = response["data"]["meta"]["retrieval"]["candidate_budget"]
    assert diagnostic["raw_counts"]["vector"] == 4
    assert diagnostic["unique_counts"]["vector"] == diagnostic["candidate_counts"]["vector"] == 3


@pytest.mark.asyncio
async def test_full_channel_union_reaches_rerank_with_real_rrf(search_backend, monkeypatch):
    search_backend.vector.return_value = ([{
        "chunk_id": f"v{i}", "vector_rank": i + 1, "vector_score": .9,
    } for i in range(30)], "")
    search_backend.keyword.return_value = [{
        "chunk_id": f"k{i}", "keyword_rank": i + 1, "keyword_score": .8,
    } for i in range(30)]
    monkeypatch.setattr(service, "merge_results", service.merge_results_rrf)

    async def rerank(db, query, rows, top_k):
        assert len(rows) == top_k == 40
        return list(reversed(rows))

    monkeypatch.setattr(service, "rerank_results", rerank)
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "top_k": 5, "rerank_enabled": True,
        "strategy": {"family": "tiered", "tier": "hybrid", "budget": {"candidate_k": 20}},
    })
    assert response["success"], response
    diagnostic = response["data"]["meta"]["retrieval"]["candidate_budget"]
    assert diagnostic["raw_counts"] == diagnostic["unique_counts"] == {"vector": 30, "keyword": 30}
    assert diagnostic["candidate_counts"] == {"vector": 20, "keyword": 20}
    assert diagnostic["base_fused_count"] == diagnostic["merged_count_before_rerank"] == 40
    assert len(response["data"]["results"]) == 5


@pytest.mark.asyncio
async def test_fallback_still_uses_requested_candidate_budget(search_backend):
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "top_k": 2,
        "strategy": {"family": "event_graph", "budget": {"candidate_k": 7}},
    })
    assert response["success"], response
    assert search_backend.vector.call_args.args[3] == 7
    assert search_backend.keyword.call_args.args[3] == 7
    diagnostic = response["data"]["meta"]["retrieval"]
    assert diagnostic["degraded"]
    assert diagnostic["executed_strategy"]["budget"]["candidate_k"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["graph", "event_graph"])
async def test_association_aliases_keep_complete_chain_after_rerank(search_backend, monkeypatch, family):
    from types import SimpleNamespace
    from app.services.retrieval import associations

    search_backend.db.get.return_value = SimpleNamespace(retrieval_profile={
        "version": "legacy_v1", "build_indexes": ["relations", "events"],
    })
    reader = SimpleNamespace(capabilities=lambda: {"relations": True, "events": True})
    monkeypatch.setattr(associations, "EvidenceReader", lambda *args: reader)
    path = {"support_chunk_ids": ["duplicate", "other"], "steps": [{"chunk_id": "duplicate"}], "hop": 2}
    candidates = [{**item("duplicate"), "association_paths": [path]},
                  {**item("other", start=50), "association_paths": [path]}]
    monkeypatch.setattr(associations, "search_associations", lambda *args: (candidates, {
        "paths": [path], "coverage_incomplete": False,
    }))
    search_backend.vector.return_value = ([item("kept")], "")
    search_backend.keyword.return_value = []

    async def rerank(db, query, rows, top_k):
        assert len(rows) == top_k == 2
        return list(reversed(rows))

    monkeypatch.setattr(service, "rerank_results", rerank)
    strategy = {"family": "event_graph"} if family == "event_graph" else {"family": "tiered", "tier": "graph"}
    response = await service.hybrid_search(search_backend.db, None, "kb", {
        "query": "问题", "top_k": 2, "rerank_enabled": True, "strategy": strategy,
    })
    assert response["success"], response
    data = response["data"]
    assert {row["chunk_id"] for row in data["results"]} == {"kept", "other"}
    paths = data["meta"]["retrieval"]["association"]["paths"]
    assert paths[0]["support_chunk_ids"] == ["kept", "other"]
    assert paths[0]["steps"][0]["source_chunk_id"] == "duplicate"
