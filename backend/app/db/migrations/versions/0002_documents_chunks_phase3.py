"""documents and chunks phase 3

Revision ID: 0002_documents_chunks_phase3
Revises: 0001_initial
Create Date: 2026-05-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_documents_chunks_phase3"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def table_exists(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def column_names(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def add_column_if_missing(inspector, table: str, column: sa.Column) -> None:
    if column.name not in column_names(inspector, table):
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not table_exists(inspector, "documents"):
        op.create_table(
            "documents",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("file_type", sa.String(length=64), nullable=False),
            sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("file_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("storage_path", sa.Text(), nullable=False, server_default=""),
            sa.Column("parse_status", sa.String(length=50), nullable=False, server_default="pending"),
            sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        )
    else:
        add_column_if_missing(inspector, "documents", sa.Column("knowledge_base_id", sa.String(length=36), nullable=True))
        add_column_if_missing(inspector, "documents", sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"))
        add_column_if_missing(inspector, "documents", sa.Column("file_hash", sa.String(length=64), nullable=False, server_default=""))
        add_column_if_missing(inspector, "documents", sa.Column("storage_path", sa.Text(), nullable=False, server_default=""))
        add_column_if_missing(inspector, "documents", sa.Column("error_message", sa.Text(), nullable=False, server_default=""))
        add_column_if_missing(inspector, "documents", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
        add_column_if_missing(inspector, "documents", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
        bind.execute(sa.text("UPDATE documents SET knowledge_base_id = kb_id WHERE knowledge_base_id IS NULL AND kb_id IS NOT NULL"))
        bind.execute(sa.text("UPDATE documents SET storage_path = file_path WHERE storage_path = '' AND file_path IS NOT NULL"))
        bind.execute(sa.text("UPDATE documents SET file_name = COALESCE(file_name, title, '') WHERE file_name IS NULL"))
        bind.execute(sa.text("UPDATE documents SET file_type = COALESCE(file_type, '') WHERE file_type IS NULL"))
        bind.execute(sa.text("UPDATE documents SET title = COALESCE(title, file_name, '') WHERE title IS NULL"))
        if "kb_id" in column_names(inspector, "documents"):
            op.alter_column("documents", "kb_id", existing_type=sa.String(length=36), nullable=True)

    inspector = sa.inspect(bind)
    existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("documents")}
    if "fk_documents_knowledge_base_id" not in existing_fks:
        op.create_foreign_key(
            "fk_documents_knowledge_base_id",
            "documents",
            "knowledge_bases",
            ["knowledge_base_id"],
            ["id"],
            ondelete="CASCADE",
        )
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("documents")}
    if "ix_documents_knowledge_base_id" not in existing_indexes:
        op.create_index("ix_documents_knowledge_base_id", "documents", ["knowledge_base_id"])

    inspector = sa.inspect(bind)
    if not table_exists(inspector, "chunks"):
        op.create_table(
            "chunks",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("document_id", sa.String(length=36), nullable=False),
            sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="ready"),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        )
    else:
        add_column_if_missing(inspector, "chunks", sa.Column("knowledge_base_id", sa.String(length=36), nullable=True))
        add_column_if_missing(inspector, "chunks", sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"))
        add_column_if_missing(inspector, "chunks", sa.Column("status", sa.String(length=50), nullable=False, server_default="ready"))
        add_column_if_missing(inspector, "chunks", sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
        add_column_if_missing(inspector, "chunks", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
        add_column_if_missing(inspector, "chunks", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
        bind.execute(sa.text("UPDATE chunks SET knowledge_base_id = kb_id WHERE knowledge_base_id IS NULL AND kb_id IS NOT NULL"))
        bind.execute(sa.text("UPDATE chunks SET token_count = LENGTH(content) WHERE token_count = 0 AND content IS NOT NULL"))
        if "kb_id" in column_names(inspector, "chunks"):
            op.alter_column("chunks", "kb_id", existing_type=sa.String(length=36), nullable=True)

    inspector = sa.inspect(bind)
    chunk_fks = {fk["name"] for fk in inspector.get_foreign_keys("chunks")}
    if "fk_chunks_document_id" not in chunk_fks:
        op.create_foreign_key(
            "fk_chunks_document_id",
            "chunks",
            "documents",
            ["document_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if "fk_chunks_knowledge_base_id" not in chunk_fks:
        op.create_foreign_key(
            "fk_chunks_knowledge_base_id",
            "chunks",
            "knowledge_bases",
            ["knowledge_base_id"],
            ["id"],
            ondelete="CASCADE",
        )
    chunk_indexes = {idx["name"] for idx in inspector.get_indexes("chunks")}
    if "ix_chunks_document_id" not in chunk_indexes:
        op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    if "ix_chunks_knowledge_base_id" not in chunk_indexes:
        op.create_index("ix_chunks_knowledge_base_id", "chunks", ["knowledge_base_id"])


def downgrade() -> None:
    op.drop_index("ix_chunks_knowledge_base_id", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_index("ix_documents_knowledge_base_id", table_name="documents")
