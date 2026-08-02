"""Typeahead job search + directory sync (slab deliveries stage)."""

from datetime import date, datetime, timezone

from app.jobs_sync import refresh_jobs_directory
from app.models import JobDirectory
from app.tests.conftest import login_as


def seed(db, rows):
    now = datetime.now(timezone.utc)
    for job_id, name, created in rows:
        db.add(JobDirectory(job_id=job_id, customer_name=name,
                            lead_url=f"https://moraware.test/sys/job/{job_id}",
                            creation_date=created, synced_at=now))
    db.commit()


def test_search_newest_first(client, db_session):
    seed(db_session, [
        (1, "Oliver Kahn", date(2026, 7, 1)),
        (2, "Prakahn Industries", date(2026, 7, 10)),
        (3, "Kahn Brothers", date(2025, 1, 1)),
        (4, "Unrelated", date(2026, 1, 1)),
    ])
    res = client.get("/api/jobs/search", params={"q": "kah"})
    assert res.status_code == 200
    names = [j["customer_name"] for j in res.json()["jobs"]]
    # Most recently created jobs first (owner 2026-07-24) — recency beats
    # prefix-vs-substring quality.
    assert names == ["Prakahn Industries", "Oliver Kahn", "Kahn Brothers"]


def test_search_hides_done_and_cancelled(client, db_session):
    from datetime import datetime, timezone
    from app.models import JobDirectory
    now = datetime.now(timezone.utc)
    for job_id, name, status in [
        (11, "Smith Active", None),
        (12, "Smith Done", "Done"),
        (13, "Smith Cancelled", "cancelled"),
        (14, "Smith Open", "In Progress"),
    ]:
        db_session.add(JobDirectory(job_id=job_id, customer_name=name,
                                    creation_date=date(2026, 7, 1),
                                    status=status, synced_at=now))
    db_session.commit()
    names = [j["customer_name"] for j in
             client.get("/api/jobs/search", params={"q": "smith"}).json()["jobs"]]
    assert set(names) == {"Smith Active", "Smith Open"}


def test_search_multiword_and_min_length(client, db_session):
    seed(db_session, [(1, "Oliver Kahn", date(2026, 7, 1)),
                      (2, "Oliver Twist", date(2026, 6, 1))])
    res = client.get("/api/jobs/search", params={"q": "oliver k"})
    assert [j["customer_name"] for j in res.json()["jobs"]] == ["Oliver Kahn"]
    assert client.get("/api/jobs/search", params={"q": "o"}).status_code == 422


def test_search_allows_yard_but_not_anonymous(client, db_session):
    # yard (scanner staff) need the job typeahead for slab scans.
    login_as(client, db_session, "wade@test.local", "yard")
    assert client.get("/api/jobs/search", params={"q": "oliver"}).status_code == 200
    # No session at all is still rejected.
    client.cookies.clear()
    assert client.get("/api/jobs/search", params={"q": "oliver"}).status_code == 401


def test_refresh_replaces_directory(client, db_session, monkeypatch):
    seed(db_session, [(999, "Stale Row", None)])

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"Jobs": [
                {"JobId": 5829, "CustomerName": "Oliver Kahn",
                 "LeadUrl": "https://x/5829", "CreationDate": "2026-07-18T00:00:00"},
                {"JobId": 5830, "CustomerName": "  ",
                 "LeadUrl": None, "CreationDate": None},
            ]}

    monkeypatch.setattr("app.jobs_sync.httpx.get",
                        lambda url, headers, timeout: FakeResponse())
    count = refresh_jobs_directory(db_session)
    assert count == 2
    rows = {j.job_id: j for j in db_session.query(JobDirectory).all()}
    assert 999 not in rows
    assert rows[5829].creation_date == date(2026, 7, 18)
    assert rows[5830].customer_name == "Job 5830"  # blank name fallback
