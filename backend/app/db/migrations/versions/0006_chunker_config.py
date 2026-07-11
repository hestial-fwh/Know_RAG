"""add chunker config

Revision ID: 0006_chunker_config
Revises: 0005_weknora_alignment_phase_a
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_chunker_config"
down_revision = "0005_weknora_alignment_phase_a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chunker_config (
            id VARCHAR(36) PRIMARY KEY,
            strategy VARCHAR(50) NOT NULL DEFAULT 'auto',
            chunk_size INTEGER NOT NULL DEFAULT 512,
            chunk_overlap INTEGER NOT NULL DEFAULT 80,
            separators JSON NOT NULL DEFAULT '[]'::json,
            enable_parent_child BOOLEAN NOT NULL DEFAULT false,
            parent_chunk_size INTEGER NOT NULL DEFAULT 4096,
            child_chunk_size INTEGER NOT NULL DEFAULT 384,
            child_chunk_overlap INTEGER NOT NULL DEFAULT 64,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.drop_table("chunker_config")
