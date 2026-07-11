"""parse evaluation plugin phase aa

Revision ID: 0009_parse_eval_phase_aa
Revises: 0008_parse_workbench_phase_z
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_parse_eval_phase_aa"
down_revision = "0008_parse_workbench_phase_z"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parse_eval_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=64), nullable=False, server_default="no_gt"),
        sa.Column("template_id", sa.String(length=128), nullable=False, server_default="generic_no_gt"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="completed"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("risk_level", sa.String(length=50), nullable=False, server_default="unknown"),
        sa.Column("recommendation", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("score_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("options_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("report_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_parse_eval_runs_document_id", "parse_eval_runs", ["document_id"])
    op.create_index("ix_parse_eval_runs_knowledge_base_id", "parse_eval_runs", ["knowledge_base_id"])

    op.create_table(
        "parse_eval_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("evaluation_id", sa.String(length=36), sa.ForeignKey("parse_eval_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False, server_default="chunk"),
        sa.Column("target_id", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("issue_level", sa.String(length=50), nullable=False, server_default="info"),
        sa.Column("issue_type", sa.String(length=100), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("suggestion", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_parse_eval_items_evaluation_id", "parse_eval_items", ["evaluation_id"])
    op.create_index("ix_parse_eval_items_document_id", "parse_eval_items", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_parse_eval_items_document_id", table_name="parse_eval_items")
    op.drop_index("ix_parse_eval_items_evaluation_id", table_name="parse_eval_items")
    op.drop_table("parse_eval_items")
    op.drop_index("ix_parse_eval_runs_knowledge_base_id", table_name="parse_eval_runs")
    op.drop_index("ix_parse_eval_runs_document_id", table_name="parse_eval_runs")
    op.drop_table("parse_eval_runs")
