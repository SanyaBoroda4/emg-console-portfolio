"""Add audit_log table and review_items.last_edited_* (Stage 3).

The plan numbered this 0004, but 0004 was taken by the Oleksandr roster
addition after the plan was drafted — same content, next number.

audit_log has deliberately NO foreign key to review_items: edit/delete
history must survive row deletion.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-11

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("review_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_label", sa.Text(), nullable=False),
        sa.Column("actor_email", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("action IN ('edit','delete')", name="ck_audit_log_action"),
    )
    op.create_index("ix_audit_log_review_item_id", "audit_log", ["review_item_id"])

    op.add_column(
        "review_items",
        sa.Column("last_edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("review_items", sa.Column("last_edited_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_items", "last_edited_by")
    op.drop_column("review_items", "last_edited_at")
    op.drop_index("ix_audit_log_review_item_id", table_name="audit_log")
    op.drop_table("audit_log")
