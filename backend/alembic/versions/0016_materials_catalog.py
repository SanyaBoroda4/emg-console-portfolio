"""Materials catalog (slab scans chapter): every stone name we know,
fed by delivery slips, manual adds, and n8n feeds (Airtable / Excel /
supplier sites). name_key = lowercased name, the dedup key.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-24

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "materials_catalog",
        sa.Column("name_key", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("supplier", sa.Text(), nullable=True),
        # 'delivery' | 'manual' | 'airtable' | 'excel' | 'website'
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_materials_catalog_name", "materials_catalog", ["name"])


def downgrade() -> None:
    op.drop_table("materials_catalog")
