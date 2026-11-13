"""知识库检索策略；已有记录保持 legacy_v1。"""

from alembic import op
import sqlalchemy as sa


revision = "0016_kb_retrieval_profile"
down_revision = "0015_mcp_clients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_bases", sa.Column(
        "retrieval_profile", sa.JSON(), nullable=False,
        server_default=sa.text("'{\"version\":\"legacy_v1\"}'"),
    ))


def downgrade() -> None:
    op.drop_column("knowledge_bases", "retrieval_profile")
