"""Unit tests for the retrieval context overlap normalizer.

Cover the 12 cases in docs/retrieval_context_overlap_normalization_plan.md.
The normalizer is a pure function, so no DB or async fixtures are needed.
"""
from __future__ import annotations

import pytest

from app.services.retrieval_context_normalizer import (
    SpanItem,
    SpanNormalization,
    conservative_dedup_by_exact_string,
    normalize_spans,
)
from app.services.llm_context_builder import (
    build_cloud_flat_chunks_payload,
    build_evidence_groups_payload,
)


def _source(n: int) -> str:
    """Deterministic canonical source string of length n."""
    return "".join(str(i % 10) for i in range(n))


def _span(key, start, end, *, content=None, priority=1, score=0.0, order=0, exact=True, group_key="g", kind="text", node_ids=None):
    return SpanItem(
        key=key,
        start=start,
        end=end,
        content=content if content is not None else _source(end - start),
        priority=priority,
        score=score,
        source_order=order,
        exact=exact,
        group_key=group_key,
        kind=kind,
        source_node_ids=node_ids or [],
    )


# ── Case 1: exact offset A[0,100] B[80,180] → B trims leading 20 ──
def test_case1_exact_offset_trims_prefix():
    S = _source(200)
    items = [
        _span("A", 0, 100, content=S[0:100], priority=0, score=1.0),
        _span("B", 80, 180, content=S[80:180], priority=0, score=0.5),
    ]
    r = normalize_spans(items)
    assert r["A"].content == S[0:100]
    assert r["A"].overlap_trimmed is False
    assert r["B"].content == S[100:180]
    assert r["B"].overlap_trimmed is True
    assert r["B"].trimmed_chars == 20
    assert r["B"].overlap_source_keys == ["A"]


# ── Case 2: 90% overlap → merged text has no duplicate ──
def test_case2_high_overlap_no_duplicate():
    S = _source(120)
    items = [
        _span("A", 0, 100, content=S[0:100], priority=0, score=1.0),
        _span("B", 10, 110, content=S[10:110], priority=0, score=0.5),
    ]
    r = normalize_spans(items)
    merged = r["A"].content + r["B"].content
    assert merged == S[0:110]
    # Overlap region [10,100) appears exactly once.
    assert merged.count(S[10:100]) == 1


# ── Case 3: no overlap → both kept fully ──
def test_case3_no_overlap_keeps_both():
    S = _source(200)
    items = [
        _span("A", 0, 100, content=S[0:100], priority=0),
        _span("B", 100, 200, content=S[100:200], priority=0),
    ]
    r = normalize_spans(items)
    assert r["A"].content == S[0:100]
    assert r["B"].content == S[100:200]
    assert r["A"].overlap_trimmed is False and r["B"].overlap_trimmed is False


# ── Case 4: same string, different source offset → NOT deduped ──
def test_case4_same_string_different_offset_not_deduped():
    items = [
        _span("A", 0, 50, content="hello world", priority=0),
        _span("B", 200, 250, content="hello world", priority=0),
    ]
    r = normalize_spans(items)
    assert r["A"].content == "hello world"
    assert r["B"].content == "hello world"
    assert r["A"].overlap_trimmed is False and r["B"].overlap_trimmed is False


# ── Case 5: same parent adjacent overlap → trim ──
def test_case5_same_parent_adjacent_overlap_trims():
    S = _source(100)
    items = [
        _span("A", 0, 60, content=S[0:60], priority=0, group_key="p1"),
        _span("B", 40, 100, content=S[40:100], priority=0, group_key="p1"),
    ]
    r = normalize_spans(items)
    assert r["A"].content == S[0:60]
    assert r["B"].content == S[60:100]
    assert r["B"].overlap_trimmed is True


# ── Case 6: different parent → never cross-normalized ──
def test_case6_different_parent_not_cross_trimmed():
    S = _source(150)
    items = [
        _span("A", 0, 100, content=S[0:100], priority=0, group_key="p1"),
        _span("B", 50, 150, content=S[50:150], priority=0, group_key="p2"),
    ]
    r = normalize_spans(items)
    assert r["A"].content == S[0:100]
    assert r["B"].content == S[50:150]
    assert r["B"].overlap_trimmed is False


# ── Case 7: source_position_is_exact=false → conservative (no trim) ──
def test_case7_inexact_offset_leaves_untrimmed():
    items = [
        _span("A", 0, 100, content=_source(100), priority=0, exact=True),
        _span("B", 80, 180, content=_source(100), priority=0, exact=False),
    ]
    r = normalize_spans(items)
    assert r["B"].content == _source(100)
    assert r["B"].overlap_trimmed is False


# ── Case 8: source_node_ids same source ──
def test_case8_source_node_ids_signal():
    # Shared source_node_ids with NON-overlapping exact offsets → still not deduped
    # (span is the dedup criterion, node identity alone must not delete content).
    items = [
        _span("A", 0, 50, content="abcde", priority=0, node_ids=["n1", "n2"]),
        _span("B", 200, 250, content="abcde", priority=0, node_ids=["n2", "n3"]),
    ]
    r = normalize_spans(items)
    assert r["A"].content == "abcde"
    assert r["B"].content == "abcde"

    # Fallback: exact boundary string dedup when offsets are unavailable.
    trimmed, n = conservative_dedup_by_exact_string("abcd", "cdef")
    assert trimmed == "ef"
    assert n == 2


def test_case8b_fallback_no_exact_overlap_keeps_content():
    trimmed, n = conservative_dedup_by_exact_string("abcdef", "uvwxyz")
    assert trimmed == "uvwxyz"
    assert n == 0


# ── Case 9: standalone root unaffected ──
def test_case9_standalone_root_unaffected():
    S = _source(100)
    items = [
        _span("A", 0, 100, content=S[0:100], priority=0, group_key="orphan:A"),
        _span("B", 50, 150, content=S[50:150], priority=0, group_key="orphan:B"),
    ]
    r = normalize_spans(items)
    assert r["A"].content == S[0:100]
    assert r["B"].content == S[50:150]
    assert r["A"].overlap_trimmed is False and r["B"].overlap_trimmed is False


# ── Case 10: table / image / equation not text-deduped ──
def test_case10_non_text_kinds_not_deduped():
    S = _source(100)
    items = [
        _span("T", 0, 100, content=S[0:100], priority=0, kind="text"),
        _span("IMG", 50, 150, content=S[50:150], priority=0, kind="image"),
        _span("TBL", 60, 160, content=S[60:160], priority=0, kind="table"),
        _span("EQ", 70, 170, content=S[70:170], priority=0, kind="equation"),
    ]
    r = normalize_spans(items)
    assert r["IMG"].content == S[50:150]
    assert r["TBL"].content == S[60:160]
    assert r["EQ"].content == S[70:170]
    assert r["IMG"].overlap_trimmed is False
    assert r["TBL"].overlap_trimmed is False
    assert r["EQ"].overlap_trimmed is False


# ── Case 11: both raw hits keep their evidence id ──
def test_case11_hits_keep_evidence_id():
    S = _source(150)
    items = [
        _span("chunk-a", 0, 100, content=S[0:100], priority=0, score=0.9),
        _span("chunk-b", 50, 150, content=S[50:150], priority=0, score=0.8),
    ]
    r = normalize_spans(items)
    assert "chunk-a" in r and "chunk-b" in r
    assert r["chunk-a"].content != ""  # anchor keeps content
    assert r["chunk-b"].overlap_trimmed is True  # evidence preserved, content trimmed


# ── Case 12: final display text has each source span once ──
def test_case12_repeated_span_appears_once():
    S = _source(300)
    items = [
        _span("A", 0, 120, content=S[0:120], priority=0, score=0.9),
        _span("B", 100, 220, content=S[100:220], priority=0, score=0.8),
        _span("C", 200, 300, content=S[200:300], priority=0, score=0.7),
    ]
    r = normalize_spans(items)
    # Reassemble in source order and assert no source span is duplicated.
    merged = r["A"].content + r["B"].content + r["C"].content
    assert merged == S[0:300]


# ── Fully-contained chunk is emptied but keeps identity ──
def test_fully_contained_chunk_emptied():
    S = _source(100)
    items = [
        _span("big", 0, 100, content=S[0:100], priority=0, score=0.9),
        _span("small", 40, 60, content=S[40:60], priority=0, score=0.5),
    ]
    r = normalize_spans(items)
    assert r["big"].content == S[0:100]
    assert r["small"].content == ""
    assert r["small"].overlap_trimmed is True
    assert r["small"].overlap_source_keys == ["big"]


# ── Anchor priority: lower-priority neighbor is trimmed, hit kept full ──
def test_anchor_hit_kept_full_neighbor_trimmed():
    S = _source(200)
    # neighbor (priority 1) spans [0,120), hit (priority 0) spans [80,180).
    items = [
        _span("neighbor", 0, 120, content=S[0:120], priority=1, score=0.3),
        _span("hit", 80, 180, content=S[80:180], priority=0, score=0.9),
    ]
    r = normalize_spans(items)
    # hit is processed first (priority 0) and keeps its full content.
    assert r["hit"].content == S[80:180]
    assert r["hit"].overlap_trimmed is False
    # neighbor is trimmed against hit's suffix overlap [80,120).
    assert r["neighbor"].content == S[0:80]
    assert r["neighbor"].overlap_trimmed is True


# ──────────────────────────────────────────────────────────────────────
# Integration: normalization is applied through the context builders
# ──────────────────────────────────────────────────────────────────────

_SENT_SRC = "".join(
    f"Sentence number {i} contains real words with punctuation, and it ends here. "
    for i in range(60)
)


def _overlap_hit(chunk_id, parent_id, *, start, end, chunk_index, score):
    return {
        "chunk_id": chunk_id,
        "parent_chunk_id": parent_id,
        "chunk_index": chunk_index,
        "chunk_type": "text",
        "content": _SENT_SRC[start:end],
        "start_at": start,
        "end_at": end,
        "score": score,
        "metadata": {"source_position_is_exact": True},
        "image_info": {},
    }


def _overlap_group(parent_id):
    return {
        "parent_chunk_id": parent_id,
        "parent_key": parent_id,
        "parent_children": [],
        "matched_children": [],
        "binding_related_chunks": [],
    }


def test_integration_evidence_groups_trims_overlapping_hits():
    data = {
        "results": [
            _overlap_hit("c1", "P", start=0, end=200, chunk_index=1, score=0.9),
            _overlap_hit("c2", "P", start=150, end=350, chunk_index=2, score=0.8),
        ],
        "parent_groups": [_overlap_group("P")],
    }
    payload = build_evidence_groups_payload(data)
    chunks = payload["groups"][0]["chunks"]
    assert [c["chunk_id"] for c in chunks] == ["c1", "c2"]
    # c1 is the anchor (higher score) — not trimmed.
    assert chunks[0]["overlap_trimmed"] is False
    assert chunks[1]["overlap_trimmed"] is True
    assert chunks[1]["trimmed_chars"] == 50
    assert chunks[1]["overlap_source_chunk_ids"] == ["c1"]
    # Overlap region [150,200) is present in c1 but trimmed out of c2.
    assert _SENT_SRC[150:200] in chunks[0]["content"]
    assert _SENT_SRC[150:200] not in chunks[1]["content"]


def test_integration_cloud_flat_chunks_trims_overlapping_hits():
    data = {
        "results": [
            _overlap_hit("c1", "P", start=0, end=200, chunk_index=1, score=0.9),
            _overlap_hit("c2", "P", start=150, end=350, chunk_index=2, score=0.8),
        ],
        "parent_groups": [_overlap_group("P")],
    }
    payload = build_cloud_flat_chunks_payload(data, {"return_mode": "chunks"})
    chunks = payload["chunks"]
    hits = [c for c in chunks if c["role"] == "hit"]
    assert [c["chunk_id"] for c in hits] == ["c1", "c2"]
    assert hits[1]["overlap_trimmed"] is True
    assert hits[1]["trimmed_chars"] == 50
    assert _SENT_SRC[150:200] not in hits[1]["content"]
