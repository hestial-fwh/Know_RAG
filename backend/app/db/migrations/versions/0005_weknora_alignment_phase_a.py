"""weknora alignment phase a

Revision ID: 0005_weknora_alignment_phase_a
Revises: 0004_ensure_chunk_embeddings
Create Date: 2026-05-27

The added compatibility fields intentionally do not add foreign keys for
knowledge_id, parent_chunk_id, pre_chunk_id, or next_chunk_id. Phase A keeps
existing data safe and lets the application maintain these relationships.
"""

from alembic import op


revision = "0005_weknora_alignment_phase_a"
down_revision = "0004_ensure_chunk_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS source VARCHAR DEFAULT 'file',
            ADD COLUMN IF NOT EXISTS channel VARCHAR DEFAULT 'local',
            ADD COLUMN IF NOT EXISTS metadata_json JSON DEFAULT '{}'::json,
            ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP WITH TIME ZONE,
            ADD COLUMN IF NOT EXISTS enable_status VARCHAR DEFAULT 'enabled';
        """
    )
    op.execute(
        """
        ALTER TABLE chunks
            ADD COLUMN IF NOT EXISTS knowledge_id VARCHAR,
            ADD COLUMN IF NOT EXISTS start_at INTEGER,
            ADD COLUMN IF NOT EXISTS end_at INTEGER,
            ADD COLUMN IF NOT EXISTS pre_chunk_id VARCHAR,
            ADD COLUMN IF NOT EXISTS next_chunk_id VARCHAR,
            ADD COLUMN IF NOT EXISTS chunk_type VARCHAR DEFAULT 'text',
            ADD COLUMN IF NOT EXISTS parent_chunk_id VARCHAR,
            ADD COLUMN IF NOT EXISTS content_hash VARCHAR,
            ADD COLUMN IF NOT EXISTS image_info JSON DEFAULT '{}'::json,
            ADD COLUMN IF NOT EXISTS relation_chunks JSON DEFAULT '[]'::json,
            ADD COLUMN IF NOT EXISTS indirect_relation_chunks JSON DEFAULT '[]'::json,
            ADD COLUMN IF NOT EXISTS context_header TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS flags JSON DEFAULT '{}'::json;
        """
    )
    op.execute(
        """
        ALTER TABLE chunk_embeddings
            ADD COLUMN IF NOT EXISTS knowledge_id VARCHAR,
            ADD COLUMN IF NOT EXISTS source_id VARCHAR,
            ADD COLUMN IF NOT EXISTS source_type VARCHAR DEFAULT 'chunk',
            ADD COLUMN IF NOT EXISTS content TEXT,
            ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN DEFAULT true;
        """
    )

    op.execute("UPDATE chunks SET knowledge_id = document_id WHERE knowledge_id IS NULL")
    op.execute("UPDATE chunks SET chunk_type = 'text' WHERE chunk_type IS NULL")
    op.execute("UPDATE chunks SET context_header = '' WHERE context_header IS NULL")
    op.execute("UPDATE chunks SET image_info = '{}'::json WHERE image_info IS NULL")
    op.execute("UPDATE chunks SET relation_chunks = '[]'::json WHERE relation_chunks IS NULL")
    op.execute("UPDATE chunks SET indirect_relation_chunks = '[]'::json WHERE indirect_relation_chunks IS NULL")
    op.execute("UPDATE chunks SET flags = '{}'::json WHERE flags IS NULL")

    op.execute("UPDATE chunk_embeddings SET knowledge_id = document_id WHERE knowledge_id IS NULL")
    op.execute("UPDATE chunk_embeddings SET source_id = chunk_id WHERE source_id IS NULL")
    op.execute("UPDATE chunk_embeddings SET source_type = 'chunk' WHERE source_type IS NULL")
    op.execute("UPDATE chunk_embeddings SET is_enabled = true WHERE is_enabled IS NULL")
    op.execute(
        """
        UPDATE chunk_embeddings ce
        SET content = c.content
        FROM chunks c
        WHERE ce.chunk_id = c.id AND ce.content IS NULL
        """
    )

    op.execute("UPDATE documents SET source = 'file' WHERE source IS NULL")
    op.execute("UPDATE documents SET channel = 'local' WHERE channel IS NULL")
    op.execute("UPDATE documents SET metadata_json = '{}'::json WHERE metadata_json IS NULL")
    op.execute("UPDATE documents SET enable_status = 'enabled' WHERE enable_status IS NULL")

    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_knowledge_id ON chunks(knowledge_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_chunk_type ON chunks(chunk_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_parent_chunk_id ON chunks(parent_chunk_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_pre_chunk_id ON chunks(pre_chunk_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_next_chunk_id ON chunks(next_chunk_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_knowledge_id ON chunk_embeddings(knowledge_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_source_id ON chunk_embeddings(source_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_source_type ON chunk_embeddings(source_type)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunk_embeddings_source_type")
    op.execute("DROP INDEX IF EXISTS idx_chunk_embeddings_source_id")
    op.execute("DROP INDEX IF EXISTS idx_chunk_embeddings_knowledge_id")
    op.execute("DROP INDEX IF EXISTS idx_chunks_content_hash")
    op.execute("DROP INDEX IF EXISTS idx_chunks_next_chunk_id")
    op.execute("DROP INDEX IF EXISTS idx_chunks_pre_chunk_id")
    op.execute("DROP INDEX IF EXISTS idx_chunks_parent_chunk_id")
    op.execute("DROP INDEX IF EXISTS idx_chunks_chunk_type")
    op.execute("DROP INDEX IF EXISTS idx_chunks_knowledge_id")
