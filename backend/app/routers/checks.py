"""POST /api/checks — console-captured check photos — and
GET /api/photos/{review_item_id} to serve them back.

Uploads never touch Airtable/Moraware/QuickBooks; they only create local
rows (source=console, status=submitted) and a file under upload_dir.
"""

import re
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.config import get_settings
from app.db import get_db
from app.models import ItemEvent, PaymentDetails, ReviewItem, User
from app.outbound import send_check_to_workflow
from app.schemas import ReviewItemOut

# Check submission and photo serving are payments features — admin/manager
# only, same wall as /api/review-items (STAGE2_BUILD_PLAN.md §3).
router = APIRouter(
    prefix="/api",
    tags=["checks"],
    dependencies=[Depends(require_role("admin", "manager"))],
)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB
CHUNK_BYTES = 1024 * 1024
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}
MEDIA_TYPES = {".jpg": "image/jpeg", ".png": "image/png"}


@router.post("/checks", response_model=ReviewItemOut, status_code=status.HTTP_201_CREATED)
async def submit_check(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    qb_invoice: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewItem:
    # Validate the fast-path field BEFORE the file touches disk (no orphan
    # files on a 422). Client restricts input too; the server is the law.
    qb_invoice = (qb_invoice or "").strip() or None
    if qb_invoice is not None and not re.fullmatch(r"\d{4}", qb_invoice):
        raise HTTPException(
            status_code=422,
            detail="QB invoice # must be exactly 4 digits (or left empty).",
        )

    extension = ALLOWED_TYPES.get(file.content_type or "")
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG or PNG images are accepted.",
        )

    upload_dir = Path(get_settings().upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / f"{uuid.uuid4().hex}{extension}"

    # Stream to disk with a hard cap — never buffer 15 MB+ in memory.
    size = 0
    try:
        with destination.open("wb") as out:
            while chunk := await file.read(CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Image is larger than 15 MB.",
                    )
                out.write(chunk)
        if size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )
    except HTTPException:
        destination.unlink(missing_ok=True)  # no orphan files on rejection
        raise

    item = ReviewItem(
        item_type="payment",
        status="submitted",
        source="console",
        photo_path=str(destination),
        raw={},  # console-born rows have no Airtable payload
        # Payment date = the day the check was handed over (owner rule: for
        # console-captured checks they're the same). Eastern time, not server
        # UTC — an evening submission must not slip to tomorrow.
        payment_details=PaymentDetails(
            qb_invoice=qb_invoice,
            txn_date=datetime.now(ZoneInfo("America/New_York")).date(),
            # Camera submissions are checks; the workflow's cash branch
            # overwrites this to 'cash' when the photo turns out to be bills.
            payment_method="check",
        ),
    )
    db.add(item)
    db.flush()
    # First feed line. actor_email doubles as the card's "submitted by" fact
    # and as the submitted_by the resend endpoint replays.
    db.add(
        ItemEvent(
            review_item_id=item.id,
            kind="system",
            body=(
                f"Check submitted with QB invoice {qb_invoice}."
                if qb_invoice
                else "Check submitted."
            ),
            actor_email=user.email,
        )
    )
    db.commit()
    db.refresh(item)
    # AFTER the response is sent: fire the workflow trigger (no-op when
    # N8N_PILOT_WEBHOOK_URL is unset). Failures land in the feed, never here.
    background_tasks.add_task(send_check_to_workflow, item.id, user.email)
    return item


@router.get("/photos/{review_item_id}")
def get_photo(review_item_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    item = db.get(ReviewItem, review_item_id)
    if item is None or not item.photo_path:
        raise HTTPException(status_code=404, detail="No photo for this item.")
    path = Path(item.photo_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Photo file is missing.")
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream"))
