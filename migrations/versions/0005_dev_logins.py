"""dev_logins: username/password logins for AUTH_MODE=dev (hashed).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dev_logins",
        sa.Column("username", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.Text()), nullable=False),
    )
    # Default privileges from 0001 cover orchestrix_app on tables created by the migrator.


def downgrade() -> None:
    op.drop_table("dev_logins")
