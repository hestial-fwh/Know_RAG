"""P2 派生索引任务、输入清单、实体和有来源的别名。"""

from alembic import op
import sqlalchemy as sa

revision = "0018_knowledge_index_foundation"
down_revision = "0017_document_file_dedup"
branch_labels = None
depends_on = None


def _scope():
    # 每次返回独立 Column，确保历史迁移不依赖运行时 ORM 模型。
    return [
        sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table("knowledge_index_jobs",
        sa.Column("id", sa.String(36), primary_key=True), *_scope(),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table("index_manifests",
        sa.Column("id", sa.String(36), sa.ForeignKey("knowledge_index_jobs.id", ondelete="CASCADE"), primary_key=True),
        *_scope(),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table("knowledge_entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("identity_key", sa.String(64), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(512), nullable=False),
        sa.UniqueConstraint("knowledge_base_id", "identity_key", name="uq_entity_identity"),
    )
    op.create_table("knowledge_evidence",
        sa.Column("id", sa.String(36), primary_key=True), *_scope(),
        sa.Column("manifest_id", sa.String(36), sa.ForeignKey("index_manifests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", sa.String(36), sa.ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parse_revision", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("start_at", sa.Integer(), nullable=False),
        sa.Column("end_at", sa.Integer(), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
    )
    op.create_table("entity_aliases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_id", sa.String(36), sa.ForeignKey("knowledge_evidence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.UniqueConstraint("entity_id", "alias", "evidence_id", name="uq_entity_alias_evidence"),
    )
    for table, columns in {
        "knowledge_index_jobs": ["knowledge_base_id", "document_id", "state"],
        "index_manifests": ["knowledge_base_id", "document_id"],
        "knowledge_entities": ["knowledge_base_id"],
        "knowledge_evidence": ["manifest_id", "entity_id", "knowledge_base_id", "document_id", "chunk_id"],
        "entity_aliases": ["entity_id", "evidence_id", "alias"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("entity_aliases", "knowledge_evidence", "knowledge_entities", "index_manifests", "knowledge_index_jobs"):
        op.drop_table(table)
