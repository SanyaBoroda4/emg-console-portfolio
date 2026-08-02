# EMG Ops Console — Stage 3 build plan (editing + audit log)

> Audience: Claude Code. Read PROJECT_STATE.md first (source of truth for what
> exists), then this plan (the delta). All prior constraints hold unless amended
> here. THIS PLAN AMENDS ONE STANDING CONSTRAINT: the console may now WRITE to
> Airtable, but only via the single PATCH path defined below, only the fields
> whitelisted below, only on user-initiated edits. Still zero writes to
> Moraware, QuickBooks, WhatsApp. No new dependencies (pyairtable already
> supports updates).

## 0. Decision record (owner + architect, 2026-07-11)

Mirrored rows: edits write through to Airtable first, then update Postgres.
Rationale: n8n bots still read Airtable during the transition; a local-only
edit would be invisible to reconciliation and silently reverted by the mirror.
This write-through is transition scaffolding — deleted at cutover together
with the mirror. Console-born rows (airtable_id NULL) edit locally only.

## 1. Roles for editing (server-enforced whitelists)

- manager (natalia@, victor@): may edit exactly one field: `amount`.
- admin (alex@, bills@): may edit: `amount, payer_name, payment_type,
  payment_method, invoice_number, check_number, txn_date, caption_name`.
- yard: no edit ability anywhere (payments endpoints already 403 for yard).
- NOT editable by anyone this stage: status, matched_job_id, matched_job_name,
  moraware_url, match_method, photo fields, item_type, source. These belong to
  the bots' matching machinery / later stages.

UI affordance vs permission: cards mode surfaces ONLY the amount edit (the
quick OCR-fix path, available to manager+admin). Table mode surfaces the full
whitelist per role. The SERVER enforces the whitelist regardless of which UI
sent the request — a manager PATCHing payer_name gets 403 even with a
hand-crafted request.

## 2. New environment variable

`AIRTABLE_WRITE_TOKEN` — a SECOND Airtable PAT, scope `data.records:write`
(+read), restricted to the "EMG logs" base. The mirror keeps using the
read-only `AIRTABLE_TOKEN`; only the edit path uses the write token (least
privilege preserved: a leak of the mirror token still cannot modify Airtable).
Add to .env.example with documentation; config.py fail-fast if missing.

## 3. Database (migration 0004)

### New table `audit_log` (history must survive row deletion → NO foreign key)
| column | type | notes |
|---|---|---|
| id | uuid PK | gen_random_uuid, same dual-dialect pattern as elsewhere |
| review_item_id | uuid NOT NULL, indexed | plain uuid, deliberately not an FK |
| item_label | text NOT NULL | human snapshot at action time, e.g. "payment $4,850.00 — R. Simmons" — readable even after the row is gone |
| actor_email | text NOT NULL | from the session |
| action | text NOT NULL | 'edit' or 'delete' (CHECK constraint) |
| field | text NULL | which field, for edits; NULL for delete |
| old_value | text NULL | stringified previous value ("" vs NULL preserved) |
| new_value | text NULL | stringified new value; NULL for delete |
| created_at | timestamptz NOT NULL default now() | |

### review_items: add `last_edited_at` timestamptz NULL, `last_edited_by` text
NULL — cheap display fields so the UI can mark edited rows without joining
audit_log on every list call.

## 4. Backend

### PATCH /api/review-items/{id}
Auth: require_role("admin","manager"). Body: `{changes: {field: value, ...},
expected: {field: old_value_as_client_saw_it, ...}}`.

Flow, in order:
1. Load row (404 if missing). Reject any field outside the caller's role
   whitelist → 403 listing the offending field(s).
2. Validation per field: amount → Decimal via str(), > 0, ≤ 500000, quantized
   to cents (422 otherwise, with a plain message); txn_date → parse with the
   mirror's tolerant parser (422 if unparseable); text fields → trim, length
   ≤ 200. Empty string means "clear the field" (stored as NULL).
3. Concurrency guardrail: for each field in `changes`, compare the row's
   current value against `expected[field]` (string-normalized). Mismatch →
   409 `{"error":"stale", "field":..., "current":...}` — the UI re-shows the
   fresh value. This prevents two people silently overwriting each other.
4. If the row is MIRRORED (airtable_id present): PATCH Airtable FIRST via
   pyairtable with the write token, mapping our columns back to Airtable
   names (amount→Amount, payer_name→PayerName, payment_type→PaymentType,
   payment_method→PaymentMethod, invoice_number→InvoiceNumber,
   check_number→CheckNumber, caption_name→CaptionName, txn_date→PaymentDate).
   PaymentDate FORMAT: write in Airtable's live observed format
   `M/D/YYYY h:mmam/pm` with 12:00pm as the time component (the live data uses
   this shape; see PROJECT_STATE §11) — flag in PLAIN WORDS that the first
   real edit of a date must be verified against Airtable by the owner.
   Airtable failure → 502 `{"error":"airtable_write_failed"}`, NO local
   change, NO audit row. (Console rows skip this step entirely.)
5. In ONE local transaction: apply changes to payment_details/review_items,
   update raw[<airtable field>] to the new value for mirrored rows (so the
   next mirror run sees no diff and stays idempotent), set last_edited_at/by,
   and INSERT one audit_log row PER CHANGED FIELD (action='edit').
6. Return the full serialized item (fresh values + last_edited_*).

### DELETE /api/review-items/{id} — extend, don't change semantics
Before deleting, write an audit_log row: action='delete', item_label snapshot,
actor from session. (Deletion history finally survives the deletion.)

### GET /api/audit
Auth: require_role("admin") ONLY — managers get 403.
Query: `review_item_id` (optional), `limit` ≤ 200 default 50, `offset`.
Returns `{entries:[...], total}`, newest first. Each entry: everything in the
table plus nothing derived — keep it raw and honest.

## 5. Frontend

### Cards mode — the quick amount fix
A small ✎ next to the amount (manager+admin sessions only). Tap → compact
editor (inline or small modal): current amount shown struck-through, input for
the new one, then a CONFIRM step: "Change amount $4,350.00 → $4,850.00?" with
an explicit note on mirrored rows: "This also updates Airtable." Enter=confirm,
Esc=cancel. On 409: "Someone just changed this to $X — review and retry." On
502: "Couldn't reach Airtable — nothing was changed. Try again."

### Table mode — per-role editable cells
Admin: cells for all whitelisted fields become click-to-edit (input in place,
Enter commits, Esc cancels, blur cancels — no accidental commits). Manager:
only the Amount cell is editable; other cells render plain. Amount commits go
through the same old→new confirm; other fields commit directly (still audited,
still 409-guarded). Editable cells get a subtle affordance on hover (pencil or
underline) so discoverability doesn't depend on clicking around.

### The "edited" marker
Any row/card with last_edited_at shows a small neutral "edited" chip; hover/
tap shows "by {last_edited_by}, {relative time}". This keeps corrected values
visually distinct from pristine OCR output — the guardrail's second half.

### Audit view (admin-only)
An "Audit" button in the Payments toolbar, rendered ONLY for admin sessions
(and the route/endpoint 403s managers regardless). Opens `/payments/audit`:
a plain reverse-chronological table — When · Who · Action · Item · Field ·
Old → New — with "Load more" pagination. Clicking an "edited" chip on any
card/row deep-links here filtered to that item (?review_item_id=). Empty
state: "No changes recorded yet." This page is deliberately boring — it's a
ledger, not a dashboard.

## 6. Out of scope (explicit)

Status changes, job re-matching, Moraware/WhatsApp writes, bulk edit, undo
buttons (the audit log IS the undo reference — an admin can read the old
value and apply it as a new edit), editing on slab/supply/leads (pages don't
exist), audit retention/expiry policies, editing of console photos.

## 7. Tests (extend the suite; all existing must stay green)

- Role whitelist: manager PATCH amount → 200; manager PATCH payer_name → 403;
  admin PATCH payer_name → 200; yard PATCH anything → 403.
- Validation: amount 0 / negative / 600000 / "abc" → 422; amount "4850.5" →
  stored Decimal("4850.50").
- Concurrency: PATCH with stale `expected.amount` → 409 + current value in
  body; fresh expected → 200.
- Write-through: mirrored row PATCH calls Airtable update with correctly
  mapped field names (mock pyairtable), updates raw so a subsequent mirror
  transform reports unchanged; Airtable exception → 502, local row untouched,
  no audit row.
- Console row PATCH: no Airtable call at all, local update + audit row.
- Audit: one row per changed field with correct old/new; delete writes an
  audit row that survives the item's deletion; GET /api/audit as admin → 200,
  as manager → 403; review_item_id filter works.
- last_edited_at/by set on edit, absent before.

## 8. Verification checklist (owner)

1. Create the write token (data.records:write, EMG logs base only), add
   AIRTABLE_WRITE_TOKEN to .env, restart backend — starts clean.
2. As Vince (manager), cards mode: edit an amount on a MIRRORED test row →
   confirm dialog → success. Open Airtable "Customer Payments Log" → the
   Amount cell shows the new value. Run the mirror → exit line reports that
   row as UNCHANGED (proves raw-sync worked; no clobber).
3. Same row: edit the amount BACK to the original (leaves Airtable clean).
4. As Vince in table mode: Amount cell editable, Payer cell is not; devtools
   fetch PATCH payer_name → 403 (server-side proof, same discipline as
   Stage 2's check 5).
5. As Alex in table mode: edit payer_name inline → row shows "edited" chip
   with your name; Audit button visible; audit page lists both edits with
   old → new; the chip deep-links filtered.
6. As Vince: no Audit button anywhere; direct GET /api/audit → 403.
7. Two-tab test: open the same payment in two tabs, edit amount in tab 1,
   then try in tab 2 with the stale value → 409 with the friendly message.
8. Stop the internet (or temporarily break AIRTABLE_WRITE_TOKEN): amount edit
   on a mirrored row → the "couldn't reach Airtable, nothing changed" error;
   verify neither Postgres nor the UI changed. Restore token.
9. Delete a console-born test row → audit page still shows its history,
   including the delete entry.
10. pytest: all green, count increased.
