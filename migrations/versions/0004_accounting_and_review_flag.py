"""llm_calls efficiency columns (A6), tasks.require_final_approval (FR-40), runs.active_seconds
(FR-63: the time cap counts active time only, so it must survive pauses and worker changes).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("requested_model", sa.Text(), nullable=True))
    op.add_column("llm_calls", sa.Column("purpose", sa.Text(), nullable=True))
    op.add_column("llm_calls", sa.Column("subtask_key", sa.Text(), nullable=True))
    op.add_column(
        "llm_calls", sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False)
    )
    op.add_column(
        "llm_calls",
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "runs",
        sa.Column("active_seconds", sa.REAL(), server_default=sa.text("0"), nullable=False),
    )
    # Replay (SPEC 19.4): the operator's state patch, applied by the replay job.
    op.add_column("runs", sa.Column("replay_patch", postgresql.JSONB(), nullable=True))
    op.add_column("runs", sa.Column("replay_as_node", sa.Text(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column(
            "require_final_approval", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "require_final_approval")
    op.drop_column("runs", "active_seconds")
    op.drop_column("runs", "replay_as_node")
    op.drop_column("runs", "replay_patch")
    for column in ("revision", "attempt", "subtask_key", "purpose", "requested_model"):
        op.drop_column("llm_calls", column)
