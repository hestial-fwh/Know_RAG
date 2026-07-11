"""embedding and retrieval phase 4

Revision ID: 0003_embedding_retrieval_phase4
Revises: 0002_documents_chunks_phase3
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0003_embedding_retrieval_phase4"
down_revision = "0002_documents_chunks_phase3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pg_trgm extension is not available; keyword search will use ILIKE fallback';
        END $$;
        """
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chunk_embeddings" not in inspector.get_table_names():
        op.create_table(
            "chunk_embeddings",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("chunk_id", sa.String(length=36), nullable=False),
            sa.Column("document_id", sa.String(length=36), nullable=False),
            sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
            sa.Column("embedding", Vector(1024), nullable=False),
            sa.Column("model_name", sa.String(length=128), nullable=False),
            sa.Column("dimension", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        )
    inspector = sa.inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("chunk_embeddings")}
    if "ix_chunk_embeddings_chunk_id" not in indexes:
        op.create_index("ix_chunk_embeddings_chunk_id", "chunk_embeddings", ["chunk_id"], unique=True)
    if "ix_chunk_embeddings_document_id" not in indexes:
        op.create_index("ix_chunk_embeddings_document_id", "chunk_embeddings", ["document_id"])
    if "ix_chunk_embeddings_knowledge_base_id" not in indexes:
        op.create_index("ix_chunk_embeddings_knowledge_base_id", "chunk_embeddings", ["knowledge_base_id"])
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_embedding
        ON chunk_embeddings
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_embedding")
    op.drop_index("ix_chunk_embeddings_knowledge_base_id", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_document_id", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_chunk_id", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
