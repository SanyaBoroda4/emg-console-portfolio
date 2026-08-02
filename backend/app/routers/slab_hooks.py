"""SLABBOT → console hooks (slab deliveries stage). Same X-Pilot-Secret as
the pilot hooks; operates on item_type='slab_delivery' rows only.

The slab workflow narrates and fills data — it never asks questions here:
the manager decides on the card (typeahead/stock/poll), and the console
calls the workflow's completion webhook when everything is assigned.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers.materials import upsert_materials
from app.models import DeliveryDetails, ItemEvent, ReviewItem
from app.notify import push_to_pilot_pool
from app.routers.hooks import require_pilot_secret

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/hooks/slab",
    tags=["slab-hooks"],
    dependencies=[Depends(require_pilot_secret)],
)

TEXT_MAX = 500


def _slab_item(db: Session, review_item_id: uuid.UUID) -> ReviewItem:
    item = db.get(ReviewItem, review_item_id)
    if item is None or item.item_type != "slab_delivery" or item.source != "console":
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if item.delivery_details is None:
        item.delivery_details = DeliveryDetails()
    return item


def _txt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:TEXT_MAX] if text else None


def _num(value: Any):
    if value in (None, ""):
        return None
    try:
        return round(float(str(value).replace(",", "")), 2)
    except ValueError:
        return None


class SlipDetailsIn(BaseModel):
    supplier: Any = None
    supplier_confidence: Any = None
    document_number: Any = None
    order_date: Any = None  # ISO yyyy-mm-dd
    subtotal: Any = None
    tax: Any = None
    total: Any = None
    slab_count: Any = None
    hand_notes: Any = None
    validation_note: Any = None
    validation_ok: bool | None = None
    materials: list[dict] | None = None
    drive_file_id: Any = None
    drive_url: Any = None
    supplier_folder_id: Any = None


class SlabUpdateIn(BaseModel):
    review_item_id: uuid.UUID
    body: str
    details: SlipDetailsIn | None = None
    status: str | None = None
    # The delivery's ONE push ("Delivery from CRS — needs a job").
    push: bool = False
    push_title: str | None = None


@router.post("/update")
def slab_update(payload: SlabUpdateIn, db: Session = Depends(get_db)) -> dict:
    item = _slab_item(db, payload.review_item_id)
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail={"error": "invalid",
                                                     "message": "body must not be empty."})
    d = item.delivery_details
    if payload.details:
        s = payload.details
        d.supplier = _txt(s.supplier) or d.supplier
        d.supplier_confidence = _txt(s.supplier_confidence) or d.supplier_confidence
        d.document_number = _txt(s.document_number) or d.document_number
        if s.order_date:
            try:
                d.order_date = datetime.fromisoformat(str(s.order_date)).date()
            except ValueError:
                pass
        d.subtotal = _num(s.subtotal) if s.subtotal is not None else d.subtotal
        d.tax = _num(s.tax) if s.tax is not None else d.tax
        d.total = _num(s.total) if s.total is not None else d.total
        if s.slab_count is not None:
            try:
                d.slab_count = int(s.slab_count)
            except (TypeError, ValueError):
                pass
        d.hand_notes = _txt(s.hand_notes) or d.hand_notes
        d.validation_note = _txt(s.validation_note) or d.validation_note
        if s.validation_ok is not None:
            d.validation_ok = s.validation_ok
        if s.materials is not None:
            d.materials = [
                {
                    "material": _txt(m.get("material")) or "Material",
                    "finish": _txt(m.get("finish")),
                    "thickness": _txt(m.get("thickness")),
                    "area": _txt(m.get("area")),
                    "slab_count": m.get("slab_count"),
                    "total_sf": m.get("total_sf"),
                    "serials": _txt(m.get("serials")),
                    "barcodes": _txt(m.get("barcodes")),
                    "lot": _txt(m.get("lot")),
                    "unit_price": m.get("unit_price"),
                    "extended_price": m.get("extended_price"),
                    "stock": False,
                    "job_id": None,
                    "job_name": None,
                    "moraware_url": None,
                }
                for m in s.materials
            ]
            # Every slip read feeds the materials catalog (slab scans
            # chapter): the typeahead grows from what we actually buy.
            try:
                upsert_materials(
                    db,
                    [(m.get("material"), _txt(s.supplier)) for m in s.materials
                     if _txt(m.get("material"))],
                    "delivery",
                )
            except Exception:  # noqa: BLE001 — catalog is best-effort
                logger.exception("materials catalog upsert failed")
        d.drive_file_id = _txt(s.drive_file_id) or d.drive_file_id
        d.drive_url = _txt(s.drive_url) or d.drive_url
        d.supplier_folder_id = _txt(s.supplier_folder_id) or d.supplier_folder_id
    # One delivery = ONE push: only the FIRST transition into needs_job may
    # push — reruns/resends of the workflow stay quiet (owner: "tons of
    # pushes" after retries, 2026-07-22).
    first_needs_job = (
        payload.status == "needs_job" and item.status != "needs_job"
    )
    if payload.status is not None:
        item.status = str(payload.status).strip()[:60] or item.status
    db.add(ItemEvent(review_item_id=item.id, kind="bot_update", body=body))
    db.commit()

    sent = 0
    if payload.push and first_needs_job:
        title = (payload.push_title or "").strip() or (
            f"Delivery from {d.supplier or 'a supplier'} needs a job"
        )
        sent, _ = push_to_pilot_pool(
            db, title=title, body=body, url=f"/deliveries/item/{item.id}"
        )
    return {"ok": True, "pushed": sent}


class SlabFinalIn(BaseModel):
    review_item_id: uuid.UUID
    body: str
    status: str
    push: bool = False


@router.post("/final")
def slab_final(payload: SlabFinalIn, db: Session = Depends(get_db)) -> dict:
    item = _slab_item(db, payload.review_item_id)
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail={"error": "invalid",
                                                     "message": "body must not be empty."})
    item.status = str(payload.status).strip()[:60]
    db.add(ItemEvent(review_item_id=item.id, kind="system", body=body))
    db.commit()
    sent = 0
    if payload.push:
        sent, _ = push_to_pilot_pool(
            db, title=body, body="Tap to view", url=f"/deliveries/item/{item.id}"
        )
    return {"ok": True, "pushed": sent}


@router.get("/find")
def slab_find(
    supplier: str | None = Query(default=None),
    document_number: str | None = Query(default=None),
    total: str | None = Query(default=None),
    slab_count: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Dedup lookup, mirroring SLABBOT's rule: Supplier+Doc#, or
    Supplier+Total+SlabCount when the slip has no document number."""
    clauses = []
    if supplier is not None:
        clauses.append(DeliveryDetails.supplier == supplier.strip())
    if document_number is not None:
        clauses.append(DeliveryDetails.document_number == document_number.strip())
    if total is not None:
        parsed = _num(total)
        if parsed is not None:
            clauses.append(DeliveryDetails.total == parsed)
    if slab_count is not None:
        clauses.append(DeliveryDetails.slab_count == slab_count)
    if not clauses:
        raise HTTPException(status_code=422, detail={
            "error": "invalid",
            "message": "Provide at least one of: supplier, document_number, total, slab_count.",
        })
    items = (
        db.scalars(
            select(ReviewItem)
            .join(ReviewItem.delivery_details)
            .where(ReviewItem.item_type == "slab_delivery",
                   ReviewItem.source == "console", *clauses)
            .order_by(ReviewItem.created_at.desc())
        )
        .unique()
        .all()
    )
    return {
        "items": [
            {
                "review_item_id": str(i.id),
                "status": i.status,
                "supplier": i.delivery_details.supplier,
                "document_number": i.delivery_details.document_number,
                "total": str(i.delivery_details.total)
                if i.delivery_details.total is not None else None,
                "slab_count": i.delivery_details.slab_count,
                "created_at": i.created_at.isoformat(),
            }
            for i in items
        ]
    }


@router.get("/list")
def slab_list(db: Session = Depends(get_db)) -> dict:  # pragma: no cover - thin
    items = (
        db.scalars(
            select(ReviewItem)
            .where(ReviewItem.item_type == "slab_delivery",
                   ReviewItem.source == "console")
            .order_by(ReviewItem.created_at.desc())
            .limit(200)
        )
        .unique()
        .all()
    )
    return {"items": [{"review_item_id": str(i.id), "status": i.status}
                      for i in items]}
