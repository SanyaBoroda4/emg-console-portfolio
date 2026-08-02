"""Web Push subscription endpoints (push mechanics slice §3).

This slice proves the push pipe only: authenticated browsers register a
PushSubscription here so they can be pushed to. The decision-card feed and the
n8n fan-out are a SEPARATE later stage. test-send (§4) lives here too but is a
self-service pipe check, not a broadcast tool.
"""

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from py_vapid import Vapid02
from pydantic import BaseModel
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.config import get_settings
from app.db import get_db
from app.models import PushSubscription, User

router = APIRouter(prefix="/api/push", tags=["push"])

logger = logging.getLogger(__name__)

TEST_NOTIFICATION = {
    "title": "EMG ops console",
    "body": "Test notification from EMG console — if you see this, push works!",
    "url": "/",
}


class SubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionIn(BaseModel):
    # The browser's PushSubscription.toJSON() shape.
    endpoint: str
    keys: SubscriptionKeys


class UnsubscribeIn(BaseModel):
    endpoint: str


@router.get("/vapid-public-key")
def vapid_public_key(user: User = Depends(get_current_user)) -> dict:
    """The browser's applicationServerKey. Public by design (same category as
    the Google client id) — any authenticated user may read it. Empty string
    when push is not configured; the frontend shows a 'not configured' state."""
    return {"key": get_settings().vapid_public_key}


@router.post(
    "/subscribe",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin", "manager"))],
)
def subscribe(
    body: SubscriptionIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Register (or refresh) this browser's subscription. Upsert by endpoint —
    re-subscribing from the same browser updates in place, never duplicates.
    Yard excluded (matches their lack of payments access)."""
    sub = db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
    ).scalar_one_or_none()
    if sub is None:
        sub = PushSubscription(endpoint=body.endpoint)
        db.add(sub)
    sub.user_email = user.email
    sub.p256dh = body.keys.p256dh
    sub.auth = body.keys.auth
    sub.user_agent = request.headers.get("user-agent")
    db.commit()
    return {"ok": True}


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(
    body: UnsubscribeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Remove a subscription by endpoint. 204 whether or not it existed
    (idempotent) — the browser calls this after it drops its own subscription."""
    db.query(PushSubscription).filter(
        PushSubscription.endpoint == body.endpoint
    ).delete()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class TestSendIn(BaseModel):
    # 'self' = only the caller's own devices; 'all' = every subscribed user
    # (owner-approved amendment: lets the admin verify anyone's phone works).
    scope: Literal["self", "all"] = "self"


@router.post("/test-send", dependencies=[Depends(require_role("admin"))])
def test_send(
    body: TestSendIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Push a fixed test notification. Admin only. scope='self' (default) hits
    the caller's own subscriptions; scope='all' hits EVERY subscription — the
    owner's phone-verification tool for onboarding managers. Dead endpoints
    (404/410 from the push service) are pruned. Returns {sent, pruned}.

    NOTE (push mechanics slice): this exists only to prove the pipe on real
    phones. Delete or lock it down once the real decision-card push lands.
    """
    settings = get_settings()
    if not (settings.vapid_private_key and settings.vapid_public_key):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "push_not_configured",
                "message": "Push is not configured on the server (no VAPID keys).",
            },
        )

    scope = body.scope if body else "self"
    query = select(PushSubscription)
    if scope == "self":
        query = query.where(PushSubscription.user_email == user.email)
    subs = db.execute(query).scalars().all()

    sender = user.display_name or user.email
    message = dict(
        TEST_NOTIFICATION,
        body=(
            TEST_NOTIFICATION["body"]
            if scope == "self"
            else f"Test to the whole team from {sender} — if you see this, push works!"
        ),
    )
    vapid = Vapid02.from_raw(settings.vapid_private_key.encode())
    payload = json.dumps(message)

    sent = 0
    pruned = 0
    # Per-recipient success counts, returned to the UI and logged — makes every
    # test click self-evident about who it actually targeted (verification
    # kept stalling on "which button did what").
    recipients: dict[str, int] = {}
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=vapid,
                vapid_claims={"sub": settings.vapid_subject},
                # TTL matters: pywebpush defaults to ttl=0 = "deliver this
                # instant or silently drop" — locked phones missed messages.
                # An hour gives the push service room to reach a sleeping phone.
                ttl=3600,
                headers={"Urgency": "high"},
            )
            sent += 1
            recipients[sub.user_email] = recipients.get(sub.user_email, 0) + 1
        except WebPushException as exc:
            # 404/410 = the browser dropped this subscription; prune it. Other
            # errors leave the row intact (the count mismatch signals a problem).
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):
                db.delete(sub)
                pruned += 1
    db.commit()
    # Shows up in App Service -> Log stream: forensics for "did my click send?"
    logger.info(
        "push test-send scope=%s by=%s sent=%d pruned=%d recipients=%s",
        scope, user.email, sent, pruned, recipients,
    )
    return {"sent": sent, "pruned": pruned, "recipients": recipients}
