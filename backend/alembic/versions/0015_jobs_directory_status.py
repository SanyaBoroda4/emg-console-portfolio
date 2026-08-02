"""Jobs directory: nullable status column (Moraware job status once the
bridge sends it). Search hides Done/Cancelled jobs.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-24

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs_directory", sa.Column("status", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs_directory", "status")
