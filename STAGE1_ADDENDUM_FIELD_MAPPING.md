# Stage 1 addendum — verified Airtable field mapping

> Read together with STAGE1_BUILD_PLAN.md. This supersedes section 6's "Known Airtable
> field names" list. Source: owner's screenshots of the live base ("EMG logs",
> appXXXXXXXXXXXXXX) on 2026-07-08.

## Table identity

The payments table is named **"Customer Payments Log"** (formerly "Pending Checks").
The table ID is unchanged: `tblXXXXXXXXXXXXXX`. Continue binding by ID.
Rename the env var to `AIRTABLE_PAYMENTS_TABLE` (same value) for clarity.

## Verified field list → mapping

| Airtable field   | maps to                             | notes |
|------------------|--------------------------------------|-------|
| Status           | review_items.status                  | Values not yet enumerated — see "Status discovery" below. |
| DriveURL         | review_items.photo_drive_url         | |
| JobId            | review_items.matched_job_id          | Airtable type is number; cast to text. |
| JobName          | review_items.matched_job_name        | |
| MorawareURL      | review_items.moraware_url            | |
| MatchMethod      | review_items.match_method            | |
| Source           | raw only (Stage 1)                   | Bot-recorded origin; our own `source` column stays `airtable_mirror` for provenance of the *mirror row*. Do not confuse the two. |
| Amount           | payment_details.amount               | Decimal via str(), never float. |
| PaymentMethod    | payment_details.payment_method       | |
| PaymentType      | payment_details.payment_type         | Observed values: PIF, deposit, remainder, progress. Store verbatim. |
| PayerName        | payment_details.payer_name           | |
| InvoiceNumber    | payment_details.invoice_number       | |
| PaymentDate      | payment_details.txn_date             | |
| CheckNumber      | payment_details.check_number  (NEW column, text NULL) | Useful on the board card. |
| CaptionName      | payment_details.caption_name  (NEW column, text NULL) | Name typed by staff in the WhatsApp caption; often the best human hint when OCR and QB disagree. |
| DateReceived     | payment_details.date_received (NEW column, timestamptz NULL) | When the photo hit WhatsApp — business time, distinct from txn_date and created_at. |
| MessageId        | raw only                             | WhatsApp plumbing; irrelevant to the console. |
| GroupJID         | raw only                             | Same. |
| ConfidenceFlags  | raw only (Stage 1)                   | Likely useful on the board later ("low-confidence match" badge) — revisit in Stage 3. |
| LastQBScanTime   | raw only                             | Bot bookkeeping. |
| QBPaymentId      | raw only (Stage 1)                   | Will matter at cutover for QB reconciliation; not needed to render the board. |

Everything above, mapped or not, still goes into `review_items.raw` in full.

## Status discovery (small mirror change)

The Status column's value set is unknown. Extend the mirror's exit summary to print the
distinct Status values with counts, e.g. `statuses={"confirmed": 41, "pending": 3, ...}`.
The owner will paste that line back for board/badge design. No other behavior change.

## Frontend card additions

Show `check_number` (as `Check #51`) when present, and prefer `payer_name`, falling back
to `caption_name` with a subtle "(from caption)" hint when only that exists.

## For later stages — confirmed to exist in the same base (do not touch in Stage 1)

- "Stalled Jobs" (JobName, Salesperson, AnchorType, AnchorDate, DateChased, NoteText,
  ActivityId, State, ResolvedHow, ResolutionNote, ManagerReminderSent,
  BossEscalationSent, LastChecked) → Follow-ups queue.
- "Slab Deliveries" (+ linked "Materials" table; validation flags SF/Qty/Price,
  Validation OK, Stage JSON, queue fields) → Slab deliveries board.
- "Supply Log" (Supplier, Doc type, Document #, Order date, Received, Total, Items,
  Photo, Photo filename) → Supply deliveries board.
