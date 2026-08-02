"""Add review_items.photo_path for console-captured check photos.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-09

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_items", sa.Column("photo_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_items", "photo_path")
