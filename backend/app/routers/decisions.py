"""Human-facing decision flow (plan §6): the decision itself, the item's
activity feed, and comments. Session-authenticated, payments roles.

The two guarantees that make this trustworthy:
1. FIRST TAP WINS, enforced by the database — inserting the decision event hits
   the partial unique index (one kind='decision' row per item), so a race
   between two managers becomes an IntegrityError → a clean 409 naming the
   winner. No application-level politeness involved.
2. COMPENSATING ROLLBACK — if the workflow's resume_url can't be reached, the
   just-inserted decision event is DELETED and the caller gets a 502: a
   decision the workflow never heard about must not exist. The card stays
   answerable.
"""

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.auth import get_current_user, require_role
from app.config import get_settings
from app.db import get_db
from app.models import ItemEvent, ReviewItem, User
from app.schemas import ReviewItemOut

router = APIRouter(
    prefix="/api/review-items",
    tags=["decisions"],
    dependencies=[Depends(require_role("admin", "manager"))],
)

RESUME_TIMEOUT_SECONDS = 15
COMMENT_MAX_LEN = 1000


def _get_item(db: Session, review_item_id: uuid.UUID) -> ReviewItem:
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such review item.")
    return item


def _latest_question(db: Session, item_id: uuid.UUID) -> ItemEvent | None:
    return db.scalar(
        select(ItemEvent)
        .where(ItemEvent.review_item_id == item_id, ItemEvent.kind == "bot_question")
        .order_by(ItemEvent.created_at.desc())
        .limit(1)
    )


def _decision_for(db: Session, question_id: uuid.UUID) -> ItemEvent | None:
    """The decision answering one specific question — multi-round aware."""
    return db.scalar(
        select(ItemEvent).where(
            ItemEvent.kind == "decision",
            ItemEvent.answers_event_id == question_id,
        )
    )


def _already_decided(decision: ItemEvent) -> HTTPException:
    """The race-loser response: name the winner so the UI can say so calmly."""
    return HTTPException(
        status_code=409,
        detail={
            "error": "already_decided",
            "message": "Someone already answered this one.",
            "decided_by": decision.actor_email,
            "decided_at": decision.created_at.isoformat(),
            "body": decision.body,
        },
    )


class ChoiceIn(BaseModel):
    label: str
    job_id: str | None = None


class DecisionIn(BaseModel):
    # Exactly one of these:
    choice: ChoiceIn | None = None
    text: str | None = None


@router.post("/{review_item_id}/decision")
def decide(
    review_item_id: uuid.UUID,
    payload: DecisionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # -- validate the body shape -------------------------------------------
    text = payload.text.strip() if payload.text else None
    if (payload.choice is None) == (text is None):  # both or neither
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid",
                    "message": "Send exactly one of: choice, text."},
        )

    item = _get_item(db, review_item_id)
    question = _latest_question(db, item.id)
    if question is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "no_open_question",
                    "message": "This item has no question to answer."},
        )
    q_payload: dict[str, Any] = question.payload or {}
    if text is not None and not q_payload.get("allowed_freeform", True):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid",
                    "message": "This question only accepts one of its options."},
        )

    # Fast pre-check for the common non-race case; the unique index below
    # remains the actual authority. Paired per QUESTION, so an answered
    # round doesn't block answering a newer question.
    existing = _decision_for(db, question.id)
    if existing is not None:
        raise _already_decided(existing)

    # -- (2) INSERT the decision; the partial unique index referees races ---
    body = f"Chose {payload.choice.label}" if payload.choice else f"Replied: {text}"
    decision = ItemEvent(
        review_item_id=item.id,
        kind="decision",
        body=body,
        payload=(
            {"choice": payload.choice.model_dump()} if payload.choice
            else {"text": text}
        ),
        actor_email=user.email,
        answers_event_id=question.id,
    )
    db.add(decision)
    try:
        db.commit()
    except IntegrityError:
        # Two taps raced; the database picked the winner. Report who.
        db.rollback()
        winner = _decision_for(db, question.id)
        if winner is not None:
            raise _already_decided(winner)
        raise  # unique violation but no row? — genuinely unexpected, surface it

    # -- (3) tell the workflow ----------------------------------------------
    resume_url = q_payload.get("resume_url", "")
    resume_body: dict[str, Any] = {"secret": get_settings().pilot_hook_secret}
    if payload.choice:
        resume_body["choice"] = payload.choice.model_dump()
    else:
        resume_body["text"] = text
    try:
        response = httpx.post(
            resume_url, json=resume_body, timeout=RESUME_TIMEOUT_SECONDS
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 — any failure means the same thing here
        # -- (4) compensating rollback: a decision the workflow never heard
        # about must not exist. Delete it and tell the human nothing happened.
        db.delete(decision)
        db.commit()
        raise HTTPException(
            status_code=502,
            detail={
                "error": "workflow_unreachable",
                "message": "The workflow couldn't be reached — nothing was "
                "recorded. Try again in a moment.",
            },
        )

    # Success appends nothing extra: the workflow's own /final hook narrates
    # the outcome into the feed.
    return {"ok": True, "decided_by": user.email, "body": body}


@router.get("/needs-decision")
def needs_decision_ids(db: Session = Depends(get_db)) -> dict:
    """Ids of items with an open question (a bot_question no decision answers)
    — the payments board decorates its cards/rows with a chip from this."""
    question = aliased(ItemEvent)
    answered = exists().where(
        ItemEvent.kind == "decision",
        ItemEvent.answers_event_id == question.id,
    )
    ids = db.scalars(
        select(question.review_item_id)
        .where(question.kind == "bot_question", ~answered)
        .distinct()
    ).all()
    return {"ids": [str(i) for i in ids]}


# --- the feed -----------------------------------------------------------------


@router.get("/{review_item_id}/events")
def list_events(
    review_item_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """The ordered feed PLUS the item itself — one call refreshes the whole
    card, so the 5s poll stays a single request."""
    item = _get_item(db, review_item_id)
    events = (
        db.scalars(
            select(ItemEvent)
            .where(ItemEvent.review_item_id == item.id)
            .order_by(ItemEvent.created_at, ItemEvent.id)
        )
        .unique()
        .all()
    )

    def public_payload(event: ItemEvent) -> dict | None:
        if event.payload is None:
            return None
        # resume_url is server business (the decision endpoint reads it from
        # the DB) — the browser never needs it.
        return {k: v for k, v in event.payload.items() if k != "resume_url"}

    return {
        "item": ReviewItemOut.model_validate(item).model_dump(mode="json"),
        "events": [
            {
                "id": str(e.id),
                "kind": e.kind,
                "body": e.body,
                "payload": public_payload(e),
                "actor_email": e.actor_email,
                "answers_event_id": (
                    str(e.answers_event_id) if e.answers_event_id else None
                ),
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }


# --- comments -------------------------------------------------------------------


class CommentIn(BaseModel):
    body: str


@router.post("/{review_item_id}/comments", status_code=201)
def add_comment(
    review_item_id: uuid.UUID,
    payload: CommentIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    item = _get_item(db, review_item_id)
    body = payload.body.strip()
    if not body or len(body) > COMMENT_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid",
                    "message": f"Comment must be 1-{COMMENT_MAX_LEN} characters."},
        )
    event = ItemEvent(
        review_item_id=item.id, kind="comment", body=body, actor_email=user.email
    )
    db.add(event)
    db.commit()
    # No push for comments in v1 (plan §6).
    return {"ok": True, "id": str(event.id)}
