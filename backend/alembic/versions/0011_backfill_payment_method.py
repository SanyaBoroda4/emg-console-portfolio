"""Backfill payment_method='check' for camera-submitted console rows (they
have a photo). Sweep rows carry their method from QuickBooks; cash rows get
overwritten by the workflow's cash branch.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-20

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE payment_details pd
        SET payment_method = 'check'
        FROM review_items ri
        WHERE ri.id = pd.review_item_id
          AND ri.source = 'console'
          AND ri.photo_path IS NOT NULL
          AND pd.payment_method IS NULL
        """
    )


def downgrade() -> None:
    pass
