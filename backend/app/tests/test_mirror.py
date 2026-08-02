"""Mirror logic tests — pure transform/upsert functions, no Airtable calls."""

import copy
from decimal import Decimal

from sqlalchemy import func, select

from app.models import PaymentDetails, ReviewItem
from app.scripts.mirror_airtable import mirror_records, parse_amount

FAKE_RECORD = {
    "id": "rec00000000000001",
    "fields": {
        "Status": "pending",
        "Amount": 4850.5,
        "PaymentMethod": "check",
        "PaymentType": "deposit",
        "PayerName": "Jane Homeowner",
        "InvoiceNumber": "INV-1042",
        "PaymentDate": "2026-07-01",
        "CheckNumber": 51,
        "CaptionName": "jane h",
        "DateReceived": "2026-07-01T14:23:00.000Z",
        "JobId": 8321,
        "JobName": "Smith Kitchen",
        "MorawareURL": "https://example.moraware.net/sys/job/8321",
        "MatchMethod": "invoice-recheck:1042",
        "DriveURL": "https://drive.google.com/file/d/abc",
        "GroupJID": "120363@g.us",
    },
}


def _record(**field_overrides):
    record = copy.deepcopy(FAKE_RECORD)
    record["fields"].update(field_overrides)
    return record


def test_insert_creates_both_rows_with_correct_mapping(db_session):
    summary = mirror_records(db_session, [_record()])
    db_session.commit()

    assert summary["inserted"] == 1
    assert summary["statuses"] == {"pending": 1}

    item = db_session.scalars(select(ReviewItem)).one()
    assert item.item_type == "payment"
    assert item.source == "airtable_mirror"
    assert item.status == "pending"
    assert item.airtable_id == "rec00000000000001"
    assert item.matched_job_id == "8321"  # Airtable number cast to text
    assert item.matched_job_name == "Smith Kitchen"
    assert item.moraware_url == "https://example.moraware.net/sys/job/8321"
    assert item.match_method == "invoice-recheck:1042"
    assert item.photo_drive_url == "https://drive.google.com/file/d/abc"
    assert item.raw["GroupJID"] == "120363@g.us"  # raw keeps everything

    details = item.payment_details
    assert details is not None
    assert details.amount == Decimal("4850.50")
    assert details.payment_method == "check"
    assert details.payment_type == "deposit"
    assert details.payer_name == "Jane Homeowner"
    assert details.invoice_number == "INV-1042"
    assert details.txn_date.isoformat() == "2026-07-01"
    assert details.check_number == "51"  # number cast to text
    assert details.caption_name == "jane h"
    assert details.date_received is not None


def test_same_input_twice_yields_one_row(db_session):
    mirror_records(db_session, [_record()])
    db_session.commit()

    summary = mirror_records(db_session, [_record()])
    db_session.commit()

    assert summary["inserted"] == 0
    assert summary["updated"] == 0
    assert summary["unchanged"] == 1
    assert db_session.scalar(select(func.count()).select_from(ReviewItem)) == 1
    assert db_session.scalar(select(func.count()).select_from(PaymentDetails)) == 1


def test_changed_field_updates_row_and_updated_at(db_session):
    mirror_records(db_session, [_record()])
    db_session.commit()
    original_updated_at = db_session.scalars(select(ReviewItem)).one().updated_at

    summary = mirror_records(db_session, [_record(Status="confirmed", Amount=5000)])
    db_session.commit()

    assert summary["updated"] == 1
    item = db_session.scalars(select(ReviewItem)).one()
    assert item.status == "confirmed"
    assert item.payment_details.amount == Decimal("5000.00")
    assert item.updated_at != original_updated_at


def test_unknown_status_is_stored_not_rejected(db_session):
    summary = mirror_records(db_session, [_record(Status="wildly_new_status")])
    db_session.commit()

    assert summary["inserted"] == 1
    item = db_session.scalars(select(ReviewItem)).one()
    assert item.status == "wildly_new_status"


def test_amount_parses_via_str_roundtrip_never_float():
    assert parse_amount(4850.5) == Decimal("4850.50")
    assert str(parse_amount(4850.5)) == "4850.50"
    assert parse_amount(None) is None
    assert parse_amount("not-money") is None


def test_record_with_no_payment_fields_still_gets_details_row(db_session):
    mirror_records(db_session, [{"id": "recEMPTY000000001", "fields": {"Status": "pending"}}])
    db_session.commit()

    item = db_session.scalars(select(ReviewItem)).one()
    details = item.payment_details
    assert details is not None  # row of NULLs, per plan §7
    assert details.amount is None
    assert details.payer_name is None
    assert details.txn_date is None
