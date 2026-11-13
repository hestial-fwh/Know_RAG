"""P3 有来源关系、完整事件及事件参与者。"""
from alembic import op
import sqlalchemy as sa

revision = "0019_relation_event_retrieval"
down_revision = "0018_knowledge_index_foundation"
branch_labels = None
depends_on = None


def _source():
    return [sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("manifest_id", sa.String(36), sa.ForeignKey("index_manifests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", sa.String(36), sa.ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("start_at", sa.Integer(), nullable=False), sa.Column("end_at", sa.Integer(), nullable=False),
        sa.Column("qualifiers", sa.JSON(), nullable=False)]


def upgrade():
    op.create_table("knowledge_relations", *_source(),
        sa.Column("subject_id", sa.String(36), sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_id", sa.String(36), sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("predicate", sa.String(255), nullable=False))
    op.create_table("knowledge_events", *_source(), sa.Column("text", sa.Text(), nullable=False))
    op.create_table("event_entities",
        sa.Column("event_id", sa.String(36), sa.ForeignKey("knowledge_events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("evidence_id", sa.String(36), sa.ForeignKey("knowledge_evidence.id", ondelete="CASCADE"), nullable=False))
    for table in ("knowledge_relations", "knowledge_events"):
        for column in ("manifest_id", "knowledge_base_id", "document_id", "chunk_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    for column in ("subject_id", "object_id"):
        op.create_index(f"ix_knowledge_relations_{column}", "knowledge_relations", [column])
    op.create_index("ix_event_entities_entity_id", "event_entities", ["entity_id"])


def downgrade():
    for table in ("event_entities", "knowledge_events", "knowledge_relations"):
        op.drop_table(table)
