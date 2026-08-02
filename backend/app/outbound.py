"""Outbound console → n8n trigger (decision flow plan §4).

Runs as a FastAPI background task after POST /api/checks succeeds — the upload
response NEVER waits on (or fails because of) the workflow. Every attempt
narrates itself into item_events, so a stuck item is visible on its card and
POST /api/review-items/{id}/resend (admin) can re-fire it.

No QuickBooks anything here: the payload carries the manager's qb_invoice
digits as an opaque string; what the workflow does with them is its business.
"""

import base64
import io
import logging
import uuid

import httpx
from PIL import Image, ImageOps

from app.config import get_settings
from app.db import SessionLocal
from app.models import ItemEvent, ReviewItem

logger = logging.getLogger(__name__)

MAX_SIDE = 1568  # vision-friendly longest side for the workflow's OCR
JPEG_QUALITY = 85
SEND_TIMEOUT_SECONDS = 30

SENT_BODY = "Sent to CHECK-BOT."
FAILED_BODY = "Couldn't reach the workflow — will need manual retry."


def encode_photo(path: str) -> str:
    """Stored original → base64 JPEG, longest side ≤1568px, q85.

    The original stays untouched on disk (the Lightbox keeps full quality);
    this downscaled copy exists only inside the outbound payload.
    """
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)  # honor the camera's orientation tag
        img.thumbnail((MAX_SIDE, MAX_SIDE))  # only ever downscales
        if img.mode != "RGB":
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode()


def send_check_to_workflow(review_item_id: uuid.UUID, submitted_by: str) -> None:
    """POST the submitted check to the cloned CHECK-BOT's webhook.

    Own session on purpose: the request's session is gone by the time a
    background task runs. Never raises — success or failure both end as a
    system line in the item's feed.
    """
    settings = get_settings()
    if not settings.n8n_pilot_webhook_url:
        return  # dev without n8n: trigger disabled (plan §2)

    db = SessionLocal()
    try:
        item = db.get(ReviewItem, review_item_id)
        if item is None or not item.photo_path:
            return
        ok = False
        try:
            payload = {
                "review_item_id": str(item.id),
                "image_base64": encode_photo(item.photo_path),
                "qb_invoice": (
                    item.payment_details.qb_invoice if item.payment_details else None
                ),
                "submitted_by": submitted_by,
                "secret": settings.pilot_hook_secret,
            }
            response = httpx.post(
                settings.n8n_pilot_webhook_url,
                json=payload,
                timeout=SEND_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            ok = True
        except Exception:  # noqa: BLE001 — any failure = the same feed line
            logger.exception("outbound trigger failed for %s", review_item_id)
        if ok:
            # The board/card render this as "in progress" until the workflow's
            # question or final hook moves the status on.
            item.status = "processing"
        db.add(
            ItemEvent(
                review_item_id=item.id,
                kind="system",
                body=SENT_BODY if ok else FAILED_BODY,
            )
        )
        db.commit()
    finally:
        db.close()
