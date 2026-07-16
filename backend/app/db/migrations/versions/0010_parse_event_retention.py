"""stop retaining parse process events

Revision ID: 0010_parse_event_retention
Revises: 0009_parse_eval_phase_aa
Create Date: 2026-07-15
"""

from alembic import op


revision = "0010_parse_event_retention"
down_revision = "0009_parse_eval_phase_aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Events are transient UI progress signals. Existing rows duplicate final
    # document/chunk data and are removed during this retention-policy change.
    op.execute("DELETE FROM parse_task_events")


def downgrade() -> None:
    # Deleted transient events cannot and should not be reconstructed.
    pass
