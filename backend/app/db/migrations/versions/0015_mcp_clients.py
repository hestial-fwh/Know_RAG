"""add mcp_clients for MCP signed access management

Revision ID: 0015_mcp_clients
Revises: 0014_backfill_relation_chunks
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_mcp_clients"
down_revision = "0014_backfill_relation_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_clients (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            client_id VARCHAR(64) NOT NULL UNIQUE,
            client_secret_encrypted VARCHAR(512) NOT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT true,
            expires_at TIMESTAMPTZ,
            scopes JSON NOT NULL DEFAULT '[]'::json,
            description TEXT NOT NULL DEFAULT '',
            last_used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_mcp_clients_client_id ON mcp_clients (client_id)
        """
    )


def downgrade() -> None:
    op.drop_table("mcp_clients")
