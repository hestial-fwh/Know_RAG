"""parse workbench phase z

Revision ID: 0008_parse_workbench_phase_z
Revises: 0007_retrieval_rrf_phase_d
Create Date: 2026-06-08
"""

from alembic import op


revision = "0008_parse_workbench_phase_z"
down_revision = "0007_retrieval_rrf_phase_d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS parse_tasks (
            id VARCHAR(36) PRIMARY KEY,
            document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            knowledge_base_id VARCHAR(36) NOT NULL,
            task_type VARCHAR(50) NOT NULL DEFAULT 'parse',
            status VARCHAR(50) NOT NULL DEFAULT 'queued',
            current_stage VARCHAR(100) NOT NULL DEFAULT 'queued',
            progress DOUBLE PRECISION NOT NULL DEFAULT 0,
            total_steps INTEGER NOT NULL DEFAULT 0,
            completed_steps INTEGER NOT NULL DEFAULT 0,
            stats JSON NOT NULL DEFAULT '{}'::json,
            error_message TEXT NOT NULL DEFAULT '',
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            created_by VARCHAR(36) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS parse_task_events (
            id VARCHAR(36) PRIMARY KEY,
            task_id VARCHAR(36) NOT NULL REFERENCES parse_tasks(id) ON DELETE CASCADE,
            document_id VARCHAR(36) NOT NULL,
            seq_no INTEGER NOT NULL DEFAULT 0,
            event_type VARCHAR(80) NOT NULL,
            stage VARCHAR(100) NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            payload_json JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_parse_tasks_document_id ON parse_tasks(document_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_parse_tasks_knowledge_base_id ON parse_tasks(knowledge_base_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_parse_task_events_task_seq ON parse_task_events(task_id, seq_no)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_parse_task_events_document_id ON parse_task_events(document_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_parse_task_events_document_id")
    op.execute("DROP INDEX IF EXISTS idx_parse_task_events_task_seq")
    op.execute("DROP INDEX IF EXISTS idx_parse_tasks_knowledge_base_id")
    op.execute("DROP INDEX IF EXISTS idx_parse_tasks_document_id")
    op.execute("DROP TABLE IF EXISTS parse_task_events")
    op.execute("DROP TABLE IF EXISTS parse_tasks")
