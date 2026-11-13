"""Retrieval context normalization: deduplicate overlapping source spans at display time.

Index/vectorization intentionally allows chunk overlap.  When two adjacent
chunks in the same document+parent are both retrieved and displayed, their
overlapping source span is shown twice.  This module provides the *display-time*
normalization layer that runs after retrieval/group assembly and before the
UI/MCP/LLM context payload is emitted.

It never touches the index, chunk content in the DB, embeddings, BM25, RRF,
rerank, or figure binding.  It only trims the *content string* that will be
shown, and only trims text spans that are provably the same source region via
canonical ``start_at``/``end_at`` offsets.

Design (see docs/retrieval_context_overlap_normalization_plan.md):

* Only ``text`` chunks with exact source offsets participate.
* Scope is one group = same ``document_id`` + same ``parent_chunk_id``
  (``group_key``).  Cross-parent and standalone roots are never merged.
* Priority: direct hits are anchors (never trimmed), then by score desc,
  then by source order.
* Each chunk "claims" its full source span; later (lower-priority) chunks trim
  their prefix/suffix overlap against already-claimed spans.
* Fully-contained chunks have their content emptied but keep their identity.
* Fallback (no exact offset): conservative exact prefix/suffix string dedup
  only — never fuzzy, never by semantic similarity.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SpanItem:
    """A single display chunk participating in normalization.

    ``content`` is the string that will be displayed (already resolved from the
    chunk's ``content``/``matched_content``).  ``start``/``end`` are canonical
    char offsets into the document's normalized markdown; the mapping is ~1:1
    with ``content`` (99.8% of text chunks differ by ≤10 chars).
    """

    key: str
    start: int | None
    end: int | None
    content: str
    priority: int = 1  # lower = higher priority (0 = direct-hit anchor)
    score: float = 0.0
    source_order: int = 0  # chunk_index tie-break
    exact: bool = True  # source_position_is_exact
    group_key: str = ""  # same document+parent scope; "" = standalone
    kind: str = "text"  # only "text" participates; table/image/equation never trimmed
    source_node_ids: list[str] = field(default_factory=list)


@dataclass
class SpanNormalization:
    """Result of normalizing one chunk."""

    content: str
    overlap_trimmed: bool
    trimmed_chars: int
    overlap_source_keys: list[str]


def _merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    """Merge a list of ``[s, e)`` intervals into a sorted disjoint list."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: (iv[0], iv[1]))
    merged = [list(ordered[0])]
    for s, e in ordered[1:]:
        last = merged[-1]
        if s <= last[1]:
            last[1] = max(last[1], e)
        else:
            merged.append([s, e])
    return merged


def _covered_within(covered: list[list[int]], s: int, e: int) -> list[list[int]]:
    """Return the sub-intervals of ``[s, e)`` already covered by ``covered``."""
    result: list[list[int]] = []
    for cs, ce in covered:
        if ce <= s or cs >= e:
            continue
        result.append([max(cs, s), min(ce, e)])
    return result


def _normalize_group(items: list[SpanItem]) -> dict[str, SpanNormalization]:
    """Normalize one group (single ``group_key``) of chunks."""
    results: dict[str, SpanNormalization] = {}

    eligible: list[SpanItem] = []
    for it in items:
        if (
            it.kind == "text"
            and it.exact
            and it.start is not None
            and it.end is not None
            and it.end > it.start
        ):
            eligible.append(it)
        else:
            # Non-text, or no exact offset → leave untrimmed.
            results[it.key] = SpanNormalization(
                content=it.content,
                overlap_trimmed=False,
                trimmed_chars=0,
                overlap_source_keys=[],
            )

    # Priority order: anchors first, then higher score, then earlier source.
    ordered = sorted(eligible, key=lambda it: (it.priority, -it.score, it.start, it.source_order))

    covered: list[list[int]] = []  # merged claimed intervals (canonical offset space)
    claimed: list[tuple[int, int, str]] = []  # (s, e, key) per processed chunk

    for it in ordered:
        s = it.start  # type: ignore[assignment]
        e = it.end  # type: ignore[assignment]
        cov = _covered_within(covered, s, e)
        source_keys = [key for (cs, ce, key) in claimed if ce > s and cs < e]

        if not cov:
            results[it.key] = SpanNormalization(
                content=it.content,
                overlap_trimmed=False,
                trimmed_chars=0,
                overlap_source_keys=[],
            )
            covered = _merge_intervals(covered + [[s, e]])
            claimed.append((s, e, it.key))
            continue

        # Leading/trailing covered char counts (1:1 offset→char mapping).
        leading = cov[0][1] - s if cov[0][0] <= s else 0
        trailing = e - cov[-1][0] if cov[-1][1] >= e else 0
        total_covered = sum(ce - cs for cs, ce in cov)

        original = it.content
        if total_covered >= (e - s):
            # Fully contained by higher-priority chunk(s).
            results[it.key] = SpanNormalization(
                content="",
                overlap_trimmed=True,
                trimmed_chars=len(original),
                overlap_source_keys=source_keys,
            )
        else:
            leading_chars = min(leading, len(original))
            trailing_chars = min(trailing, max(0, len(original) - leading_chars))
            end_cut = len(original) - trailing_chars if trailing_chars else len(original)
            new_content = original[leading_chars:end_cut]
            results[it.key] = SpanNormalization(
                content=new_content,
                overlap_trimmed=bool(leading_chars or trailing_chars),
                trimmed_chars=len(original) - len(new_content),
                overlap_source_keys=source_keys,
            )

        covered = _merge_intervals(covered + [[s, e]])
        claimed.append((s, e, it.key))

    return results


def normalize_spans(items: list[SpanItem]) -> dict[str, SpanNormalization]:
    """Normalize overlapping spans, scoped to each ``group_key`` independently.

    Chunks in different groups (different parents) are never trimmed against
    each other; standalone roots (``group_key == ""``) are left untouched.
    """
    by_group: dict[str, list[SpanItem]] = {}
    for it in items:
        by_group.setdefault(it.group_key, []).append(it)

    results: dict[str, SpanNormalization] = {}
    for group_items in by_group.values():
        results.update(_normalize_group(group_items))
    return results


def conservative_dedup_by_exact_string(prev_content: str, content: str) -> tuple[str, int]:
    """Fallback dedup for chunks without exact offsets.

    Trims the longest suffix of ``prev_content`` that is also an exact prefix of
    ``content``.  Purely character-exact and boundary-based — it never removes
    text on semantic similarity, and never touches non-boundary repeats.

    Returns ``(trimmed_content, trimmed_chars)``.
    """
    prev = prev_content or ""
    cur = content or ""
    if not prev or not cur:
        return cur, 0
    max_len = min(len(prev), len(cur), 200)
    overlap = 0
    for n in range(max_len, 0, -1):
        if prev[-n:] == cur[:n]:
            overlap = n
            break
    if overlap == 0:
        return cur, 0
    return cur[overlap:], overlap
