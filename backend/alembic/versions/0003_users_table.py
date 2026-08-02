"""Add users roster table and seed the six known people.

STAGE2_BUILD_PLAN.md §4. The seed is idempotent (ON CONFLICT DO NOTHING),
so re-running upgrade is safe. Roster changes in this stage = a new
migration or direct SQL; there is no management UI.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-10

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('admin','manager','yard')", name="ck_users_role"),
    )
    op.execute(
        """
        INSERT INTO users (email, display_name, role) VALUES
        ('owner@example.com', 'Alex Sorokin', 'admin'),
        ('office@example.com', 'Nora Adams', 'admin'),
        ('manager1@example.com', 'Nora Bennett', 'manager'),
        ('manager2@example.com', 'Vince Cole', 'manager'),
        ('yard1@example.com', 'Wade', 'yard'),
        ('yard2@example.com', 'Sam', 'yard')
        ON CONFLICT (email) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("users")
