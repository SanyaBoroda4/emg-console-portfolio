"""Slab deliveries (slab chapter): upload + the card's assignment actions.

The manager does the deciding HERE (typeahead picker, stock, one-vs-split
poll) — the workflow only reads the slip, files the photo, and, on confirm,
posts the Moraware notes. One slip = one row = one card = one push.
"""

import base64
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    UploadFile,
    status,
)
from PIL import Image, ImageOps
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import DeliveryDetails, ItemEvent, ReviewItem, User
from app.schemas import ReviewItemOut

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/deliveries",
    tags=["deliveries"],
    dependencies=[Depends(require_role("admin", "manager"))],
)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MEDIA_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}
MAX_SIDE = 1568
JPEG_QUALITY = 85
SEND_TIMEOUT_SECONDS = 30
CONFIRM_TIMEOUT_SECONDS = 20


def _encode_photo(path: str) -> str:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        if img.mode != "RGB":
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode()


def send_delivery_to_workflow(review_item_id: uuid.UUID, submitted_by: str) -> None:
    """Background task: POST the slip to the SLABBOT clone's webhook."""
    settings = get_settings()
    if not settings.n8n_slab_webhook_url:
        return
    db = SessionLocal()
    try:
        item = db.get(ReviewItem, review_item_id)
        if item is None or not item.photo_path:
            return
        ok = False
        try:
            response = httpx.post(
                settings.n8n_slab_webhook_url,
                json={
                    "review_item_id": str(item.id),
                    "image_base64": _encode_photo(item.photo_path),
                    "submitted_by": submitted_by,
                    "secret": settings.pilot_hook_secret,
                },
                timeout=SEND_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            ok = True
        except Exception:  # noqa: BLE001
            logger.exception("slab outbound failed for %s", review_item_id)
        if ok:
            item.status = "processing"
        db.add(
            ItemEvent(
                review_item_id=item.id,
                kind="system",
                body="Sent to SLABBOT." if ok else
                "Couldn't reach the workflow — will need manual retry.",
            )
        )
        db.commit()
    finally:
        db.close()


@router.post("", response_model=ReviewItemOut, status_code=status.HTTP_201_CREATED)
async def submit_delivery(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewItem:
    suffix = MEDIA_TYPES.get(file.content_type or "")
    if suffix is None:
        raise HTTPException(415, detail="Only JPEG or PNG photos are accepted.")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail="Photo is too large (max 15 MB).")

    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / f"{uuid.uuid4()}{suffix}"
    destination.write_bytes(content)

    item = ReviewItem(
        item_type="slab_delivery",
        status="submitted",
        source="console",
        photo_path=str(destination),
        raw={},
        delivery_details=DeliveryDetails(),
    )
    db.add(item)
    db.flush()
    db.add(
        ItemEvent(
            review_item_id=item.id,
            kind="system",
            body="Delivery slip photographed in the console.",
            actor_email=user.email,
        )
    )
    db.commit()
    db.refresh(item)
    background_tasks.add_task(send_delivery_to_workflow, item.id, user.email)
    return item


def _delivery(db: Session, review_item_id: uuid.UUID) -> ReviewItem:
    item = db.get(ReviewItem, review_item_id)
    if item is None or item.item_type != "slab_delivery":
        raise HTTPException(404, detail={"error": "not_found"})
    if item.delivery_details is None:  # defensive
        item.delivery_details = DeliveryDetails()
    return item


class ModeIn(BaseModel):
    mode: str  # 'one' | 'split'


@router.post("/{review_item_id}/mode")
def set_mode(
    review_item_id: uuid.UUID,
    payload: ModeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if payload.mode not in ("one", "split"):
        raise HTTPException(422, detail={"error": "invalid",
                                         "message": "mode must be 'one' or 'split'."})
    item = _delivery(db, review_item_id)
    item.delivery_details.assignment_mode = payload.mode
    db.add(ItemEvent(
        review_item_id=item.id, kind="system",
        body=("One job for the whole slip." if payload.mode == "one"
              else "Different jobs per material — assigning one by one."),
        actor_email=user.email,
    ))
    db.commit()
    return {"ok": True}


class AssignIn(BaseModel):
    # None = assign every material at once (mode 'one').
    material_index: int | None = None
    stock: bool = False
    job_id: str | None = None
    job_name: str | None = None
    moraware_url: str | None = None


@router.post("/{review_item_id}/assign")
def assign_material(
    review_item_id: uuid.UUID,
    payload: AssignIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not payload.stock and not (payload.job_id and payload.job_name):
        raise HTTPException(422, detail={"error": "invalid",
                                         "message": "Pick a job or choose Stock."})
    item = _delivery(db, review_item_id)
    details = item.delivery_details
    materials: list[dict[str, Any]] = list(details.materials or [])
    if not materials:
        raise HTTPException(409, detail={"error": "not_ready",
                                         "message": "SLABBOT hasn't read the slip yet."})

    targets = (range(len(materials)) if payload.material_index is None
               else [payload.material_index])
    if payload.material_index is not None and not (
        0 <= payload.material_index < len(materials)
    ):
        raise HTTPException(422, detail={"error": "invalid",
                                         "message": "No such material."})

    stamp = {
        "stock": payload.stock,
        "job_id": None if payload.stock else payload.job_id,
        "job_name": None if payload.stock else payload.job_name,
        "moraware_url": None if payload.stock else payload.moraware_url,
        "assigned_by": user.email,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
    }
    for i in targets:
        materials[i] = {**materials[i], **stamp}
    details.materials = materials

    label = "Stock" if payload.stock else (payload.job_name or "job")
    what = ("all materials" if payload.material_index is None
            else materials[payload.material_index].get("material") or
            f"material {payload.material_index + 1}")
    db.add(ItemEvent(review_item_id=item.id, kind="system",
                     body=f"{what} → {label}.", actor_email=user.email))
    db.commit()
    assigned = sum(1 for m in materials if m.get("stock") or m.get("job_id"))
    return {"ok": True, "assigned": assigned, "total": len(materials)}


@router.post("/{review_item_id}/confirm")
def confirm_delivery(
    review_item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """All materials assigned → hand the complete mapping to the workflow
    (Moraware notes + Drive filing + the quiet final)."""
    item = _delivery(db, review_item_id)
    details = item.delivery_details
    materials = list(details.materials or [])
    unassigned = [m for m in materials if not (m.get("stock") or m.get("job_id"))]
    if not materials or unassigned:
        raise HTTPException(409, detail={
            "error": "unassigned",
            "message": f"{len(unassigned) or 'All'} material(s) still need a job or Stock.",
        })

    settings = get_settings()
    if not settings.n8n_slab_decision_url:
        raise HTTPException(502, detail={
            "error": "workflow_unreachable",
            "message": "The slab workflow isn't configured — nothing was finalized.",
        })

    jobs: dict[str, dict] = {}
    stock: list[dict] = []
    for m in materials:
        entry = {"material": m.get("material"), "slab_count": m.get("slab_count"),
                 "total_sf": m.get("total_sf")}
        if m.get("stock"):
            stock.append(entry)
        else:
            job = jobs.setdefault(str(m["job_id"]), {
                "job_id": str(m["job_id"]), "job_name": m.get("job_name"),
                "moraware_url": m.get("moraware_url"), "materials": [],
            })
            job["materials"].append(entry)

    body = {
        "secret": settings.pilot_hook_secret,
        "review_item_id": str(item.id),
        "supplier": details.supplier,
        "document_number": details.document_number,
        "received": item.created_at.date().isoformat(),
        "slab_count": details.slab_count,
        "drive_file_id": details.drive_file_id,
        "supplier_folder_id": details.supplier_folder_id,
        "all_stock": bool(stock) and not jobs,
        "jobs": list(jobs.values()),
        "stock_materials": stock,
        "confirmed_by": user.email,
    }
    try:
        response = httpx.post(settings.n8n_slab_decision_url, json=body,
                              timeout=CONFIRM_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception:  # noqa: BLE001
        logger.exception("slab confirm delivery failed for %s", item.id)
        raise HTTPException(502, detail={
            "error": "workflow_unreachable",
            "message": "The workflow couldn't be reached — nothing was finalized. "
            "Your assignments are saved; try Confirm again in a moment.",
        })

    # Job identity on the item: single job shows it directly; several show a
    # summary (the table's Job column reads these).
    if len(jobs) == 1 and not stock:
        only = next(iter(jobs.values()))
        item.matched_job_id = only["job_id"]
        item.matched_job_name = only["job_name"]
        item.moraware_url = only["moraware_url"]
    elif not jobs and stock:
        item.matched_job_name = "Stock"
    elif jobs:
        item.matched_job_name = f"{len(jobs)} jobs" + (" + stock" if stock else "")
    # The card hides the Register button and shows "filing…" until the
    # workflow's final hook flips the status to confirmed/stock.
    item.status = "filing"
    db.add(ItemEvent(review_item_id=item.id, kind="decision",
                     body="Assignments registered — filing to Moraware.",
                     actor_email=user.email))
    db.commit()
    return {"ok": True}


@router.post("/{review_item_id}/resend", status_code=202,
             dependencies=[Depends(require_role("admin"))])
def resend_delivery(
    review_item_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Re-fire the outbound slip trigger for a stuck delivery — the recovery
    path when the workflow URL was missing/down at upload time."""
    item = _delivery(db, review_item_id)
    if not item.photo_path:
        raise HTTPException(409, detail={"error": "no_photo",
                                         "message": "This delivery has no photo."})
    if not get_settings().n8n_slab_webhook_url:
        raise HTTPException(502, detail={
            "error": "outbound_disabled",
            "message": "N8N_SLAB_WEBHOOK_URL isn't configured on the server.",
        })
    background_tasks.add_task(send_delivery_to_workflow, item.id, user.email)
    return {"ok": True}
