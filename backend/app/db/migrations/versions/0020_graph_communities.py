"""P4 KB级图任务、社区和有支持来源的派生报告。"""
from alembic import op
import sqlalchemy as sa

revision = "0020_graph_communities"
down_revision = "0019_relation_event_retrieval"
branch_labels = None
depends_on = None


def _kb():
    return sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)


def upgrade():
    op.create_table("knowledge_graph_jobs", sa.Column("id", sa.String(36), primary_key=True), _kb(),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("input_json", sa.JSON(), nullable=False), sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("lease_token", sa.String(36), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)), sa.Column("error", sa.Text(), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("graph_communities", sa.Column("id", sa.String(36), primary_key=True), _kb(),
        sa.Column("graph_revision", sa.String(36), sa.ForeignKey("knowledge_graph_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("community_key", sa.String(128), nullable=False), sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("members_json", sa.JSON(), nullable=False), sa.Column("sources_json", sa.JSON(), nullable=False),
        sa.Column("provider_json", sa.JSON(), nullable=False))
    op.create_table("community_reports", sa.Column("id", sa.String(36), primary_key=True), _kb(),
        sa.Column("community_id", sa.String(36), sa.ForeignKey("graph_communities.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("graph_revision", sa.String(36), sa.ForeignKey("knowledge_graph_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False), sa.Column("findings_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False))
    for table in ("knowledge_graph_jobs", "graph_communities", "community_reports"):
        op.create_index(f"ix_{table}_knowledge_base_id", table, ["knowledge_base_id"])
    op.create_index("ix_knowledge_graph_jobs_state", "knowledge_graph_jobs", ["state"])
    for table in ("graph_communities", "community_reports"):
        op.create_index(f"ix_{table}_graph_revision", table, ["graph_revision"])


def downgrade():
    for table in ("community_reports", "graph_communities", "knowledge_graph_jobs"):
        op.drop_table(table)
