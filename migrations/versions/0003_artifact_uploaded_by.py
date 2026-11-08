"""artifacts.uploaded_by: only the uploader can attach an upload to a task (FR-02).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("uploaded_by", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "artifacts_uploaded_by_fkey", "artifacts", "users", ["uploaded_by"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("artifacts_uploaded_by_fkey", "artifacts", type_="foreignkey")
    op.drop_column("artifacts", "uploaded_by")
