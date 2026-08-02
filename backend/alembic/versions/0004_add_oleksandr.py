"""Roster change: add Oleksandr (yard).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-10

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO users (email, display_name, role) VALUES
        ('owner-phone@example.com', 'Oleksandr', 'yard')
        ON CONFLICT (email) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM users WHERE email = 'owner-phone@example.com'")
