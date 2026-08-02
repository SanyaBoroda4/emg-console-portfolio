"""Slab scans: scan_details — one row per scanning session, slab IDs
embedded as JSONB. item_type='slab_scan' rows in review_items own these.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-23

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_details",
        sa.Column(
            "review_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("slab_ids", postgresql.JSONB(), nullable=True),
        sa.Column("scanned_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("scan_details")
