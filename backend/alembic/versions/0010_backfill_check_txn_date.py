"""Backfill txn_date for console-captured checks: payment date = the Eastern-
time day the check was submitted (owner rule 2026-07-20). Mirror rows keep
whatever Airtable said; future sweep-workflow rows will carry their own dates.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-20

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE payment_details pd
        SET txn_date = (ri.created_at AT TIME ZONE 'America/New_York')::date
        FROM review_items ri
        WHERE ri.id = pd.review_item_id
          AND ri.source = 'console'
          AND pd.txn_date IS NULL
        """
    )


def downgrade() -> None:
    # Data backfill — nothing sensible to undo.
    pass
