"""rerank_enabled replaces rerank_model_id

Revision ID: 0013_rerank_enabled
Revises: 0012_document_relation_graph
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_rerank_enabled"
down_revision = "0012_document_relation_graph"
branch_labels = None
depends_on = None


def upgrade():
    # Drop old string default first, then convert type, then set new default
    op.execute(sa.text("ALTER TABLE retrieval_config ALTER COLUMN rerank_model_id DROP DEFAULT"))
    op.execute(
        sa.text(
            "ALTER TABLE retrieval_config ALTER COLUMN rerank_model_id "
            "SET DATA TYPE boolean USING (rerank_model_id IS NOT NULL AND rerank_model_id != '')"
        )
    )
    op.execute(sa.text("ALTER TABLE retrieval_config ALTER COLUMN rerank_model_id SET NOT NULL"))
    op.execute(sa.text("ALTER TABLE retrieval_config ALTER COLUMN rerank_model_id SET DEFAULT false"))
    op.alter_column("retrieval_config", "rerank_model_id", new_column_name="rerank_enabled")


def downgrade():
    op.alter_column("retrieval_config", "rerank_enabled", new_column_name="rerank_model_id")
    op.execute(sa.text("ALTER TABLE retrieval_config ALTER COLUMN rerank_model_id DROP DEFAULT"))
    op.execute(
        sa.text(
            "ALTER TABLE retrieval_config ALTER COLUMN rerank_model_id "
            "SET DATA TYPE varchar(128) USING CASE WHEN rerank_model_id THEN 'enabled' ELSE '' END"
        )
    )
    op.execute(sa.text("ALTER TABLE retrieval_config ALTER COLUMN rerank_model_id SET NOT NULL"))
    op.execute(sa.text("ALTER TABLE retrieval_config ALTER COLUMN rerank_model_id SET DEFAULT ''"))
