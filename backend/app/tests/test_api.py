"""API tests — TestClient against the real app with a SQLite session."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models import PaymentDetails, ReviewItem


def _seed(
    db_session,
    status: str,
    amount: str,
    txn_date: date | None = None,
    date_received: datetime | None = None,
) -> ReviewItem:
    item = ReviewItem(
        item_type="payment",
        status=status,
        source="airtable_mirror",
        airtable_id=f"rec{uuid.uuid4().hex[:14]}",
        raw={"Status": status},
        payment_details=PaymentDetails(
            amount=Decimal(amount), txn_date=txn_date, date_received=date_received
        ),
    )
    db_session.add(item)
    db_session.commit()
    return item


def test_health_returns_200_ok(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "ok"}


def test_review_items_empty_db_shape(client):
    res = client.get("/api/review-items")
    assert res.status_code == 200
    assert res.json() == {"items": [], "total": 0}


def test_review_items_filter_by_status(client, db_session):
    _seed(db_session, status="pending", amount="100.00")
    _seed(db_session, status="confirmed", amount="250.00")

    res = client.get("/api/review-items", params={"item_type": "payment", "status": "pending"})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "pending"
    # Decimal rides the wire as an exact string.
    assert body["items"][0]["payment_details"]["amount"] == "100.00"


def test_stats_counts_by_status(client, db_session):
    _seed(db_session, status="pending", amount="100.00")
    _seed(db_session, status="pending", amount="50.00")
    _seed(db_session, status="confirmed", amount="250.00")

    res = client.get("/api/review-items/stats", params={"item_type": "payment"})
    assert res.status_code == 200
    assert res.json() == {"by_status": {"pending": 2, "confirmed": 1}, "total": 3}


def test_list_orders_by_payment_date_newest_first_with_fallback(client, db_session):
    march = _seed(db_session, status="confirmed", amount="1.00", txn_date=date(2026, 3, 1))
    january = _seed(db_session, status="confirmed", amount="2.00", txn_date=date(2026, 1, 15))
    # No txn_date — must sort by its date_received fallback (February).
    february = _seed(
        db_session,
        status="confirmed",
        amount="3.00",
        date_received=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
    )

    res = client.get("/api/review-items")
    assert res.status_code == 200
    ids = [item["airtable_id"] for item in res.json()["items"]]
    assert ids == [march.airtable_id, february.airtable_id, january.airtable_id]


def test_limit_up_to_1000_is_accepted(client, db_session):
    _seed(db_session, status="confirmed", amount="1.00")
    res = client.get("/api/review-items", params={"limit": 1000})
    assert res.status_code == 200
    assert res.json()["total"] == 1


def test_limit_over_1000_is_rejected(client):
    res = client.get("/api/review-items", params={"limit": 1001})
    assert res.status_code == 422


def test_negative_offset_is_rejected(client):
    res = client.get("/api/review-items", params={"offset": -1})
    assert res.status_code == 422
