"""add canonical document relation graph

Revision ID: 0012_document_relation_graph
Revises: 0011_canonical_provenance_p25
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_document_relation_graph"
down_revision = "0011_canonical_provenance_p25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_canonical_artifacts",
        sa.Column(
            "relation_graph_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("document_canonical_artifacts", "relation_graph_json")
