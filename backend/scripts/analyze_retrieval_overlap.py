"""Diagnose source-span overlap between adjacent text chunks in Know_RAG.

Purpose
-------
Index/vectorization intentionally allows chunk overlap.  When adjacent chunks
in the same parent are both retrieved and displayed, their overlapping source
span is shown twice.  This script is a *diagnostic* (not a production API) to:

  1. establish a baseline overlap distribution before normalization,
  2. regress after normalization,
  3. inspect concrete overlapping pairs in real documents.

Usage inside the backend container:

    python scripts/analyze_retrieval_overlap.py --kb <kb_id>
    python scripts/analyze_retrieval_overlap.py --document-id <doc_id> --show-content
    python scripts/analyze_retrieval_overlap.py --kb <kb_id> --min-overlap-ratio 0.8
    python scripts/analyze_retrieval_overlap.py --kb <kb_id> --only-same-parent
    python scripts/analyze_retrieval_overlap.py --document-id <doc_id> --show-content --limit 20
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal  # noqa: E402

TEXT_TYPES = ("text",)


def _scope_where(args) -> tuple[str, dict[str, Any], str]:
    if args.document_id:
        return "document_id = :document_id", {"document_id": args.document_id}, f"document={args.document_id}"
    if args.kb:
        return "knowledge_base_id = :kb_id", {"kb_id": args.kb}, f"kb={args.kb}"
    return "1=1", {}, "all"


def _compact(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _fetch_overlapping_pairs(db, where_sql: str, params: dict, min_ratio: float) -> list[dict]:
    sql = f"""
        WITH t AS (
            SELECT id, document_id, parent_chunk_id, chunk_index, start_at, end_at, content
            FROM chunks
            WHERE {where_sql}
              AND chunk_type IN ('text')
              AND start_at IS NOT NULL
              AND end_at IS NOT NULL
              AND metadata_json->>'source_position_is_exact' = 'true'
        ),
        t2 AS (
            SELECT *,
                   LAG(end_at)         OVER w AS prev_end,
                   LAG(start_at)       OVER w AS prev_start,
                   LAG(id)             OVER w AS prev_id,
                   LAG(chunk_index)    OVER w AS prev_chunk_index,
                   LAG(parent_chunk_id) OVER w AS prev_parent,
                   LAG(content)        OVER w AS prev_content
            FROM t
            WINDOW w AS (PARTITION BY document_id, parent_chunk_id ORDER BY start_at, chunk_index)
        ),
        ov AS (
            SELECT *,
                   GREATEST(0, LEAST(prev_end, end_at) - GREATEST(prev_start, start_at)) AS overlap_len,
                   (end_at - start_at) AS span_len,
                   (prev_end - prev_start) AS prev_span_len
            FROM t2
            WHERE prev_end IS NOT NULL
        )
        SELECT prev_id, id, prev_chunk_index, chunk_index, parent_chunk_id, prev_parent,
               prev_start, prev_end, start_at, end_at,
               overlap_len, span_len, prev_span_len, prev_content, content,
               CASE
                 WHEN LEAST(prev_span_len, span_len) = 0 THEN 0
                 ELSE overlap_len::numeric / LEAST(prev_span_len, span_len)
               END AS overlap_ratio
        FROM ov
        WHERE overlap_len > 0
          AND CASE
                WHEN LEAST(prev_span_len, span_len) = 0 THEN false
                ELSE overlap_len::numeric / LEAST(prev_span_len, span_len) >= :min_ratio
              END
        ORDER BY overlap_ratio DESC, overlap_len DESC
        LIMIT :limit
    """
    rows = db.execute(text(sql), {**params, "min_ratio": min_ratio, "limit": 100000}).mappings().all()
    return [dict(row) for row in rows]


def _overlap_preview(prev_content: str, content: str, prev_start: int, prev_end: int, start: int, end: int) -> tuple[str, str]:
    """Return (chunk_a_overlap_tail, chunk_b_overlap_head) using 1:1 offset→char mapping."""
    overlap_start = max(prev_start, start)
    overlap_end = min(prev_end, end)
    a_begin = max(0, overlap_start - prev_start)
    a_end = min(len(prev_content or ""), overlap_end - prev_start)
    b_begin = max(0, overlap_start - start)
    b_end = min(len(content or ""), overlap_end - start)
    return (prev_content or "")[a_begin:a_end], (content or "")[b_begin:b_end]


def _suggestion(ratio: float, overlap_len: int, prev_span: int, span: int) -> str:
    if ratio >= 0.8:
        return "merge/trim: 高度重叠，后块裁掉前缀重叠（或合并 span）"
    if ratio >= 0.3:
        return "trim: 后块裁掉前缀重叠部分"
    return "trim(轻微): 后块裁掉前缀重叠部分"


def analyze(args) -> int:
    where_sql, params, label = _scope_where(args)

    with SessionLocal() as db:
        pairs = _fetch_overlapping_pairs(db, where_sql, params, args.min_overlap_ratio)
        pairs = pairs[: args.limit]

        # ── Distribution summary (always computed on the scoped corpus) ──
        summary_sql = f"""
            WITH t AS (
                SELECT id, document_id, parent_chunk_id, chunk_index, start_at, end_at
                FROM chunks
                WHERE {where_sql}
                  AND chunk_type IN ('text')
                  AND start_at IS NOT NULL AND end_at IS NOT NULL
                  AND metadata_json->>'source_position_is_exact' = 'true'
            ),
            t2 AS (
                SELECT *,
                       LAG(end_at) OVER w AS prev_end,
                       LAG(start_at) OVER w AS prev_start
                FROM t
                WINDOW w AS (PARTITION BY document_id, parent_chunk_id ORDER BY start_at, chunk_index)
            ),
            ov AS (
                SELECT GREATEST(0, LEAST(prev_end, end_at) - GREATEST(prev_start, start_at)) AS overlap_len,
                       (end_at - start_at) AS span_len,
                       (prev_end - prev_start) AS prev_span_len
                FROM t2
                WHERE prev_end IS NOT NULL
            )
            SELECT COUNT(*) AS total_pairs,
                   COUNT(*) FILTER (WHERE overlap_len > 0) AS overlapping_pairs,
                   COUNT(*) FILTER (WHERE overlap_len > 0 AND overlap_len::numeric/NULLIF(LEAST(prev_span_len, span_len),0) >= 0.8) AS high_ge80,
                   COUNT(*) FILTER (WHERE overlap_len > 0 AND overlap_len::numeric/NULLIF(LEAST(prev_span_len, span_len),0) >= 0.3 AND overlap_len::numeric/NULLIF(LEAST(prev_span_len, span_len),0) < 0.8) AS mid_30_80,
                   COUNT(*) FILTER (WHERE overlap_len > 0 AND overlap_len::numeric/NULLIF(LEAST(prev_span_len, span_len),0) < 0.3) AS low_lt30
            FROM ov
        """
        summary = dict(db.execute(text(summary_sql), params).mappings().first())
        print(f"## Overlap summary ({label})")
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        print(f"\n## Top overlapping adjacent text chunk pairs (min_ratio={args.min_overlap_ratio}, limit={args.limit})")
        if not pairs:
            print("(none above threshold)")
            return 0

        for i, p in enumerate(pairs, start=1):
            ratio = float(p["overlap_ratio"])
            prev_span = int(p["prev_span_len"])
            span = int(p["span_len"])
            overlap_len = int(p["overlap_len"])
            a_tail, b_head = _overlap_preview(
                p.get("prev_content"), p.get("content"),
                int(p["prev_start"]), int(p["prev_end"]), int(p["start_at"]), int(p["end_at"]),
            )
            print(f"\n[{i}] overlap_ratio={ratio:.3f}  overlap_len={overlap_len}  same_parent={bool(p.get('parent_chunk_id'))}")
            print(f"    chunk_a: {p['prev_id']}  index={p['prev_chunk_index']}  span=[{p['prev_start']},{p['prev_end']}) len={prev_span}")
            print(f"    chunk_b: {p['id']}  index={p['chunk_index']}  span=[{p['start_at']},{p['end_at']}) len={span}")
            print(f"    parent:  {p.get('parent_chunk_id') or '(none)'}")
            if args.show_content:
                print(f"    a_overlap_tail: {_compact(a_tail)!r}")
                print(f"    b_overlap_head: {_compact(b_head)!r}")
            print(f"    suggestion: {_suggestion(ratio, overlap_len, prev_span, span)}")

        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose source-span overlap between adjacent text chunks")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--kb", help="Knowledge base id to scan")
    group.add_argument("--document-id", help="Document id to scan")
    parser.add_argument("--min-overlap-ratio", type=float, default=0.0, help="Only show pairs with overlap_ratio >= this (0.0~1.0)")
    parser.add_argument("--only-same-parent", action="store_true", default=True, help="Only consider adjacency within the same parent (always on; kept for interface compatibility)")
    parser.add_argument("--show-content", action="store_true", help="Show overlap text preview")
    parser.add_argument("--limit", type=int, default=30, help="Max pairs to list")
    args = parser.parse_args()
    raise SystemExit(analyze(args))


if __name__ == "__main__":
    main()
