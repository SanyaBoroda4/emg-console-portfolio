"""Initial schema: review_items + payment_details.

STAGE1_BUILD_PLAN.md §6, with the three payment_details columns added by
STAGE1_ADDENDUM_FIELD_MAPPING.md (check_number, caption_name, date_received).

Revision ID: 0001
Revises:
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # For gen_random_uuid() server-side UUID defaults.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "review_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("item_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("airtable_id", sa.Text(), nullable=True),
        sa.Column("photo_drive_url", sa.Text(), nullable=True),
        sa.Column("matched_job_id", sa.Text(), nullable=True),
        sa.Column("matched_job_name", sa.Text(), nullable=True),
        sa.Column("moraware_url", sa.Text(), nullable=True),
        sa.Column("match_method", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # The board's main query.
    op.create_index(
        "ix_review_items_item_type_status", "review_items", ["item_type", "status"]
    )
    # The mirror's idempotent upsert key.
    op.create_index(
        "ix_review_items_airtable_id", "review_items", ["airtable_id"], unique=True
    )

    op.create_table(
        "payment_details",
        sa.Column(
            "review_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("payment_method", sa.Text(), nullable=True),
        sa.Column("payment_type", sa.Text(), nullable=True),
        sa.Column("payer_name", sa.Text(), nullable=True),
        sa.Column("invoice_number", sa.Text(), nullable=True),
        sa.Column("txn_date", sa.Date(), nullable=True),
        sa.Column("check_number", sa.Text(), nullable=True),
        sa.Column("caption_name", sa.Text(), nullable=True),
        sa.Column("date_received", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("payment_details")
    op.drop_index("ix_review_items_airtable_id", table_name="review_items")
    op.drop_index("ix_review_items_item_type_status", table_name="review_items")
    op.drop_table("review_items")
