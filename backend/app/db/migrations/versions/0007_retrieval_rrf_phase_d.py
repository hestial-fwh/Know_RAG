"""add retrieval rrf config

Revision ID: 0007_retrieval_rrf_phase_d
Revises: 0006_chunker_config
Create Date: 2026-05-27
"""

from alembic import op


revision = "0007_retrieval_rrf_phase_d"
down_revision = "0006_chunker_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE retrieval_config ADD COLUMN IF NOT EXISTS fusion_strategy VARCHAR(50) NOT NULL DEFAULT 'rrf'")
    op.execute("ALTER TABLE retrieval_config ADD COLUMN IF NOT EXISTS rrf_k INTEGER NOT NULL DEFAULT 60")
    op.execute("ALTER TABLE retrieval_config ADD COLUMN IF NOT EXISTS rrf_vector_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0")
    op.execute("ALTER TABLE retrieval_config ADD COLUMN IF NOT EXISTS rrf_keyword_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0")
    op.execute("ALTER TABLE retrieval_config ADD COLUMN IF NOT EXISTS include_context BOOLEAN NOT NULL DEFAULT true")
    op.execute("UPDATE retrieval_config SET fusion_strategy = 'rrf' WHERE fusion_strategy IS NULL OR fusion_strategy = ''")
    op.execute("UPDATE retrieval_config SET rrf_k = 60 WHERE rrf_k IS NULL OR rrf_k <= 0")
    op.execute("UPDATE retrieval_config SET rrf_vector_weight = 1.0 WHERE rrf_vector_weight IS NULL OR rrf_vector_weight <= 0")
    op.execute("UPDATE retrieval_config SET rrf_keyword_weight = 1.0 WHERE rrf_keyword_weight IS NULL OR rrf_keyword_weight <= 0")
    op.execute("UPDATE retrieval_config SET include_context = true WHERE include_context IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE retrieval_config DROP COLUMN IF EXISTS include_context")
    op.execute("ALTER TABLE retrieval_config DROP COLUMN IF EXISTS rrf_keyword_weight")
    op.execute("ALTER TABLE retrieval_config DROP COLUMN IF EXISTS rrf_vector_weight")
    op.execute("ALTER TABLE retrieval_config DROP COLUMN IF EXISTS rrf_k")
    op.execute("ALTER TABLE retrieval_config DROP COLUMN IF EXISTS fusion_strategy")
