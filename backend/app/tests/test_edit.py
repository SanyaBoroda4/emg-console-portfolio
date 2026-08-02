"""Stage 3 tests: PATCH whitelists, validation, 409 guard, Airtable
write-through (mocked), raw sync, and the audit trail."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, PaymentDetails, ReviewItem
from app.scripts.mirror_airtable import upsert_record
from app.tests.conftest import TEST_ADMIN_EMAIL, login_as


class FakeAirtable:
    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    def update(self, record_id, fields, typecast=False):
        if self.fail:
            raise RuntimeError("airtable down")
        self.calls.append({"id": record_id, "fields": fields, "typecast": typecast})


@pytest.fixture()
def airtable(monkeypatch):
    fake = FakeAirtable()
    monkeypatch.setattr("app.routers.review_items._airtable_table", lambda: fake)
    return fake


def _seed(db_session, *, airtable_id=None, amount="4850.50", payer="Jane", raw=None):
    item = ReviewItem(
        item_type="payment",
        status="confirmed" if airtable_id else "submitted",
        source="airtable_mirror" if airtable_id else "console",
        airtable_id=airtable_id,
        raw=raw if raw is not None else {},
        payment_details=PaymentDetails(
            amount=Decimal(amount) if amount else None, payer_name=payer
        ),
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def _patch(client, item_id, changes, expected=None):
    return client.patch(
        f"/api/review-items/{item_id}",
        json={"changes": changes, "expected": expected or {}},
    )


# ---- role whitelists --------------------------------------------------------


def test_manager_can_edit_amount(client, db_session):
    item = _seed(db_session)
    login_as(client, db_session, "mgr@test.local", "manager")
    res = _patch(client, item.id, {"amount": "100"}, {"amount": "4850.50"})
    assert res.status_code == 200
    assert res.json()["payment_details"]["amount"] == "100.00"


def test_manager_cannot_edit_payer_name(client, db_session):
    item = _seed(db_session)
    login_as(client, db_session, "mgr@test.local", "manager")
    res = _patch(client, item.id, {"payer_name": "X"})
    assert res.status_code == 403
    assert "payer_name" in res.json()["message"]


def test_admin_can_edit_payer_name(client, db_session):
    item = _seed(db_session)
    res = _patch(client, item.id, {"payer_name": "New Name"}, {"payer_name": "Jane"})
    assert res.status_code == 200
    assert res.json()["payment_details"]["payer_name"] == "New Name"


def test_yard_patch_is_403(client, db_session):
    item = _seed(db_session)
    login_as(client, db_session, "yard@test.local", "yard")
    assert _patch(client, item.id, {"amount": "1"}).status_code == 403


def test_nobody_edits_status(client, db_session):
    item = _seed(db_session)  # admin client
    res = _patch(client, item.id, {"status": "confirmed"})
    assert res.status_code == 403


# ---- validation -------------------------------------------------------------


@pytest.mark.parametrize("bad", ["0", "-5", "600000", "abc"])
def test_bad_amounts_are_422(client, db_session, bad):
    item = _seed(db_session)
    assert _patch(client, item.id, {"amount": bad}).status_code == 422


def test_amount_str_roundtrip_to_cents(client, db_session):
    item = _seed(db_session)
    res = _patch(client, item.id, {"amount": "4850.5"}, {"amount": "4850.50"})
    assert res.status_code == 200
    assert res.json()["payment_details"]["amount"] == "4850.50"
    db_session.expire_all()
    assert db_session.get(ReviewItem, item.id).payment_details.amount == Decimal("4850.50")


def test_unparseable_date_is_422(client, db_session):
    item = _seed(db_session)
    assert _patch(client, item.id, {"txn_date": "not a date"}).status_code == 422


# ---- concurrency ------------------------------------------------------------


def test_stale_expected_is_409_with_current(client, db_session):
    item = _seed(db_session, amount="100.00")
    res = _patch(client, item.id, {"amount": "200"}, {"amount": "999.99"})
    assert res.status_code == 409
    body = res.json()
    assert body["error"] == "stale"
    assert body["field"] == "amount"
    assert body["current"] == "100.00"


def test_fresh_expected_is_200(client, db_session):
    item = _seed(db_session, amount="100.00")
    res = _patch(client, item.id, {"amount": "200"}, {"amount": "100.00"})
    assert res.status_code == 200


# ---- Airtable write-through -------------------------------------------------


def test_mirrored_patch_maps_fields_and_syncs_raw(client, db_session, airtable):
    raw = {"Status": "confirmed", "Amount": 4850.5, "PayerName": "Jane", "CheckNumber": "51"}
    item = _seed(db_session, airtable_id="recEDIT0000000001", raw=raw)

    res = _patch(
        client,
        item.id,
        {
            "amount": "5000",
            "payer_name": "New Name",
            "txn_date": "2026-07-04",
            "check_number": "",  # clear
        },
        {"amount": "4850.50", "payer_name": "Jane"},
    )
    assert res.status_code == 200

    # Airtable got exactly the mapped payload, typecast on.
    assert len(airtable.calls) == 1
    call = airtable.calls[0]
    assert call["id"] == "recEDIT0000000001"
    assert call["typecast"] is True
    assert call["fields"] == {
        "Amount": 5000.0,
        "PayerName": "New Name",
        "PaymentDate": "7/4/2026 12:00pm",
        "CheckNumber": None,
    }

    # raw synced: the next mirror run must see no difference.
    db_session.expire_all()
    fresh = db_session.get(ReviewItem, item.id)
    assert fresh.raw["Amount"] == 5000.0
    assert fresh.raw["PayerName"] == "New Name"
    assert fresh.raw["PaymentDate"] == "7/4/2026 12:00pm"
    assert "CheckNumber" not in fresh.raw
    record = {"id": fresh.airtable_id, "fields": dict(fresh.raw)}
    assert upsert_record(db_session, record) == "unchanged"


def test_airtable_failure_is_502_and_changes_nothing(client, db_session, monkeypatch):
    fake = FakeAirtable(fail=True)
    monkeypatch.setattr("app.routers.review_items._airtable_table", lambda: fake)
    item = _seed(db_session, airtable_id="recFAIL0000000001", amount="100.00")

    res = _patch(client, item.id, {"amount": "200"}, {"amount": "100.00"})
    assert res.status_code == 502
    assert res.json()["error"] == "airtable_write_failed"

    db_session.expire_all()
    fresh = db_session.get(ReviewItem, item.id)
    assert fresh.payment_details.amount == Decimal("100.00")  # untouched
    assert fresh.last_edited_at is None
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_console_row_patch_skips_airtable(client, db_session, airtable):
    item = _seed(db_session)  # airtable_id=None
    res = _patch(client, item.id, {"amount": "222"}, {"amount": "4850.50"})
    assert res.status_code == 200
    assert airtable.calls == []  # no Airtable involvement at all
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == 1


# ---- audit ------------------------------------------------------------------


def test_one_audit_row_per_changed_field(client, db_session):
    item = _seed(db_session, amount="100.00", payer="Old Payer")
    res = _patch(
        client,
        item.id,
        {"amount": "200", "payer_name": "New Payer"},
        {"amount": "100.00", "payer_name": "Old Payer"},
    )
    assert res.status_code == 200

    entries = db_session.scalars(select(AuditLog).order_by(AuditLog.field)).all()
    assert len(entries) == 2
    by_field = {e.field: e for e in entries}
    assert by_field["amount"].old_value == "100.00"
    assert by_field["amount"].new_value == "200.00"
    assert by_field["payer_name"].old_value == "Old Payer"
    assert by_field["payer_name"].new_value == "New Payer"
    assert all(e.action == "edit" for e in entries)
    assert all(e.actor_email == TEST_ADMIN_EMAIL for e in entries)


def test_delete_audit_survives_the_row(client, db_session):
    item = _seed(db_session)
    item_id = item.id
    assert client.delete(f"/api/review-items/{item_id}").status_code == 204
    assert db_session.get(ReviewItem, item_id) is None  # row gone

    entry = db_session.scalars(select(AuditLog)).one()
    assert entry.action == "delete"
    assert entry.review_item_id == item_id
    assert "payment" in entry.item_label


def test_audit_feed_admin_only_with_filter(client, db_session):
    a = _seed(db_session, payer="Payer A")
    b = _seed(db_session, payer="Payer B")
    _patch(client, a.id, {"payer_name": "A2"}, {"payer_name": "Payer A"})
    _patch(client, b.id, {"payer_name": "B2"}, {"payer_name": "Payer B"})

    res = client.get("/api/audit")
    assert res.status_code == 200
    assert res.json()["total"] == 2

    filtered = client.get(f"/api/audit?review_item_id={a.id}")
    assert filtered.json()["total"] == 1
    assert filtered.json()["entries"][0]["new_value"] == "A2"

    login_as(client, db_session, "mgr2@test.local", "manager")
    assert client.get("/api/audit").status_code == 403


def test_last_edited_set_on_edit_absent_before(client, db_session):
    item = _seed(db_session)
    before = client.get("/api/review-items").json()["items"][0]
    assert before["last_edited_at"] is None
    assert before["last_edited_by"] is None

    res = _patch(client, item.id, {"amount": "150"}, {"amount": "4850.50"})
    body = res.json()
    assert body["last_edited_at"] is not None
    assert body["last_edited_by"] == TEST_ADMIN_EMAIL


def test_patch_unknown_item_is_404(client):
    assert _patch(client, uuid.uuid4(), {"amount": "1"}).status_code == 404
