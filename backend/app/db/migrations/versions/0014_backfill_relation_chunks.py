"""backfill relation_chunks from metadata_json binding data

Revision ID: 0014_backfill_relation_chunks
Revises: 0013_rerank_enabled
Create Date: 2026-08-06
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "0014_backfill_relation_chunks"
down_revision = "0013_rerank_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # metadata_json is json (not jsonb) — cast to jsonb so the ? operator works
    rows = conn.execute(
        sa.text(
            """
            SELECT id, metadata_json FROM chunks
            WHERE metadata_json::jsonb ? 'figure_references'
               OR metadata_json::jsonb ? 'outgoing_asset_chunk_ids'
               OR metadata_json::jsonb ? 'image_bindings'
               OR metadata_json::jsonb ? 'incoming_reference_chunk_ids'
            """
        )
    ).fetchall()

    if not rows:
        return

    updated = 0
    for chunk_id, metadata in rows:
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(metadata, dict):
            continue

        related: list[str] = []

        # figure_references[*].image_chunk_ids[*]
        for ref in metadata.get("figure_references") or []:
            if isinstance(ref, dict):
                for img_id in ref.get("image_chunk_ids") or []:
                    if isinstance(img_id, str) and img_id:
                        related.append(img_id)

        # outgoing_asset_chunk_ids
        for asset_id in metadata.get("outgoing_asset_chunk_ids") or []:
            if isinstance(asset_id, str) and asset_id:
                related.append(asset_id)

        # image_bindings[*].reference_chunk_ids[*]
        for binding in metadata.get("image_bindings") or []:
            if isinstance(binding, dict):
                for ref_id in binding.get("reference_chunk_ids") or []:
                    if isinstance(ref_id, str) and ref_id:
                        related.append(ref_id)

        # incoming_reference_chunk_ids
        for ref_id in metadata.get("incoming_reference_chunk_ids") or []:
            if isinstance(ref_id, str) and ref_id:
                related.append(ref_id)

        # Stable dedup, exclude self-reference
        deduped = list(dict.fromkeys(related))
        result = [cid for cid in deduped if cid != chunk_id]

        conn.execute(
            sa.text("UPDATE chunks SET relation_chunks = CAST(:rel AS json) WHERE id = :id"),
            {"rel": json.dumps(result, ensure_ascii=False), "id": chunk_id},
        )
        updated += 1

    # Force a no-op schema change so Alembic records the migration even when
    # zero rows are updated (fresh install).
    op.execute(
        sa.text(
            "COMMENT ON COLUMN chunks.relation_chunks IS 'Direct relation chunk IDs from binding materialization'"
        )
    )
    print(f"relation_chunks backfill: {updated} chunks updated")


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE chunks SET relation_chunks = '[]'::json WHERE relation_chunks IS NULL OR jsonb_array_length(relation_chunks::jsonb) > 0")
    )
