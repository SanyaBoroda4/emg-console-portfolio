"""Inbound n8n → console hooks (decision flow plan §5).

The contract the owner's cloned workflows drive. Authenticated by the
X-Pilot-Secret header (401 without it), constant-time compared. Every endpoint
operates ONLY on console-born rows (source='console', airtable_id NULL) —
mirrored rows belong to Airtable and the production bots, so they get 403.
Field values pass the SAME validators as the Stage 3 PATCH path.

No QuickBooks code or credentials live here, ever: qb_* values arrive as plain
strings from the workflow — the console neither calls QB nor knows sandbox
from production.
"""

import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import ItemEvent, PaymentDetails, ReviewItem
from app.notify import push_to_pilot_pool

# The Stage 3 PATCH validators — one validation brain for humans and bots
# (plan §5). _validate raises 422 with a plain message; imported privately on
# purpose rather than duplicated.
from app.routers.review_items import ADMIN_EDITABLE, _validate

# Bots may set everything an admin may edit, plus the sweep's dedup key.
# qb_invoice is NOT here: that field is the manager's fast-path entry.
HOOK_EDITABLE = ADMIN_EDITABLE | {"qb_payment_id"}

# Job identity lives on the ITEM (not payment_details) — the table's Job
# column and the card read these. Only the bot writes them (it's the matcher).
ITEM_JOB_FIELDS = {"matched_job_id", "matched_job_name", "moraware_url"}
JOB_FIELD_MAX_LEN = 500

STATUS_MAX_LEN = 60


def require_pilot_secret(x_pilot_secret: str | None = Header(default=None)) -> None:
    expected = get_settings().pilot_hook_secret
    if not x_pilot_secret or not hmac.compare_digest(x_pilot_secret, expected):
        raise HTTPException(status_code=401, detail={"error": "bad_secret"})


router = APIRouter(
    prefix="/api/hooks/pilot",
    tags=["hooks"],
    dependencies=[Depends(require_pilot_secret)],
)


def _console_item(db: Session, review_item_id: uuid.UUID) -> ReviewItem:
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if item.source != "console" or item.airtable_id is not None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "mirrored_row",
                "message": "Hooks operate on console-born payments only.",
            },
        )
    return item


def _validated_status(value: str) -> str:
    status = str(value).strip()
    if not status or len(status) > STATUS_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid", "field": "status",
                    "message": "Status must be 1-60 characters."},
        )
    return status


def _apply_fields(details: PaymentDetails, fields: dict[str, Any]) -> None:
    unknown = set(fields) - HOOK_EDITABLE
    if unknown:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "field_not_allowed",
                "fields": sorted(unknown),
                "message": "These fields cannot be set by hooks: "
                + ", ".join(sorted(unknown)),
            },
        )
    for field, raw in fields.items():
        setattr(details, field, _validate(field, raw))


def _money(item: ReviewItem) -> str | None:
    details = item.payment_details
    if details is not None and details.amount is not None:
        return f"${details.amount:,.2f}"
    return None


def _deep_link(item: ReviewItem) -> str:
    return f"/payments/item/{item.id}"


# --- POST /items — the sweep creates a photo-less payment -------------------


class ItemsIn(BaseModel):
    amount: Any = None
    payer_name: Any = None
    payment_method: Any = None
    payment_type: Any = None
    invoice_number: Any = None
    check_number: Any = None
    txn_date: Any = None
    qb_payment_id: Any = None
    status: str = "needs_job"
    # Optional first feed line; defaults to a generic system line.
    body: str | None = None


@router.post("/items", status_code=201)
def create_item(payload: ItemsIn, db: Session = Depends(get_db)) -> dict:
    field_values = {
        field: _validate(field, getattr(payload, field))
        for field in (
            "amount", "payer_name", "payment_method", "payment_type",
            "invoice_number", "check_number", "txn_date", "qb_payment_id",
        )
        if getattr(payload, field) is not None
    }
    item = ReviewItem(
        item_type="payment",
        status=_validated_status(payload.status),
        source="console",
        airtable_id=None,
        raw={},
        payment_details=PaymentDetails(**field_values),
    )
    db.add(item)
    db.flush()  # need item.id for the event
    db.add(
        ItemEvent(
            review_item_id=item.id,
            kind="system",
            body=(payload.body or "").strip() or "Created by the payment sweep.",
        )
    )
    db.commit()
    return {"review_item_id": str(item.id)}


# --- POST /update — OCR results / bot narration ------------------------------


class UpdateIn(BaseModel):
    review_item_id: uuid.UUID
    body: str
    fields: dict[str, Any] | None = None
    status: str | None = None


@router.post("/update")
def update_item(payload: UpdateIn, db: Session = Depends(get_db)) -> dict:
    item = _console_item(db, payload.review_item_id)
    body = payload.body.strip()
    if not body:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid", "field": "body",
                    "message": "body must not be empty."},
        )
    if payload.fields:
        fields = dict(payload.fields)
        # Job identity fields land on the item itself.
        for name in ITEM_JOB_FIELDS & set(fields):
            raw = fields.pop(name)
            value = str(raw).strip() if raw is not None else None
            if value is not None and len(value) > JOB_FIELD_MAX_LEN:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid", "field": name,
                            "message": f"{name} must be ≤{JOB_FIELD_MAX_LEN} characters."},
                )
            if name == "moraware_url" and value and not value.startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid", "field": name,
                            "message": "moraware_url must be an http(s) URL."},
                )
            setattr(item, name, value or None)
        if fields:
            if item.payment_details is None:  # defensive: console rows always have one
                item.payment_details = PaymentDetails()
            _apply_fields(item.payment_details, fields)
    if payload.status is not None:
        item.status = _validated_status(payload.status)
    db.add(
        ItemEvent(
            review_item_id=item.id,
            kind="bot_update",
            body=body,
            payload={"fields": sorted(payload.fields)} if payload.fields else None,
        )
    )
    db.commit()
    return {"ok": True}


# --- POST /question — the decision request ----------------------------------


class CandidateIn(BaseModel):
    label: str
    sublabel: str | None = None
    job_id: str | None = None
    moraware_url: str | None = None


class QuestionIn(BaseModel):
    review_item_id: uuid.UUID
    body: str
    candidates: list[CandidateIn]
    resume_url: str
    allowed_freeform: bool = True
    format_hint: str | None = None
    # False = ask quietly (retry rounds — the answering manager is already
    # on the card; re-buzzing the whole pool reads as a new payment).
    push: bool = True


@router.post("/question")
def post_question(payload: QuestionIn, db: Session = Depends(get_db)) -> dict:
    item = _console_item(db, payload.review_item_id)
    body = payload.body.strip()
    if not body:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid", "field": "body",
                    "message": "body must not be empty."},
        )
    if not payload.resume_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid", "field": "resume_url",
                    "message": "resume_url must be an http(s) URL."},
        )

    # One open question at a time, multi-round aware: the newest bot_question
    # must have a decision paired to it (answers_event_id) before another
    # question may be asked.
    latest_question = db.scalar(
        select(ItemEvent)
        .where(
            ItemEvent.review_item_id == item.id,
            ItemEvent.kind == "bot_question",
        )
        .order_by(ItemEvent.created_at.desc())
        .limit(1)
    )
    if latest_question is not None:
        answered = db.scalar(
            select(
                exists().where(
                    ItemEvent.kind == "decision",
                    ItemEvent.answers_event_id == latest_question.id,
                )
            )
        )
        if not answered:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "question_open",
                    "message": "This item already has an unanswered question.",
                },
            )

    db.add(
        ItemEvent(
            review_item_id=item.id,
            kind="bot_question",
            body=body,
            payload={
                "candidates": [c.model_dump() for c in payload.candidates],
                "resume_url": payload.resume_url,
                "allowed_freeform": payload.allowed_freeform,
                "format_hint": payload.format_hint,
            },
        )
    )
    item.status = "needs_job"
    db.commit()

    if not payload.push:
        return {"ok": True, "pushed": 0}
    money = _money(item)
    sent, _ = push_to_pilot_pool(
        db,
        title=f"Check {money} needs a job" if money else "A payment needs a job",
        body=body,
        url=_deep_link(item),
    )
    return {"ok": True, "pushed": sent}


# --- POST /final — resolution (fast path AND post-decision) ------------------


class FinalIn(BaseModel):
    review_item_id: uuid.UUID
    body: str
    status: str
    # False = resolve quietly (sweep auto-records / after a manager already
    # acted) — the run summary or the board carries the news instead.
    push: bool = True


@router.post("/final")
def post_final(payload: FinalIn, db: Session = Depends(get_db)) -> dict:
    item = _console_item(db, payload.review_item_id)
    body = payload.body.strip()
    if not body:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid", "field": "body",
                    "message": "body must not be empty."},
        )
    item.status = _validated_status(payload.status)
    db.add(ItemEvent(review_item_id=item.id, kind="system", body=body))
    db.commit()

    if not payload.push:
        return {"ok": True, "pushed": 0}
    # The workflow authors the whole line ("✓ $4,850 → Simmons") — pass it
    # through as the notification title.
    sent, _ = push_to_pilot_pool(db, title=body, body="Tap to view", url=_deep_link(item))
    return {"ok": True, "pushed": sent}


# --- GET /find — the sweep's dedup/query brain --------------------------------


@router.get("/find")
def find_items(
    qb_payment_id: str | None = Query(default=None),
    check_number: str | None = Query(default=None),
    invoice_number: str | None = Query(default=None),
    amount: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    clauses = []
    if qb_payment_id is not None:
        clauses.append(PaymentDetails.qb_payment_id == qb_payment_id.strip())
    if check_number is not None:
        clauses.append(PaymentDetails.check_number == check_number.strip())
    if invoice_number is not None:
        clauses.append(PaymentDetails.invoice_number == invoice_number.strip())
    if amount is not None:
        clauses.append(PaymentDetails.amount == _validate("amount", amount))
    if not clauses:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid",
                "message": "Provide at least one of: qb_payment_id, check_number, "
                "invoice_number, amount.",
            },
        )

    items = (
        db.scalars(
            select(ReviewItem)
            .join(ReviewItem.payment_details)
            .where(
                ReviewItem.source == "console",
                ReviewItem.airtable_id.is_(None),
                *clauses,
            )
            .order_by(ReviewItem.created_at.desc())
        )
        .unique()
        .all()
    )
    return {"items": [_item_row(i) for i in items]}


def _item_row(i: ReviewItem) -> dict:
    d = i.payment_details
    return {
        "review_item_id": str(i.id),
        "status": i.status,
        "amount": str(d.amount) if d and d.amount is not None else None,
        "payer_name": d.payer_name if d else None,
        "check_number": d.check_number if d else None,
        "invoice_number": d.invoice_number if d else None,
        "payment_method": d.payment_method if d else None,
        "payment_type": d.payment_type if d else None,
        "txn_date": d.txn_date.isoformat() if d and d.txn_date else None,
        "qb_invoice": d.qb_invoice if d else None,
        "qb_payment_id": d.qb_payment_id if d else None,
        "matched_job_id": i.matched_job_id,
        "matched_job_name": i.matched_job_name,
        "moraware_url": i.moraware_url,
        "created_at": i.created_at.isoformat(),
    }


# --- GET /list — the sweep's register snapshot --------------------------------


@router.get("/list")
def list_recent_items(
    days: int = Query(default=45, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    """Recent console-born rows, newest first — the sweep diffs QuickBooks
    payments against this snapshot (skip / backfill / supersede decisions)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items = (
        db.scalars(
            select(ReviewItem)
            .where(
                ReviewItem.source == "console",
                ReviewItem.airtable_id.is_(None),
                ReviewItem.item_type == "payment",
                ReviewItem.created_at >= cutoff,
            )
            .order_by(ReviewItem.created_at.desc())
        )
        .unique()
        .all()
    )
    return {"items": [_item_row(i) for i in items]}


# --- GET /photo/{id} — the ORIGINAL full-quality photo for Drive archiving ----


@router.get("/photo/{review_item_id}")
def hook_photo(review_item_id: uuid.UUID, db: Session = Depends(get_db)):
    """The stored original (the outbound trigger only carries a downscaled
    OCR copy) — the workflow archives THIS to Google Drive."""
    from pathlib import Path as _Path

    from fastapi.responses import FileResponse

    from app.routers.checks import MEDIA_TYPES

    item = _console_item(db, review_item_id)
    if not item.photo_path:
        raise HTTPException(status_code=404, detail={"error": "no_photo"})
    path = _Path(item.photo_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"error": "photo_missing"})
    return FileResponse(
        path, media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream")
    )


# --- POST /notify — run-level pool push (no single item to hang it on) --------


class NotifyIn(BaseModel):
    title: str
    body: str
    url: str | None = None


@router.post("/notify")
def notify_pool(payload: NotifyIn, db: Session = Depends(get_db)) -> dict:
    """Push to the pilot pool about a RUN, not an item — sweep summaries and
    split-check digests. url defaults to the payments board."""
    title = payload.title.strip()
    body = payload.body.strip()
    if not title or not body:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid",
                    "message": "title and body must not be empty."},
        )
    url = (payload.url or "").strip() or "/payments"
    sent, _ = push_to_pilot_pool(db, title=title, body=body, url=url)
    return {"ok": True, "pushed": sent}
