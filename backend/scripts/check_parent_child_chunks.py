"""Validate structured parent-child chunks for Know_RAG documents.

Usage inside backend container:
    python scripts/check_parent_child_chunks.py --document-id <document_id>
    python scripts/check_parent_child_chunks.py --kb <knowledge_base_id>
    python scripts/check_parent_child_chunks.py --all
    python scripts/check_parent_child_chunks.py --document-id <document_id> --strict
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal  # noqa: E402

CHILD_TYPES = ("text", "image", "table")


def _rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in rows]


def _print_table(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n## {title}")
    if not rows:
        print("(empty)")
        return
    keys = list(rows[0].keys())
    print(" | ".join(keys))
    print(" | ".join("---" for _ in keys))
    for row in rows:
        print(" | ".join(str(row.get(k, "")) for k in keys))


def _scope_where(args) -> tuple[str, dict[str, Any], str]:
    if args.document_id:
        return "document_id = :document_id", {"document_id": args.document_id}, f"document={args.document_id}"
    if args.kb:
        return "knowledge_base_id = :kb_id", {"kb_id": args.kb}, f"kb={args.kb}"
    return "1=1", {}, "all"


def _scalar(db, sql: str, params: dict[str, Any]) -> int:
    return int(db.execute(text(sql), params).scalar() or 0)


def validate(args) -> int:
    where_sql, params, label = _scope_where(args)
    failures: list[str] = []

    with SessionLocal() as db:
        total = _scalar(db, f"SELECT COUNT(*) FROM chunks WHERE {where_sql}", params)
        ready = _scalar(db, f"SELECT COUNT(*) FROM chunks WHERE {where_sql} AND status='ready' AND is_enabled=true", params)
        parent_count = _scalar(db, f"SELECT COUNT(*) FROM chunks WHERE {where_sql} AND chunk_type='parent_text'", params)
        child_count = _scalar(
            db,
            f"SELECT COUNT(*) FROM chunks WHERE {where_sql} AND chunk_type IN ('text', 'image', 'table')",
            {**params},
        )

        print(f"Parent-child chunk validation: {label}")
        print(json.dumps({"total_chunks": total, "ready_enabled_chunks": ready, "parent_text": parent_count, "child_text_image_table": child_count}, ensure_ascii=False, indent=2))

        type_rows = _rows_to_dicts(
            db.execute(
                text(
                    f"""
                    SELECT chunk_type, COUNT(*) AS count
                    FROM chunks
                    WHERE {where_sql}
                    GROUP BY chunk_type
                    ORDER BY chunk_type
                    """
                ),
                params,
            ).all()
        )
        _print_table("Chunk types", type_rows)

        child_parent_rows = _rows_to_dicts(
            db.execute(
                text(
                    f"""
                    SELECT chunk_type,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE parent_chunk_id IS NOT NULL) AS with_parent,
                           COUNT(*) FILTER (WHERE parent_chunk_id IS NULL) AS without_parent
                    FROM chunks
                    WHERE {where_sql}
                      AND chunk_type IN ('text', 'image', 'table')
                    GROUP BY chunk_type
                    ORDER BY chunk_type
                    """
                ),
                {**params},
            ).all()
        )
        _print_table("Child parent coverage", child_parent_rows)

        orphan_rows = _rows_to_dicts(
            db.execute(
                text(
                    f"""
                    SELECT c.chunk_type, COUNT(*) AS orphan_count
                    FROM chunks c
                    LEFT JOIN chunks p ON p.id = c.parent_chunk_id
                    WHERE c.{where_sql}
                      AND c.chunk_type IN ('text', 'image', 'table')
                      AND c.parent_chunk_id IS NOT NULL
                      AND p.id IS NULL
                    GROUP BY c.chunk_type
                    ORDER BY c.chunk_type
                    """.replace("c.1=1", "1=1")
                ),
                {**params},
            ).all()
        )
        _print_table("Broken parent references", orphan_rows)

        chart_count = _scalar(
            db,
            f"""
            SELECT COUNT(*)
            FROM chunks
            WHERE {where_sql}
              AND chunk_type='image'
              AND (
                metadata_json->>'visual_subtype'='chart'
                OR metadata_json->>'raw_block_type'='chart'
                OR metadata_json->>'type'='chart'
                OR metadata_json->>'block_type'='chart'
              )
            """,
            params,
        )
        chart_with_parent = _scalar(
            db,
            f"""
            SELECT COUNT(*)
            FROM chunks
            WHERE {where_sql}
              AND chunk_type='image'
              AND parent_chunk_id IS NOT NULL
              AND (
                metadata_json->>'visual_subtype'='chart'
                OR metadata_json->>'raw_block_type'='chart'
                OR metadata_json->>'type'='chart'
                OR metadata_json->>'block_type'='chart'
              )
            """,
            params,
        )
        equation_count = _scalar(
            db,
            f"""
            SELECT COUNT(*)
            FROM chunks
            WHERE {where_sql}
              AND chunk_type='text'
              AND (metadata_json->>'is_equation'='true' OR metadata_json->>'subtype'='equation')
            """,
            params,
        )
        equation_with_parent = _scalar(
            db,
            f"""
            SELECT COUNT(*)
            FROM chunks
            WHERE {where_sql}
              AND chunk_type='text'
              AND parent_chunk_id IS NOT NULL
              AND (metadata_json->>'is_equation'='true' OR metadata_json->>'subtype'='equation')
            """,
            params,
        )
        multimodal_rows = [
            {"relation": "chart_as_image", "total": chart_count, "with_parent": chart_with_parent, "without_parent": chart_count - chart_with_parent},
            {"relation": "equation_as_text", "total": equation_count, "with_parent": equation_with_parent, "without_parent": equation_count - equation_with_parent},
        ]
        _print_table("Chart / equation coverage", multimodal_rows)

        parent_dist_rows = _rows_to_dicts(
            db.execute(
                text(
                    f"""
                    SELECT p.id AS parent_id,
                           p.document_id,
                           p.chunk_index AS parent_index,
                           LEFT(p.context_header, 80) AS context_header,
                           COUNT(c.id) AS child_count,
                           COUNT(c.id) FILTER (WHERE c.chunk_type='text') AS text_children,
                           COUNT(c.id) FILTER (WHERE c.chunk_type='image') AS image_children,
                           COUNT(c.id) FILTER (WHERE c.chunk_type='table') AS table_children
                    FROM chunks p
                    LEFT JOIN chunks c ON c.parent_chunk_id = p.id
                    WHERE p.{where_sql}
                      AND p.chunk_type='parent_text'
                    GROUP BY p.id, p.document_id, p.chunk_index, p.context_header
                    ORDER BY child_count DESC, p.chunk_index ASC
                    LIMIT :limit
                    """.replace("p.1=1", "1=1")
                ),
                {**params, "limit": args.limit},
            ).all()
        )
        _print_table(f"Top parent child distribution (limit={args.limit})", parent_dist_rows)

        parent_emb_count = _scalar(
            db,
            f"""
            SELECT COUNT(*)
            FROM chunk_embeddings ce
            JOIN chunks c ON c.id = ce.chunk_id
            WHERE c.{where_sql}
              AND c.chunk_type='parent_text'
              AND ce.is_enabled=true
            """.replace("c.1=1", "1=1"),
            params,
        )
        print("\n## Indexing checks")
        print(json.dumps({"parent_text_embeddings": parent_emb_count}, ensure_ascii=False, indent=2))

        orphan_total = sum(int(row.get("orphan_count") or 0) for row in orphan_rows)
        child_without_parent_total = sum(int(row.get("without_parent") or 0) for row in child_parent_rows)
        if parent_count > 0 and child_without_parent_total > 0:
            failures.append(f"child chunks without parent_chunk_id: {child_without_parent_total}")
        if orphan_total > 0:
            failures.append(f"child chunks with broken parent references: {orphan_total}")
        if chart_count and chart_count != chart_with_parent:
            failures.append(f"chart image chunks without parent: {chart_count - chart_with_parent}")
        if equation_count and equation_count != equation_with_parent:
            failures.append(f"equation text chunks without parent: {equation_count - equation_with_parent}")
        if parent_emb_count > 0:
            failures.append(f"parent_text chunks have embeddings: {parent_emb_count}")

        print("\n## Result")
        if failures:
            print("FAIL")
            for failure in failures:
                print(f"- {failure}")
            return 1 if args.strict else 0
        print("PASS")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate parent-child chunk relationships")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--document-id", help="Document id to validate")
    group.add_argument("--kb", help="Knowledge base id to validate")
    group.add_argument("--all", action="store_true", help="Validate all chunks")
    parser.add_argument("--limit", type=int, default=10, help="Rows to show in parent distribution")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when validation fails")
    args = parser.parse_args()
    raise SystemExit(validate(args))


if __name__ == "__main__":
    main()
