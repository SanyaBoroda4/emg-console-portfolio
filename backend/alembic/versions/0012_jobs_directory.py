"""Jobs directory — the console's local copy of Moraware jobs, synced from
the bridge's /api/console/job-directory every ~10 minutes. Powers the
typeahead job picker (search must feel instant, so it never waits on the
bridge).

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs_directory",
        sa.Column("job_id", sa.Integer(), primary_key=True),
        sa.Column("customer_name", sa.Text(), nullable=False),
        sa.Column("lead_url", sa.Text(), nullable=True),
        sa.Column("creation_date", sa.Date(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_jobs_directory_customer_name", "jobs_directory", ["customer_name"]
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_directory_customer_name", table_name="jobs_directory")
    op.drop_table("jobs_directory")
