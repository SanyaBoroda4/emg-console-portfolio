"""GET/PATCH/DELETE /api/review-items and /api/review-items/stats.

Stage 3 amendment: PATCH is the console's ONLY Airtable write path —
mirrored rows write through to Airtable first (n8n bots still read it
during the transition), then update Postgres + raw in one transaction.
Console-born rows edit locally only. Every change lands in audit_log.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pyairtable import Api
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, contains_eager

from app.auth import get_current_user, require_role
from app.config import get_settings
from app.db import get_db
from app.models import AuditLog, ItemEvent, PaymentDetails, ReviewItem, User
from app.outbound import send_check_to_workflow
from app.schemas import ReviewItemListOut, ReviewItemOut, StatsOut
from app.scripts.mirror_airtable import parse_date

# Per-role edit whitelists (STAGE3_BUILD_PLAN.md §1) — enforced HERE, no
# matter which UI sent the request.
MANAGER_EDITABLE = {"amount"}
ADMIN_EDITABLE = {
    "amount",
    "payer_name",
    "payment_type",
    "payment_method",
    "invoice_number",
    "check_number",
    "txn_date",
    "caption_name",
}

# Our column → Airtable field, for the write-through and the raw sync.
AIRTABLE_FIELD_MAP = {
    "amount": "Amount",
    "payer_name": "PayerName",
    "payment_type": "PaymentType",
    "payment_method": "PaymentMethod",
    "invoice_number": "InvoiceNumber",
    "check_number": "CheckNumber",
    "caption_name": "CaptionName",
    "txn_date": "PaymentDate",
}

AMOUNT_MAX = Decimal("500000")
TEXT_MAX_LEN = 200

# Payments are admin/manager only (role matrix, STAGE2_BUILD_PLAN.md §3).
# Yard gets a clean 403 from every payments call — enforced here, never
# only in the UI.
router = APIRouter(
    prefix="/api/review-items",
    tags=["review-items"],
    dependencies=[Depends(require_role("admin", "manager"))],
)


def _filters(item_type: str | None, status: str | None) -> list:
    clauses = []
    if item_type is not None:
        clauses.append(ReviewItem.item_type == item_type)
    if status is not None:
        clauses.append(ReviewItem.status == status)
    return clauses


@router.get("/stats", response_model=StatsOut)
def stats(
    item_type: str | None = None,
    db: Session = Depends(get_db),
) -> StatsOut:
    query = select(ReviewItem.status, func.count()).group_by(ReviewItem.status)
    if item_type is not None:
        query = query.where(ReviewItem.item_type == item_type)
    by_status = {status: count for status, count in db.execute(query)}
    return StatsOut(by_status=by_status, total=sum(by_status.values()))


@router.get("", response_model=ReviewItemListOut)
def list_review_items(
    item_type: str | None = None,
    status: str | None = None,
    # Ceiling 1000: table mode fetches the full set (Stage 1.5 §3).
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ReviewItemListOut:
    clauses = _filters(item_type, status)
    total = db.scalar(select(func.count()).select_from(ReviewItem).where(*clauses))
    # Newest payment first, by the payment's own date with fallbacks for
    # rows the OCR couldn't date (owner override of Stage 1.5 §3, which
    # specified ascending). No casts: Postgres promotes date → timestamptz
    # inside COALESCE, and SQLite (tests) compares the ISO strings
    # chronologically. created_at breaks ties stably.
    payment_moment = func.coalesce(
        PaymentDetails.txn_date,
        PaymentDetails.date_received,
        ReviewItem.created_at,
    )
    items = (
        db.scalars(
            select(ReviewItem)
            .outerjoin(ReviewItem.payment_details)
            .options(contains_eager(ReviewItem.payment_details))
            .where(*clauses)
            .order_by(payment_moment.desc(), ReviewItem.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .unique()
        .all()
    )
    return ReviewItemListOut(items=items, total=total)


class PatchIn(BaseModel):
    changes: dict[str, Any]
    # What the client believed each field's value was — the 409 guard.
    expected: dict[str, Any] = {}


def _validate(field: str, value: Any) -> Any:
    """422 on bad input; empty string means 'clear the field' (NULL)."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if field == "amount":
        try:
            amount = Decimal(str(value)).quantize(Decimal("0.01"))
        except InvalidOperation:
            raise HTTPException(422, {"error": "invalid", "field": field,
                                      "message": "Amount must be a number."})
        if amount <= 0 or amount > AMOUNT_MAX:
            raise HTTPException(422, {"error": "invalid", "field": field,
                                      "message": "Amount must be between 0 and 500,000."})
        return amount
    if field == "txn_date":
        parsed = parse_date(str(value))
        if parsed is None:
            raise HTTPException(422, {"error": "invalid", "field": field,
                                      "message": "Couldn't understand that date — try YYYY-MM-DD."})
        return parsed
    text = str(value).strip()
    if len(text) > TEXT_MAX_LEN:
        raise HTTPException(422, {"error": "invalid", "field": field,
                                  "message": f"Text is too long (max {TEXT_MAX_LEN} characters)."})
    return text


def _normalize(field: str, value: Any) -> str | None:
    """String-normalize a value for the 409 comparison — DB values and
    client-sent 'expected' values must meet in one representation."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None  # cleared field: the client sees null
    if field == "amount":
        try:
            return str(Decimal(str(value)).quantize(Decimal("0.01")))
        except InvalidOperation:
            return str(value)
    if field == "txn_date":
        if isinstance(value, date):
            return value.isoformat()
        parsed = parse_date(str(value))
        return parsed.isoformat() if parsed else str(value)
    return str(value)


def _stringify(value: Any) -> str | None:
    """Audit representation: NULL stays NULL, "" stays "" (plan §3)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _to_airtable_value(field: str, value: Any) -> Any:
    """Our validated value → what Airtable should store. None clears."""
    if value is None:
        return None
    if field == "amount":
        return float(value)  # Airtable number field
    if field == "txn_date":
        # Airtable's live PaymentDate shape: M/D/YYYY h:mmam/pm, noon.
        return f"{value.month}/{value.day}/{value.year} 12:00pm"
    return str(value)


def _item_label(item: ReviewItem) -> str:
    """Human snapshot for audit_log — readable after the row is gone."""
    if item.item_type == "slab_delivery":
        d = item.delivery_details
        supplier = (d.supplier if d else None) or "unknown supplier"
        doc = (d.document_number if d else None) or "no #"
        return f"delivery {supplier} — {doc}"
    details = item.payment_details
    amount = (
        f"${details.amount:,.2f}" if details and details.amount is not None else "no amount"
    )
    payer = (details.payer_name or details.caption_name) if details else None
    return f"payment {amount} — {payer or 'unknown payer'}"


def _airtable_table():
    """The write-token table handle. Isolated so tests can mock it."""
    settings = get_settings()
    return Api(settings.airtable_write_token).table(
        settings.airtable_base_id, settings.airtable_payments_table
    )


@router.patch("/{review_item_id}", response_model=ReviewItemOut)
def edit_review_item(
    review_item_id: uuid.UUID,
    payload: PatchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewItem:
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise HTTPException(404, detail="No such review item.")
    if not payload.changes:
        raise HTTPException(422, {"error": "invalid", "message": "No changes provided."})

    # 1. Role whitelist — server-enforced regardless of which UI asked.
    allowed = ADMIN_EDITABLE if user.role == "admin" else MANAGER_EDITABLE
    offending = sorted(set(payload.changes) - allowed)
    if offending:
        raise HTTPException(
            403,
            {"error": "forbidden", "message": f"Your role can't edit: {', '.join(offending)}."},
        )

    details = item.payment_details
    if details is None:  # defensive; every row is created with one
        details = PaymentDetails()
        item.payment_details = details

    # 2. Validate everything before touching anything.
    validated: dict[str, Any] = {
        field: _validate(field, value) for field, value in payload.changes.items()
    }

    # 3. Concurrency guardrail — two people must never silently overwrite
    # each other. Compare current vs what the client saw.
    for field in payload.changes:
        if field in payload.expected:
            current = _normalize(field, getattr(details, field))
            expected = _normalize(field, payload.expected[field])
            if current != expected:
                raise HTTPException(
                    409, {"error": "stale", "field": field, "current": current}
                )

    # 4. Mirrored rows: Airtable FIRST. Any failure → nothing changes here.
    if item.airtable_id:
        fields_payload = {
            AIRTABLE_FIELD_MAP[field]: _to_airtable_value(field, value)
            for field, value in validated.items()
        }
        try:
            # typecast=True lets Airtable coerce e.g. "51" into number fields.
            _airtable_table().update(item.airtable_id, fields_payload, typecast=True)
        except Exception:
            raise HTTPException(
                502,
                {
                    "error": "airtable_write_failed",
                    "message": "Couldn't reach Airtable — nothing was changed. Try again.",
                },
            )

    # 5. One local transaction: values + raw sync + edited marker + audit.
    now = datetime.now(timezone.utc)
    label = _item_label(item)
    new_raw = dict(item.raw)
    for field, new_value in validated.items():
        old_value = getattr(details, field)
        setattr(details, field, new_value)
        if item.airtable_id:
            airtable_field = AIRTABLE_FIELD_MAP[field]
            airtable_value = _to_airtable_value(field, new_value)
            if airtable_value is None:
                # Airtable omits empty fields from fetched records.
                new_raw.pop(airtable_field, None)
            else:
                new_raw[airtable_field] = airtable_value
        db.add(
            AuditLog(
                review_item_id=item.id,
                item_label=label,
                actor_email=user.email,
                action="edit",
                field=field,
                old_value=_stringify(old_value),
                new_value=_stringify(new_value),
            )
        )
    if item.airtable_id:
        item.raw = new_raw  # reassign: JSONB mutation isn't tracked in place
    item.last_edited_at = now
    item.last_edited_by = user.email
    db.commit()
    db.refresh(item)
    return item


@router.post(
    "/{review_item_id}/resend",
    status_code=202,
    # Re-firing the workflow is an admin recovery tool (decision flow §4).
    dependencies=[Depends(require_role("admin"))],
)
def resend_to_workflow(
    review_item_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Re-fire the outbound check-submitted trigger for a stuck item — the
    recovery path for a "couldn't reach the workflow" feed line."""
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such review item.")
    if item.source != "console" or item.airtable_id is not None:
        raise HTTPException(
            status_code=403,
            detail={"error": "mirrored_row",
                    "message": "Only console-born payments go to the workflow."},
        )
    if not item.photo_path:
        raise HTTPException(
            status_code=409,
            detail={"error": "no_photo",
                    "message": "This item has no photo to send."},
        )
    if not get_settings().n8n_pilot_webhook_url:
        raise HTTPException(
            status_code=503,
            detail={"error": "outbound_disabled",
                    "message": "N8N_PILOT_WEBHOOK_URL is not configured."},
        )
    # Replay the ORIGINAL submitter when the feed remembers one (the
    # "Check submitted" system line); fall back to the resending admin.
    original = db.scalar(
        select(ItemEvent.actor_email)
        .where(
            ItemEvent.review_item_id == item.id,
            ItemEvent.kind == "system",
            ItemEvent.actor_email.is_not(None),
        )
        .order_by(ItemEvent.created_at)
        .limit(1)
    )
    background_tasks.add_task(send_check_to_workflow, item.id, original or user.email)
    return {"ok": True}


@router.delete(
    "/{review_item_id}",
    status_code=204,
    # Stricter than the router-level admin/manager wall: deleting anything,
    # from any tab, is admins only (owner, 2026-07-10).
    dependencies=[Depends(require_role("admin"))],
)
def delete_review_item(
    review_item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete one item (details cascade; console photo file removed too).

    Note: a row mirrored from Airtable reappears on the next mirror run —
    the mirror treats Airtable as the source of truth, by design.
    """
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such review item.")
    # Deletion history finally survives the deletion (Stage 3).
    db.add(
        AuditLog(
            review_item_id=item.id,
            item_label=_item_label(item),
            actor_email=user.email,
            action="delete",
        )
    )
    if item.photo_path:
        Path(item.photo_path).unlink(missing_ok=True)
    db.delete(item)
    db.commit()
