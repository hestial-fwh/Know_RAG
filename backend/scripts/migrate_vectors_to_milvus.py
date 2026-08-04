"""Migrate existing vectors from PostgreSQL/pgvector to Milvus.

This is a one-time migration script.  It reads all enabled embeddings from the
chunk_embeddings table and upserts them into the active Milvus collection.
pgvector data is never deleted -- it remains as the local fallback.

Usage inside backend container:
    python scripts/migrate_vectors_to_milvus.py
    python scripts/migrate_vectors_to_milvus.py --kb <knowledge_base_id>
    python scripts/migrate_vectors_to_milvus.py --dry-run
    python scripts/migrate_vectors_to_milvus.py --batch-size 100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.models import ChunkEmbedding  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.vector_store_adapters import (  # noqa: E402
    _collection_name,
    _milvus_client,
    _milvus_ensure_collection,
    get_active_vector_config,
    is_external_provider,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate vectors from pgvector to Milvus",
    )
    parser.add_argument(
        "--kb",
        help="Knowledge base id to migrate (omit for all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count embeddings without inserting",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Batch size for insertion (default: 500)",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        config = get_active_vector_config(db)
        if not is_external_provider(config) or (
            (config.provider or "").lower() not in {"milvus", "zilliz"}
        ):
            print(
                "ERROR: Active vector store provider is not Milvus. "
                "Please switch to milvus via the API or admin panel first."
            )
            sys.exit(1)

        client = _milvus_client(config)

        query = db.query(ChunkEmbedding).filter(
            ChunkEmbedding.is_enabled.is_(True),
            ChunkEmbedding.source_type == "chunk",
        )
        if args.kb:
            query = query.filter(ChunkEmbedding.knowledge_base_id == args.kb)

        embeddings = (
            query.order_by(
                ChunkEmbedding.knowledge_base_id.asc(),
                ChunkEmbedding.chunk_id.asc(),
            ).all()
        )
        total = len(embeddings)
        print(f"Found {total} enabled embeddings to migrate")

        if args.dry_run or total == 0:
            if total == 0:
                print("No embeddings to migrate.")
            else:
                print("Dry run complete. Exiting without migration.")
            return

        # Ensure collection exists using the first embedding's dimension
        dim = embeddings[0].dimension or len(embeddings[0].embedding) or 1024
        _milvus_ensure_collection(client, config, dim)
        print(f"Collection ensured with dimension={dim}")

        batch_size = args.batch_size
        migrated = 0
        errors = 0
        total_batches = (total + batch_size - 1) // batch_size

        for batch_idx in range(0, total, batch_size):
            batch = embeddings[batch_idx : batch_idx + batch_size]
            data = []
            for emb in batch:
                data.append(
                    {
                        "id": emb.chunk_id,
                        "vector": emb.embedding,
                        "chunk_id": emb.chunk_id,
                        "document_id": emb.document_id,
                        "knowledge_base_id": emb.knowledge_base_id,
                        "chunk_type": "",
                        "chunk_index": 0,
                        "content": (emb.content or "")[:6000],
                    }
                )
            try:
                client.upsert(collection_name=_collection_name(config), data=data)
                migrated += len(batch)
                current_batch = batch_idx // batch_size + 1
                print(
                    f"  Batch {current_batch}/{total_batches}: "
                    f"{migrated}/{total} migrated..."
                )
            except Exception as exc:
                errors += len(batch)
                current_batch = batch_idx // batch_size + 1
                print(
                    f"  ERROR batch {current_batch}/{total_batches}: {exc}"
                )

        print(
            f"Migration complete: migrated={migrated}, errors={errors}, "
            f"total={total}"
        )


if __name__ == "__main__":
    main()
