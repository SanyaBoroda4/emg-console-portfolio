"""Roster change: promote Oleksandr (owner-phone@example.com) yard -> manager.

Idempotent by nature: the UPDATE sets an absolute role, so re-running lands on
the same state. Scoped to WHERE email = ... so it touches only this one row.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-12

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users SET role = 'manager'
        WHERE email = 'owner-phone@example.com'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE users SET role = 'yard'
        WHERE email = 'owner-phone@example.com'
        """
    )
