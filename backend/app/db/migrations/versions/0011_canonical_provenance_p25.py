"""persist canonical provenance artifacts

Revision ID: 0011_canonical_provenance_p25
Revises: 0010_parse_event_retention
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_canonical_provenance_p25"
down_revision = "0010_parse_event_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_canonical_artifacts",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("parse_revision", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "algorithm_version",
            sa.String(length=64),
            nullable=False,
            server_default="p2.5-v1",
        ),
        sa.Column("source_format", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("canonical_hash", sa.String(length=64), nullable=False),
        sa.Column("canonical_markdown", sa.Text(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("assets_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("figure_links_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("stats_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index(
        "ix_document_canonical_artifacts_knowledge_base_id",
        "document_canonical_artifacts",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_document_canonical_artifacts_canonical_hash",
        "document_canonical_artifacts",
        ["canonical_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_canonical_artifacts_canonical_hash",
        table_name="document_canonical_artifacts",
    )
    op.drop_index(
        "ix_document_canonical_artifacts_knowledge_base_id",
        table_name="document_canonical_artifacts",
    )
    op.drop_table("document_canonical_artifacts")
