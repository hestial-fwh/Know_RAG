"""Rebuild Elasticsearch BM25 keyword index from existing DB chunks.

Usage inside backend container:
    python scripts/rebuild_keyword_index.py --kb <knowledge_base_id>
    python scripts/rebuild_keyword_index.py --all
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.models import KnowledgeBase  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.keyword_index_service import rebuild_index_for_knowledge_base, ensure_index  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild Elasticsearch BM25 keyword index")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--kb", help="Knowledge base id to rebuild")
    group.add_argument("--all", action="store_true", help="Rebuild all knowledge bases")
    args = parser.parse_args()

    ensure_index()
    with SessionLocal() as db:
        if args.kb:
            total = rebuild_index_for_knowledge_base(db, args.kb)
            print(f"Rebuilt keyword index for kb={args.kb}, chunks={total}")
            return
        kb_ids = [row.id for row in db.query(KnowledgeBase).order_by(KnowledgeBase.created_at.asc()).all()]
        grand_total = 0
        for kb_id in kb_ids:
            total = rebuild_index_for_knowledge_base(db, kb_id)
            grand_total += total
            print(f"Rebuilt keyword index for kb={kb_id}, chunks={total}")
        print(f"Done, knowledge_bases={len(kb_ids)}, chunks={grand_total}")


if __name__ == "__main__":
    main()
