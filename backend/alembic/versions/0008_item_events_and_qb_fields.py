"""Decision flow (STAGE_DECISION_FLOW_BUILD_PLAN.md §1): item_events feed +
payment_details.qb_invoice / qb_payment_id.

item_events has deliberately NO FK to review_items (feed survives deletion,
same rationale as audit_log). The partial unique index allows at most ONE
kind='decision' row per item — first-tap-wins enforced by the database.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("review_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("actor_email", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('system','bot_update','bot_question','decision','comment')",
            name="ck_item_events_kind",
        ),
    )
    op.create_index("ix_item_events_review_item_id", "item_events", ["review_item_id"])
    # The race-killer: at most one decision per item, enforced by Postgres.
    op.create_index(
        "uq_item_events_one_decision",
        "item_events",
        ["review_item_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'decision'"),
    )

    op.add_column("payment_details", sa.Column("qb_invoice", sa.Text(), nullable=True))
    op.add_column(
        "payment_details", sa.Column("qb_payment_id", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_payment_details_qb_payment_id", "payment_details", ["qb_payment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_payment_details_qb_payment_id", table_name="payment_details")
    op.drop_column("payment_details", "qb_payment_id")
    op.drop_column("payment_details", "qb_invoice")
    op.drop_index("uq_item_events_one_decision", table_name="item_events")
    op.drop_index("ix_item_events_review_item_id", table_name="item_events")
    op.drop_table("item_events")
