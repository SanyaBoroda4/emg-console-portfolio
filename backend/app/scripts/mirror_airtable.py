"""Mirror the Airtable "Customer Payments Log" table into Postgres.

Read-only toward Airtable. Idempotent toward Postgres (upsert by
airtable_id). Never deletes — rows that disappear from Airtable stay here.
The whole run is one transaction: any crash means zero partial writes.

Run:
    docker compose exec backend python -m app.scripts.mirror_airtable [--dry-run]

Field mapping: STAGE1_ADDENDUM_FIELD_MAPPING.md. Everything received, mapped
or not, is stored in review_items.raw in full.
"""

import argparse
import logging
import sys
from collections import Counter
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pyairtable import Api
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PaymentDetails, ReviewItem

logger = logging.getLogger("app.scripts.mirror_airtable")

# Airtable fields mapped to columns (addendum's verified list).
MAPPED_FIELDS = {
    "Status",
    "DriveURL",
    "JobId",
    "JobName",
    "MorawareURL",
    "MatchMethod",
    "Amount",
    "PaymentMethod",
    "PaymentType",
    "PayerName",
    "InvoiceNumber",
    "PaymentDate",
    "CheckNumber",
    "CaptionName",
    "DateReceived",
}
# Known fields deliberately kept in raw only for Stage 1.
RAW_ONLY_FIELDS = {
    "Source",
    "MessageId",
    "GroupJID",
    "ConfidenceFlags",
    "LastQBScanTime",
    "QBPaymentId",
}
KNOWN_FIELDS = MAPPED_FIELDS | RAW_ONLY_FIELDS

# Live data shows PaymentDate as e.g. "5/17/2026 8:00pm" (US date + time).
_DATE_FORMATS = ("%m/%d/%Y %I:%M%p", "%m/%d/%y %I:%M%p", "%m/%d/%Y", "%m/%d/%y")


def _text(value: object) -> str | None:
    """Cast any Airtable value to text; None/empty stays NULL.

    Airtable numbers (e.g. JobId, CheckNumber) become strings; an integral
    float like 8321.0 becomes "8321", not "8321.0".
    """
    if value is None or value == "":
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_amount(value: object) -> Decimal | None:
    """Decimal via str() round-trip, never float(); quantized to cents."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        logger.warning("Unparseable Amount %r — storing NULL (raw keeps it)", value)
        return None


def parse_date(value: object) -> date | None:
    """Tolerant date parsing: ISO date/datetime, then US formats; else NULL."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    logger.warning("Unparseable date %r — storing NULL (raw keeps it)", value)
    return None


def parse_datetime(value: object) -> datetime | None:
    """ISO datetime; naive values are treated as UTC (Airtable's timezone)."""
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparseable datetime %r — storing NULL (raw keeps it)", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def transform(record: dict) -> tuple[dict, dict]:
    """Pure function: one Airtable record → (review_items values,
    payment_details values). No network, no database — unit-testable."""
    fields = record.get("fields", {})
    review_values = {
        "airtable_id": record["id"],
        "item_type": "payment",
        "source": "airtable_mirror",
        # Verbatim from Airtable; never crash on a new value. NOT NULL column,
        # so a record without a Status lands as "unknown" (raw keeps the truth).
        "status": _text(fields.get("Status")) or "unknown",
        "photo_drive_url": _text(fields.get("DriveURL")),
        "matched_job_id": _text(fields.get("JobId")),
        "matched_job_name": _text(fields.get("JobName")),
        "moraware_url": _text(fields.get("MorawareURL")),
        "match_method": _text(fields.get("MatchMethod")),
        "raw": fields,
    }
    payment_values = {
        "amount": parse_amount(fields.get("Amount")),
        "payment_method": _text(fields.get("PaymentMethod")),
        "payment_type": _text(fields.get("PaymentType")),
        "payer_name": _text(fields.get("PayerName")),
        "invoice_number": _text(fields.get("InvoiceNumber")),
        "txn_date": parse_date(fields.get("PaymentDate")),
        "check_number": _text(fields.get("CheckNumber")),
        "caption_name": _text(fields.get("CaptionName")),
        "date_received": parse_datetime(fields.get("DateReceived")),
    }
    return review_values, payment_values


def upsert_record(session: Session, record: dict) -> str:
    """Upsert one record by airtable_id. Returns 'inserted' | 'updated' |
    'unchanged'. Pure toward the network — record is passed in."""
    review_values, payment_values = transform(record)
    existing = session.scalar(
        select(ReviewItem).where(ReviewItem.airtable_id == review_values["airtable_id"])
    )

    if existing is None:
        session.add(
            ReviewItem(**review_values, payment_details=PaymentDetails(**payment_values))
        )
        logger.debug("inserted %s", review_values["airtable_id"])
        return "inserted"

    if existing.raw == review_values["raw"]:
        logger.debug("unchanged %s", review_values["airtable_id"])
        return "unchanged"

    for key, value in review_values.items():
        setattr(existing, key, value)
    details = existing.payment_details
    if details is None:
        details = PaymentDetails()
        existing.payment_details = details
    for key, value in payment_values.items():
        setattr(details, key, value)
    existing.updated_at = datetime.now(timezone.utc)
    logger.debug("updated %s", review_values["airtable_id"])
    return "updated"


def mirror_records(session: Session, records: list[dict]) -> dict:
    """Upsert every record into the given session (no commit — the caller
    owns the transaction). Returns the run summary."""
    # REQUIRED first action: log the field names actually received.
    field_names = sorted({name for r in records for name in r.get("fields", {})})
    logger.info("Airtable field names received: %s", field_names)
    unmapped = sorted(set(field_names) - KNOWN_FIELDS)
    if unmapped:
        logger.warning("Unmapped Airtable fields (stored in raw only): %s", unmapped)

    counts: Counter[str] = Counter()
    for record in records:
        counts[upsert_record(session, record)] += 1

    statuses = Counter(
        _text(r.get("fields", {}).get("Status")) or "unknown" for r in records
    )

    return {
        "fetched": len(records),
        "inserted": counts["inserted"],
        "updated": counts["updated"],
        "unchanged": counts["unchanged"],
        "unmapped_fields": unmapped,
        "statuses": dict(statuses.most_common()),
    }


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Mirror the Airtable payments table into Postgres."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the summary without committing anything",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.airtable_token:
        sys.exit(
            "AIRTABLE_TOKEN is not set. Add a read-only Personal Access Token "
            "(scope: data.records:read) to .env, then restart the backend "
            "container so it picks up the change."
        )

    table = Api(settings.airtable_token).table(
        settings.airtable_base_id, settings.airtable_payments_table
    )
    records = table.all()  # pyairtable handles pagination and rate limits

    # Import here, not at module top, so tests can use mirror_records/transform
    # without a DATABASE_URL configured.
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        summary = mirror_records(session, records)
        if args.dry_run:
            session.rollback()
            logger.info("DRY RUN — rolled back, nothing committed")
        else:
            session.commit()
        logger.info(
            "fetched=%d inserted=%d updated=%d unchanged=%d unmapped_fields=%s statuses=%s",
            summary["fetched"],
            summary["inserted"],
            summary["updated"],
            summary["unchanged"],
            summary["unmapped_fields"],
            summary["statuses"],
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
