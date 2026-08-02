"""Pydantic response models for the API.

`raw` is deliberately not exposed — the board doesn't need it and the
payload stays lean. Decimal amounts serialize as JSON strings (e.g.
"4850.50") to preserve exactness; the frontend formats them.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PaymentDetailsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    amount: Decimal | None
    payment_method: str | None
    payment_type: str | None
    payer_name: str | None
    invoice_number: str | None
    txn_date: date | None
    check_number: str | None
    caption_name: str | None
    date_received: datetime | None
    # Decision flow: the manager's fast-path entry and the sweep's dedup key.
    qb_invoice: str | None = None
    qb_payment_id: str | None = None


class DeliveryDetailsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier: str | None
    supplier_confidence: str | None
    document_number: str | None
    order_date: date | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    slab_count: int | None
    hand_notes: str | None
    validation_note: str | None
    validation_ok: bool | None
    assignment_mode: str | None
    materials: list | None
    drive_url: str | None


class ScanDetailsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slab_ids: list | None
    scanned_date: date | None


class ReviewItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_type: str
    status: str
    source: str
    airtable_id: str | None
    photo_drive_url: str | None
    photo_path: str | None
    matched_job_id: str | None
    matched_job_name: str | None
    moraware_url: str | None
    match_method: str | None
    created_at: datetime
    updated_at: datetime
    last_edited_at: datetime | None
    last_edited_by: str | None
    payment_details: PaymentDetailsOut | None
    delivery_details: DeliveryDetailsOut | None = None
    scan_details: ScanDetailsOut | None = None


class ReviewItemListOut(BaseModel):
    items: list[ReviewItemOut]
    total: int


class StatsOut(BaseModel):
    by_status: dict[str, int]
    total: int


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    review_item_id: uuid.UUID
    item_label: str
    actor_email: str
    action: str
    field: str | None
    old_value: str | None
    new_value: str | None
    created_at: datetime


class AuditListOut(BaseModel):
    entries: list[AuditEntryOut]
    total: int
