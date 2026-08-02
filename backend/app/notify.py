"""Pilot push fan-out (decision flow plan §5): question and resolution pushes
go to PILOT_PUSH_EMAILS ∩ roles admin/manager.

Lives outside the routers so the inbound hooks and the outbound trigger share
one implementation. Tests mock `app.notify.webpush`. Deliberately never raises:
a push hiccup must not fail the hook (or an upload) that triggered it.
"""

import json
import logging

from py_vapid import Vapid02
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PushSubscription, User

logger = logging.getLogger(__name__)


def push_to_pilot_pool(
    db: Session, *, title: str, body: str, url: str
) -> tuple[int, int]:
    """Push {title, body, url} to every subscription of the pilot pool.

    Pool = PILOT_PUSH_EMAILS ∩ roster roles admin/manager — the intersection is
    enforced here so a stale email in config can never widen the audience.
    Returns (sent, pruned). Call AFTER the caller has committed its own work:
    this commits (for dead-subscription pruning) and must not flush half-done
    hook state.
    """
    settings = get_settings()
    pool = settings.pilot_push_email_list
    if not pool or not (settings.vapid_private_key and settings.vapid_public_key):
        return (0, 0)

    allowed = set(
        db.scalars(
            select(User.email).where(
                User.email.in_(pool), User.role.in_(("admin", "manager"))
            )
        ).all()
    )
    if not allowed:
        return (0, 0)
    subs = (
        db.scalars(
            select(PushSubscription).where(PushSubscription.user_email.in_(allowed))
        )
        .unique()
        .all()
    )

    vapid = Vapid02.from_raw(settings.vapid_private_key.encode())
    payload = json.dumps({"title": title, "body": body, "url": url})
    sent = 0
    pruned = 0
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
                # ttl=0 (the library default) means "deliver this instant or
                # silently drop" — locked phones miss it. Learned the hard way.
                ttl=3600,
                headers={"Urgency": "high"},
            )
            sent += 1
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):
                db.delete(sub)  # the browser dropped this subscription
                pruned += 1
            else:
                logger.warning("pilot push to %s failed: %s", sub.user_email, exc)
        except Exception:  # noqa: BLE001 — push must never take down the caller
            logger.exception("pilot push to %s crashed", sub.user_email)
    db.commit()
    logger.info(
        "pilot push %r: sent=%d pruned=%d pool=%s", title, sent, pruned, sorted(allowed)
    )
    return (sent, pruned)
