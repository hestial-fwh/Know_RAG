"""Purge knowledge bases before rebuilding under a clean retrieval family.

This is a one-off maintenance tool. It removes:
- external vector entries for every chunk (Qdrant/Milvus), when configured;
- MinIO objects and parsed cache under the KB prefix;
- Elasticsearch keyword documents;
- the knowledge base row; PostgreSQL cascades remove documents, chunks,
  embeddings, entities, relations, events and communities.

Usage inside the backend container:
    python scripts/purge_knowledge_bases.py --all --yes
    python scripts/purge_knowledge_bases.py --kb <id> --yes
    python scripts/purge_knowledge_bases.py --all --yes --force

Stop the index worker before running so no job recreates deleted artifacts.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.db.models import (  # noqa: E402
    Chunk,
    KnowledgeBase,
    KnowledgeEntity,
    KnowledgeEvent,
    KnowledgeIndexJob,
    KnowledgeRelation,
)
from app.db.session import SessionLocal  # noqa: E402
from app.services.keyword_index_service import delete_knowledge_base_from_index  # noqa: E402
from app.services.storage import delete_knowledge_base_cache, delete_knowledge_base_objects  # noqa: E402
from app.services.vector_store_adapters import delete_external_vector  # noqa: E402


def _active_jobs(db, kb_id: str) -> int:
    return db.scalar(
        select(func.count()).select_from(KnowledgeIndexJob).where(
            KnowledgeIndexJob.knowledge_base_id == kb_id,
            KnowledgeIndexJob.state.in_(("pending", "running")),
        )
    ) or 0


def _purge_one(db, kb: KnowledgeBase) -> None:
    # 先清外部派生数据（向量/MinIO/ES/缓存），再删 KB 行；chunks/entities/relations
    # 等由数据库级联删除，避免留下跨存储的孤儿数据。
    chunk_ids = db.scalars(select(Chunk.id).where(Chunk.knowledge_base_id == kb.id)).all()
    for chunk_id in chunk_ids:
        delete_external_vector(db, chunk_id)
    delete_knowledge_base_objects(kb.id)
    delete_knowledge_base_from_index(kb.id)
    delete_knowledge_base_cache(kb.id)
    db.delete(kb)
    db.commit()


def _verify(db, kb_id: str) -> dict[str, int]:
    return {
        "knowledge_base": db.scalar(
            select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        ) or 0,
        "chunks": db.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.knowledge_base_id == kb_id)
        ) or 0,
        "entities": db.scalar(
            select(func.count()).select_from(KnowledgeEntity).where(KnowledgeEntity.knowledge_base_id == kb_id)
        ) or 0,
        "relations": db.scalar(
            select(func.count()).select_from(KnowledgeRelation).where(KnowledgeRelation.knowledge_base_id == kb_id)
        ) or 0,
        "events": db.scalar(
            select(func.count()).select_from(KnowledgeEvent).where(KnowledgeEvent.knowledge_base_id == kb_id)
        ) or 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge knowledge bases before clean rebuild")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--kb", help="Knowledge base id to purge")
    group.add_argument("--all", action="store_true", help="Purge every knowledge base")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation")
    parser.add_argument("--force", action="store_true", help="Proceed even when index jobs are active")
    args = parser.parse_args()

    if not args.yes:
        target = args.kb or "ALL knowledge bases"
        answer = input(f"Purge {target}? This cannot be undone. Type PURGE to continue: ").strip()
        if answer != "PURGE":
            print("Cancelled")
            return

    with SessionLocal() as db:
        if args.kb:
            kb = db.get(KnowledgeBase, args.kb)
            if kb is None:
                raise SystemExit(f"knowledge base not found: {args.kb}")
            kbs = [kb]
        else:
            kbs = list(db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.created_at.asc())).all())

        for kb in kbs:
            active = _active_jobs(db, kb.id)
            if active and not args.force:
                raise SystemExit(
                    f"kb={kb.id} has {active} active index jobs; stop the worker or pass --force"
                )
            _purge_one(db, kb)
            checks = _verify(db, kb.id)
            leftovers = {key: value for key, value in checks.items() if value}
            print(f"purged kb={kb.id} name={kb.name!r} leftovers={leftovers or 'none'}")

        print(f"done, knowledge_bases={len(kbs)}")


if __name__ == "__main__":
    main()