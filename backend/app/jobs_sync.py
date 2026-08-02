"""Jobs directory sync (slab deliveries stage).

Pulls the bridge's cached Moraware job list into the local jobs_directory
table so the typeahead picker searches Postgres (~ms), never the bridge
(~seconds). A daemon thread refreshes every jobs_sync_minutes; disabled when
bridge_console_key is empty (dev/tests).
"""

import logging
import threading
import time
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import JobDirectory

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 30


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def refresh_jobs_directory(db: Session) -> int:
    """Replace the local directory with the bridge's current cache.
    Returns the number of jobs stored. Raises on fetch failure — callers
    decide whether that is fatal (the thread just logs and retries)."""
    settings = get_settings()
    response = httpx.get(
        settings.bridge_base_url + "/api/console/job-directory",
        headers={"X-Console-Key": settings.bridge_console_key},
        timeout=FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    jobs = response.json().get("Jobs") or []
    now = datetime.now(timezone.utc)
    rows = [
        JobDirectory(
            job_id=int(j["JobId"]),
            customer_name=str(j.get("CustomerName") or "").strip() or f"Job {j['JobId']}",
            lead_url=j.get("LeadUrl"),
            creation_date=_parse_date(j.get("CreationDate")),
            status=(str(j.get("Status") or j.get("JobStatus") or "").strip()
                    or None),
            synced_at=now,
        )
        for j in jobs
        if j.get("JobId") is not None
    ]
    # Full replace inside one transaction: simplest correct sync for ~3.5k rows.
    db.execute(delete(JobDirectory))
    db.add_all(rows)
    db.commit()
    return len(rows)


def _sync_loop() -> None:
    settings = get_settings()
    while True:
        db = SessionLocal()
        try:
            count = refresh_jobs_directory(db)
            logger.info("jobs directory synced: %d jobs", count)
        except Exception:  # noqa: BLE001 — a failed sync must not kill the loop
            logger.exception("jobs directory sync failed; will retry")
        finally:
            db.close()
        time.sleep(max(60, settings.jobs_sync_minutes * 60))


def start_jobs_sync() -> None:
    """Launch the background sync thread (no-op without a bridge key)."""
    if not get_settings().bridge_console_key:
        logger.info("jobs directory sync disabled (no BRIDGE_CONSOLE_KEY)")
        return
    thread = threading.Thread(target=_sync_loop, name="jobs-sync", daemon=True)
    thread.start()
