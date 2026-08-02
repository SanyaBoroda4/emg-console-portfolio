"""Typeahead job search (slab deliveries stage §typeahead).

Serves the picker from the LOCAL jobs_directory copy — every keystroke gets
an answer in milliseconds regardless of bridge health. Ranking: word-prefix
matches first (typing "kah" should surface "Oliver Kahn" before "Prakah"),
then substring matches; newest jobs first within a rank.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import require_role
from app.db import get_db
from app.models import JobDirectory

router = APIRouter(
    prefix="/api/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_role("admin", "manager", "yard"))],
)

MIN_QUERY_LEN = 2
LIMIT = 8
SCAN_CAP = 60  # rank in Python over at most this many DB hits


@router.get("/search")
def search_jobs(
    q: str = Query(min_length=MIN_QUERY_LEN, max_length=80),
    db: Session = Depends(get_db),
) -> dict:
    tokens = [t for t in q.strip().lower().split() if t]
    if not tokens:
        return {"jobs": []}

    clauses = [JobDirectory.customer_name.ilike(f"%{t}%") for t in tokens]
    hits = db.scalars(
        select(JobDirectory).where(or_(*clauses)).limit(SCAN_CAP * 4)
    ).all()

    # Closed jobs never show up (owner 2026-07-24). Status arrives from the
    # bridge feed; rows synced before the bridge upgrade have NULL and pass.
    HIDDEN_STATUSES = {"done", "cancelled", "canceled"}
    hits = [j for j in hits
            if (j.status or "").strip().lower() not in HIDDEN_STATUSES]

    def rank(job: JobDirectory) -> tuple:
        name = job.customer_name.lower()
        words = name.split()
        # Every token must appear somewhere; prefix hits break ties.
        score = 0
        for t in tokens:
            if t not in name:
                return (9, 0, 0)  # filtered out below
            if any(w.startswith(t) for w in words):
                continue  # best: token starts a word
            score += 1  # substring-only match
        newest = job.creation_date.toordinal() if job.creation_date else 0
        # Most recently created jobs FIRST (owner 2026-07-24) — match
        # quality only breaks ties between same-day jobs.
        return (-newest, score, -job.job_id)

    def matches(job: JobDirectory) -> bool:
        name = job.customer_name.lower()
        return all(t in name for t in tokens)

    ranked = sorted((j for j in hits if matches(j)), key=rank)[:LIMIT]
    return {
        "jobs": [
            {
                "job_id": j.job_id,
                "customer_name": j.customer_name,
                "lead_url": j.lead_url,
                "creation_date": j.creation_date.isoformat() if j.creation_date else None,
            }
            for j in ranked
        ]
    }
