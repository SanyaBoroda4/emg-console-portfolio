"""SQLAlchemy models — schema defined in STAGE1_BUILD_PLAN.md §6 plus the
three payment_details columns added by STAGE1_ADDENDUM_FIELD_MAPPING.md.

Types are chosen to work on both Postgres (production) and SQLite (test
fixtures): UUIDs get a client-side default here, while the Alembic migration
adds the Postgres server-side gen_random_uuid() default; `raw` is JSONB on
Postgres and plain JSON elsewhere.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    """Roster of console users (Stage 2). No management UI this stage —
    roster changes are a migration or direct SQL."""

    __tablename__ = "users"

    # Lowercase-normalized on write (the auth layer lowercases before lookup).
    email: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    # CHECK constraint, not an enum type: this vocabulary is OURS (unlike the
    # imported Airtable statuses, which stay free text on purpose).
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("role IN ('admin','manager','yard')", name="ck_users_role"),
    )


class AuditLog(Base):
    """Who changed what, when (Stage 3). Deliberately NO foreign key to
    review_items — history must survive row deletion."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # Human snapshot at action time — readable even after the row is gone.
    item_label: Mapped[str] = mapped_column(Text, nullable=False)
    actor_email: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str | None] = mapped_column(Text)  # NULL for deletes
    old_value: Mapped[str | None] = mapped_column(Text)  # "" vs NULL preserved
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("action IN ('edit','delete')", name="ck_audit_log_action"),
    )


class PushSubscription(Base):
    """A single browser's Web Push subscription (push mechanics slice §1).

    One row per (user, browser): the push service `endpoint` is globally unique
    and is the upsert key — re-subscribing from the same browser updates in
    place rather than duplicating. No FK to users: a subscription is keyed by
    endpoint and carries the owner's email as plain text, like audit_log.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_email: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # The push service URL the browser handed us — globally unique per browser.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # The subscription's public key and auth secret (from PushSubscription.keys),
    # required by pywebpush to encrypt the payload.
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    # For debugging which device a subscription belongs to; not relied upon.
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ItemEvent(Base):
    """The shared activity feed of an item (decision flow stage): bot updates,
    bot questions, human decisions, comments, and system lines — in one ordered
    stream. Like audit_log, deliberately NO foreign key to review_items: the
    feed survives row deletion.

    First-tap-wins is enforced by the DATABASE, not application logic: a
    partial unique index allows at most one kind='decision' row per QUESTION
    (answers_event_id), so a race between two managers becomes a clean
    constraint violation → 409 — while the workflow may ask again after an
    answer didn't pan out (multi-round Q&A: "typed name found nothing").
    """

    __tablename__ = "item_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # 'system' | 'bot_update' | 'bot_question' | 'decision' | 'comment'
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Human-readable feed line, always present.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured extras. For kind='bot_question': {candidates: [{label,
    # sublabel, job_id, moraware_url}], resume_url, allowed_freeform,
    # format_hint}. For kind='decision': the choice/text that was sent.
    payload: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    # NULL = the bot / the system; set for human decisions and comments.
    actor_email: Mapped[str | None] = mapped_column(Text)
    # kind='decision' only: the bot_question event this decision answers.
    answers_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Client-side default (not just server_default): SQLite's CURRENT_TIMESTAMP
    # is second-precision, which makes "latest question" ambiguous when rounds
    # land in the same second. Python supplies microseconds on both dialects.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('system','bot_update','bot_question','decision','comment')",
            name="ck_item_events_kind",
        ),
        # The race-killer: one decision per question, enforced on both Postgres
        # (prod) and SQLite (tests) via dialect-specific partial-index clauses.
        Index(
            "uq_item_events_one_decision_per_question",
            "answers_event_id",
            unique=True,
            postgresql_where=text("kind = 'decision'"),
            sqlite_where=text("kind = 'decision'"),
        ),
    )


class ReviewItem(Base):
    """One row per thing a human may need to review (payments in Stage 1)."""

    __tablename__ = "review_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Verbatim from Airtable — deliberately not an enum; the mirror must
    # never crash on a new value.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # Airtable record id (rec...) — the upsert key that makes the mirror
    # idempotent. Nullable after cutover when rows are born in the console.
    airtable_id: Mapped[str | None] = mapped_column(Text, unique=True, index=True)
    photo_drive_url: Mapped[str | None] = mapped_column(Text)
    # Local path of a console-captured check photo (POST /api/checks);
    # served via GET /api/photos/{id}. Mirror rows leave this NULL.
    photo_path: Mapped[str | None] = mapped_column(Text)
    # Moraware job id as text, not FK — Moraware owns job identity.
    matched_job_id: Mapped[str | None] = mapped_column(Text)
    matched_job_name: Mapped[str | None] = mapped_column(Text)
    moraware_url: Mapped[str | None] = mapped_column(Text)
    match_method: Mapped[str | None] = mapped_column(Text)
    # Complete original Airtable `fields` object — the safety net.
    raw: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set by the app on every update (the mirror refreshes it when a row changes).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Cheap display fields for the "edited" chip — set by the PATCH endpoint;
    # the full history lives in audit_log.
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_edited_by: Mapped[str | None] = mapped_column(Text)

    payment_details: Mapped["PaymentDetails | None"] = relationship(
        back_populates="review_item",
        cascade="all, delete-orphan",
        uselist=False,
    )
    delivery_details: Mapped["DeliveryDetails | None"] = relationship(
        back_populates="review_item",
        cascade="all, delete-orphan",
        uselist=False,
    )
    scan_details: Mapped["ScanDetails | None"] = relationship(
        back_populates="review_item",
        cascade="all, delete-orphan",
        uselist=False,
    )

    # The board's main query: WHERE item_type = ? AND status = ?
    __table_args__ = (Index("ix_review_items_item_type_status", "item_type", "status"),)


class PaymentDetails(Base):
    """Payment-specific fields, 1:1 extension of a review item."""

    __tablename__ = "payment_details"

    review_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("review_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # numeric, never float — float arithmetic corrupts money.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[str | None] = mapped_column(Text)
    payment_type: Mapped[str | None] = mapped_column(Text)
    payer_name: Mapped[str | None] = mapped_column(Text)
    # Text, not integer — real invoice numbers grow prefixes and dashes.
    invoice_number: Mapped[str | None] = mapped_column(Text)
    # Payment date from the document, distinct from created_at (ingest time).
    txn_date: Mapped[date | None] = mapped_column(Date)
    # Addendum columns:
    check_number: Mapped[str | None] = mapped_column(Text)
    # Name typed by staff in the WhatsApp caption — best human hint when OCR
    # and QB disagree.
    caption_name: Mapped[str | None] = mapped_column(Text)
    # When the photo hit WhatsApp — business time, distinct from txn_date
    # and created_at.
    date_received: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Decision flow additions: the manager-entered 4-digit fast-path value
    # (distinct from invoice_number, which the bot fills from QB)...
    qb_invoice: Mapped[str | None] = mapped_column(Text)
    # ...and the QB payment id set by the sweep workflow via hooks — the dedup
    # key GET /api/hooks/pilot/find matches on.
    qb_payment_id: Mapped[str | None] = mapped_column(Text, index=True)

    review_item: Mapped[ReviewItem] = relationship(back_populates="payment_details")


class JobDirectory(Base):
    """Local copy of Moraware jobs, synced from the bridge's cached
    /api/console/job-directory (~10 min cadence). Read by the typeahead job
    picker — searches never wait on the bridge."""

    __tablename__ = "jobs_directory"

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    lead_url: Mapped[str | None] = mapped_column(Text)
    creation_date: Mapped[date | None] = mapped_column(Date)
    # Moraware job status (e.g. "Done", "Cancelled") once the bridge sends
    # it; NULL until then. Search hides Done/Cancelled jobs.
    status: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MaterialCatalog(Base):
    """Every stone name we know (slab scans chapter). Fed organically by
    delivery slips + manual adds, and in bulk by n8n feeds (Airtable,
    stock Excel, supplier sites). name_key = lower(name) is the PK, so
    the same stone from two sources stays one row."""

    __tablename__ = "materials_catalog"

    name_key: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    supplier: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DeliveryDetails(Base):
    """Slab delivery specifics (slab deliveries stage) — ONE row per slip,
    with the slip's materials embedded as a JSON list (owner rule: one
    beautiful table, not a separate materials table). Each material carries
    its own job assignment once a manager picks one."""

    __tablename__ = "delivery_details"

    review_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_items.id", ondelete="CASCADE"), primary_key=True
    )
    supplier: Mapped[str | None] = mapped_column(Text)
    supplier_confidence: Mapped[str | None] = mapped_column(Text)
    document_number: Mapped[str | None] = mapped_column(Text)
    order_date: Mapped[date | None] = mapped_column(Date)
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    slab_count: Mapped[int | None] = mapped_column(Integer)
    hand_notes: Mapped[str | None] = mapped_column(Text)
    validation_note: Mapped[str | None] = mapped_column(Text)
    validation_ok: Mapped[bool | None] = mapped_column()
    # 'one' | 'split' — the poll answer for multi-material slips.
    assignment_mode: Mapped[str | None] = mapped_column(Text)
    # [{material, finish, thickness, area, slab_count, total_sf, serials,
    #   barcodes, lot, unit_price, extended_price,
    #   job_id, job_name, moraware_url, stock, assigned_by, assigned_at}]
    materials: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    # Workflow bookkeeping for the Drive move on confirm.
    drive_file_id: Mapped[str | None] = mapped_column(Text)
    drive_url: Mapped[str | None] = mapped_column(Text)
    supplier_folder_id: Mapped[str | None] = mapped_column(Text)

    review_item: Mapped[ReviewItem] = relationship(back_populates="delivery_details")


class ScanDetails(Base):
    """Slab scan session (slab scans chapter) — one card = one job, the
    scanned slab IDs embedded as a JSON list. Wade's daily label scans
    end up as ONE appended note on the Moraware Job Details form."""

    __tablename__ = "scan_details"

    review_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_items.id", ondelete="CASCADE"), primary_key=True
    )
    # [{id: "2287478", source: "qr" | "ocr" | "manual"}]
    slab_ids: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    scanned_date: Mapped[date | None] = mapped_column(Date)

    review_item: Mapped[ReviewItem] = relationship(back_populates="scan_details")
