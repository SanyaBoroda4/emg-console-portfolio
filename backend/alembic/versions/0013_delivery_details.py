"""Slab deliveries: delivery_details — one row per slip, materials embedded
as JSONB (owner rule: one table). item_type='slab_delivery' rows in
review_items own these.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_details",
        sa.Column(
            "review_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("supplier", sa.Text(), nullable=True),
        sa.Column("supplier_confidence", sa.Text(), nullable=True),
        sa.Column("document_number", sa.Text(), nullable=True),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=True),
        sa.Column("tax", sa.Numeric(12, 2), nullable=True),
        sa.Column("total", sa.Numeric(12, 2), nullable=True),
        sa.Column("slab_count", sa.Integer(), nullable=True),
        sa.Column("hand_notes", sa.Text(), nullable=True),
        sa.Column("validation_note", sa.Text(), nullable=True),
        sa.Column("validation_ok", sa.Boolean(), nullable=True),
        sa.Column("assignment_mode", sa.Text(), nullable=True),
        sa.Column("materials", postgresql.JSONB(), nullable=True),
        sa.Column("drive_file_id", sa.Text(), nullable=True),
        sa.Column("drive_url", sa.Text(), nullable=True),
        sa.Column("supplier_folder_id", sa.Text(), nullable=True),
    )
    op.create_index("ix_delivery_details_supplier", "delivery_details", ["supplier"])
    op.create_index(
        "ix_delivery_details_document_number", "delivery_details", ["document_number"]
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_details_document_number", table_name="delivery_details")
    op.drop_index("ix_delivery_details_supplier", table_name="delivery_details")
    op.drop_table("delivery_details")
