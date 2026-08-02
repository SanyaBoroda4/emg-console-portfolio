"""Multi-round Q&A: pair each decision with the bot_question it answers.

The one-decision-per-ITEM index blocked the workflow from ever asking again
(e.g. the manager typed a job name and the search found nothing). Now each
kind='decision' row records answers_event_id = the bot_question event id, and
uniqueness moves to one decision per QUESTION — first-tap-wins per round.

Backfill: every existing decision is paired with its item's latest
bot_question created before it.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-17

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "item_events",
        sa.Column("answers_event_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE item_events d
        SET answers_event_id = (
            SELECT q.id FROM item_events q
            WHERE q.review_item_id = d.review_item_id
              AND q.kind = 'bot_question'
              AND q.created_at <= d.created_at
            ORDER BY q.created_at DESC
            LIMIT 1
        )
        WHERE d.kind = 'decision'
        """
    )
    op.drop_index("uq_item_events_one_decision", table_name="item_events")
    op.create_index(
        "uq_item_events_one_decision_per_question",
        "item_events",
        ["answers_event_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'decision'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_item_events_one_decision_per_question", table_name="item_events"
    )
    op.create_index(
        "uq_item_events_one_decision",
        "item_events",
        ["review_item_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'decision'"),
    )
    op.drop_column("item_events", "answers_event_id")
