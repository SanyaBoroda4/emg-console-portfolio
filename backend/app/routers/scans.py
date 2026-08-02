"""Slab scans (slab scans chapter): Wade photographs printed slab labels,
the QR codes are decoded in the browser, and on Register the console posts
ONE appended note to the Moraware Job Details form (bottom Notes box) via
the bridge. One card = one scanning session = one job, 1+ slabs.

No n8n here: QR decoding is client-side, the OCR fallback is a direct
Claude vision call, and Moraware writes go through the bridge.
"""

import base64
import io
import logging
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.config import get_settings
from app.db import get_db
from app.models import ItemEvent, ReviewItem, ScanDetails, User
from app.routers.materials import upsert_materials
from app.schemas import ReviewItemOut

logger = logging.getLogger(__name__)

# 'yard' (scanner staff like Wade) get this section and NOTHING else —
# scans, plus the job/material typeahead endpoints they need. Payments and
# deliveries stay manager-only.
router = APIRouter(
    prefix="/api/scans",
    tags=["scans"],
    dependencies=[Depends(require_role("admin", "manager", "yard"))],
)


@router.get("/list")
def list_scans(db: Session = Depends(get_db)) -> dict:
    """The scans board — yard-safe (the general review-items list stays
    manager-only so scanner staff never see payments)."""
    items = (
        db.scalars(
            select(ReviewItem)
            .where(ReviewItem.item_type == "slab_scan")
            .order_by(ReviewItem.created_at.desc())
            .limit(200)
        )
        .unique()
        .all()
    )
    return {
        "items": [ReviewItemOut.model_validate(i).model_dump(mode="json")
                  for i in items],
        "total": len(items),
    }


@router.get("/{review_item_id}/card")
def scan_card(
    review_item_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """One scan card + its activity feed, yard-safe."""
    item = _scan(db, review_item_id)
    events = (
        db.scalars(
            select(ItemEvent)
            .where(ItemEvent.review_item_id == item.id)
            .order_by(ItemEvent.created_at, ItemEvent.id)
        )
        .unique()
        .all()
    )
    return {
        "item": ReviewItemOut.model_validate(item).model_dump(mode="json"),
        "events": [
            {
                "id": str(e.id),
                "kind": e.kind,
                "body": e.body,
                "actor_email": e.actor_email,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }

EASTERN = ZoneInfo("America/New_York")
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_SIDE = 1568
BRIDGE_TIMEOUT_SECONDS = 25
OCR_TIMEOUT_SECONDS = 45
# Slab IDs are the printed numbers next to "ID:" on the label (e.g. 2287478).
SLAB_ID_RE = re.compile(r"^\d{5,9}$")


class SlabId(BaseModel):
    id: str = Field(min_length=5, max_length=9, pattern=r"^\d+$")
    source: str = "manual"  # 'qr' | 'ocr' | 'manual'
    material: str | None = Field(default=None, max_length=120)


class ScanCreateIn(BaseModel):
    slab_ids: list[SlabId] = []


class SlabListIn(BaseModel):
    slab_ids: list[SlabId]


class AssignIn(BaseModel):
    job_id: int
    job_name: str
    moraware_url: str | None = None


def _dedupe(slabs: list[SlabId]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for s in slabs:
        if s.id in seen:
            continue
        seen.add(s.id)
        out.append({"id": s.id, "source": s.source,
                    "material": (" ".join(s.material.split())
                                 if s.material else None)})
    return out


def _scan(db: Session, review_item_id: uuid.UUID) -> ReviewItem:
    item = db.get(ReviewItem, review_item_id)
    if item is None or item.item_type != "slab_scan":
        raise HTTPException(404, detail={"error": "not_found"})
    if item.scan_details is None:  # defensive
        item.scan_details = ScanDetails(slab_ids=[])
    return item


def _used_slab_ids(db: Session, exclude: uuid.UUID | None = None) -> dict[str, str]:
    """Map every slab ID currently on ANY scan card -> that card's id (slab
    numbers are globally unique, so the same one can't live on two cards).
    Deleted cards are gone from review_items, so their IDs free up."""
    rows = db.execute(
        select(ScanDetails.review_item_id, ScanDetails.slab_ids)
    ).all()
    used: dict[str, str] = {}
    for review_item_id, slab_ids in rows:
        if exclude is not None and review_item_id == exclude:
            continue
        for s in (slab_ids or []):
            sid = s.get("id") if isinstance(s, dict) else None
            if sid:
                used[sid] = str(review_item_id)
    return used


def _reject_cross_card_dups(
    db: Session, slabs: list[dict], exclude: uuid.UUID | None
) -> None:
    used = _used_slab_ids(db, exclude=exclude)
    dups = [s["id"] for s in slabs if s["id"] in used]
    if dups:
        raise HTTPException(409, detail={
            "error": "duplicate_slabs",
            "message": (f"Already scanned on another card: {', '.join(dups[:5])}"
                        + (" …" if len(dups) > 5 else "")),
            "duplicates": dups,
        })


@router.get("/used-ids")
def used_ids(db: Session = Depends(get_db)) -> dict:
    """Every slab ID already on a card — the scanner preloads this to block
    a repeat scan instantly, before it even lands on the card."""
    return {"ids": sorted(_used_slab_ids(db).keys())}


@router.post("", response_model=ReviewItemOut, status_code=status.HTTP_201_CREATED)
def create_scan(
    payload: ScanCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewItem:
    slabs = _dedupe(payload.slab_ids)
    _reject_cross_card_dups(db, slabs, exclude=None)
    item = ReviewItem(
        item_type="slab_scan",
        status="pending",
        source="console",
        raw={},
        scan_details=ScanDetails(
            slab_ids=slabs,
            scanned_date=datetime.now(EASTERN).date(),
        ),
    )
    db.add(item)
    db.flush()
    db.add(
        ItemEvent(
            review_item_id=item.id,
            kind="system",
            body=f"Scanned {len(slabs)} slab label(s) in the console.",
            actor_email=user.email,
        )
    )
    db.commit()
    db.refresh(item)
    return item


@router.put("/{review_item_id}/slabs")
def update_slabs(
    review_item_id: uuid.UUID,
    payload: SlabListIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    item = _scan(db, review_item_id)
    if item.status == "confirmed":
        raise HTTPException(409, detail={
            "error": "already_confirmed",
            "message": "This scan was already posted to Moraware.",
        })
    slabs = _dedupe(payload.slab_ids)
    _reject_cross_card_dups(db, slabs, exclude=item.id)
    item.scan_details.slab_ids = slabs
    db.commit()
    return {"ok": True, "count": len(slabs)}


@router.post("/{review_item_id}/assign")
def assign_job(
    review_item_id: uuid.UUID,
    payload: AssignIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    item = _scan(db, review_item_id)
    if item.status == "confirmed":
        raise HTTPException(409, detail={
            "error": "already_confirmed",
            "message": "This scan was already posted to Moraware.",
        })
    item.matched_job_id = str(payload.job_id)
    item.matched_job_name = payload.job_name
    item.moraware_url = payload.moraware_url
    db.add(ItemEvent(
        review_item_id=item.id, kind="system",
        body=f"Job picked: {payload.job_name}.", actor_email=user.email,
    ))
    db.commit()
    return {"ok": True}


def _note_text(item: ReviewItem) -> str:
    details = item.scan_details
    scanned = details.scanned_date or item.created_at.date()
    date_str = f"{scanned:%b} {scanned.day}, {scanned.year}"
    lines = [
        f"{s['material']} — {s['id']}" if s.get("material") else s["id"]
        for s in (details.slab_ids or [])
    ]
    return f"Slabs scanned {date_str}:\n" + "\n".join(lines)


@router.post("/{review_item_id}/confirm")
def confirm_scan(
    review_item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    item = _scan(db, review_item_id)
    if item.status == "confirmed":
        raise HTTPException(409, detail={
            "error": "already_confirmed",
            "message": "This scan was already posted to Moraware.",
        })
    slabs = list(item.scan_details.slab_ids or [])
    if not slabs:
        raise HTTPException(409, detail={
            "error": "no_slabs",
            "message": "No slab numbers on this card yet.",
        })
    if not item.matched_job_id:
        raise HTTPException(409, detail={
            "error": "no_job",
            "message": "Pick a job first.",
        })
    unnamed = [s["id"] for s in slabs if not s.get("material")]
    if unnamed:
        raise HTTPException(409, detail={
            "error": "no_material",
            "message": (f"{len(unnamed)} slab(s) still need a material name: "
                        + ", ".join(unnamed[:5])),
        })

    settings = get_settings()
    if not settings.bridge_console_key:
        raise HTTPException(502, detail={
            "error": "bridge_unconfigured",
            "message": "The Moraware bridge isn't configured — nothing was posted.",
        })

    text = _note_text(item)
    try:
        response = httpx.post(
            settings.bridge_base_url + "/api/console/job-form-note",
            headers={"X-Console-Key": settings.bridge_console_key},
            # form="summary" -> Job Summary form's Notes (owner 2026-07-25);
            # the bridge still defaults to Details if it doesn't know the field.
            json={"jobId": int(item.matched_job_id), "text": text,
                  "form": "summary"},
            timeout=BRIDGE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001
        logger.exception("scan confirm bridge post failed for %s", item.id)
        db.add(ItemEvent(
            review_item_id=item.id, kind="system",
            body="Couldn't reach Moraware — nothing was posted. Try Register again.",
            actor_email=user.email,
        ))
        db.commit()
        raise HTTPException(502, detail={
            "error": "bridge_unreachable",
            "message": "Moraware couldn't be reached — nothing was posted. "
            "Your slab list is saved; try Register again in a moment.",
        })

    try:
        upsert_materials(
            db,
            [(s["material"], None) for s in slabs if s.get("material")],
            "manual",
        )
    except Exception:  # noqa: BLE001 — catalog is best-effort
        logger.exception("materials catalog upsert failed on scan confirm")
    item.status = "confirmed"
    db.add(ItemEvent(
        review_item_id=item.id, kind="decision",
        body=(f"Posted {len(slabs)} slab ID(s) to Moraware — "
              f"{item.matched_job_name}."),
        actor_email=user.email,
    ))
    db.commit()
    return {"ok": True, "note": text}


# --- OCR fallback (labels whose QR was cut off / unreadable) ---------------

OCR_PROMPT = (
    "This photo shows one or more printed slab labels. Each label has a row "
    "'ID :' followed by a number (5-9 digits). List every ID number you can "
    "read, separated by commas. Reply with ONLY the numbers, or NONE if you "
    "cannot read any."
)


def _prep_image(content: bytes) -> str:
    with Image.open(io.BytesIO(content)) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        if img.mode != "RGB":
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode()


@router.post("/ocr")
async def ocr_label(file: UploadFile) -> dict:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(502, detail={
            "error": "ocr_unconfigured",
            "message": "Label reading isn't configured on the server.",
        })
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail="Photo is too large (max 15 MB).")

    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": settings.anthropic_ocr_model,
                "max_tokens": 300,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": _prep_image(content),
                        }},
                        {"type": "text", "text": OCR_PROMPT},
                    ],
                }],
            },
            timeout=OCR_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        text = "".join(
            block.get("text", "")
            for block in response.json().get("content", [])
        )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("scan OCR call failed")
        raise HTTPException(502, detail={
            "error": "ocr_failed",
            "message": "Couldn't read the label — add the number manually.",
        })

    ids = [m for m in re.findall(r"\d{5,9}", text)]
    # preserve order, drop duplicates
    seen: set[str] = set()
    unique = [i for i in ids if not (i in seen or seen.add(i))]
    return {"ids": unique}
