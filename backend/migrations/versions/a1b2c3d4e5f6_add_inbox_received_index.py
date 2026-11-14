"""add composite index for inbox_id and received_at

Revision ID: a1b2c3d4e5f6
Revises: 4111147a610e
Create Date: 2026-11-14 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "4111147a610e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_emails_inbox_received_at",
        "emails",
        ["inbox_id", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_emails_inbox_received_at", table_name="emails")
