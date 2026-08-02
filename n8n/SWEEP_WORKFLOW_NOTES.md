# PILOT CONSOLE PAYMENT-SWEEP — workflow notes

Companion to `n8n/PILOT_CONSOLE_PAYMENT-SWEEP.json` (63 nodes: 26 transplanted
byte-faithfully from EMG PAYMENT-SWEEP BOT v21, 37 new). Regenerate with
`python n8n/build_sweep_workflow.py` (needs the gitignored export in `n8n/`).

The sweep is the night watchman: every hour it pulls the last 3 days of
QuickBooks payments, diffs them against the console register, and files what
it can prove. **Owner rule (2026-07-20): a payment whose invoice number
matches a Moraware job records FULLY AUTOMATICALLY — no questions, one push,
card born resolved.**

## 1. Disposition

| Original piece | Disposition | Console version |
|---|---|---|
| Hourly schedule | PORT | interval = n8n schedule; QB window = `lookback_days` (3) in Sweep Config |
| QB reads (PaymentMethods, Payments, Invoices, Customer) | PORT | `qb_company_url` = **sandbox realm 9341457257917923** baked in — prod QB unreachable |
| `Normalize Payments` (method classify, split detection), `Build Work Items` (skip/backfill/supersede/record brain), `Build Tier1/2/3` (75%-contract math), `NF` fallback, `Parse QB Address`, `Pick Best Match` | **PORT VERBATIM** | `Rows As Register` adapter dresses console rows in Airtable's `{id, fields}` shape so the brain runs unmodified |
| `Fetch Airtable Rows` | REPLACE | `GET /api/hooks/pilot/list?days=45` |
| `Dedup Search` (Airtable formula) | REPLACE | live `GET /find?qb_payment_id` + the same predicate in JS over the register snapshot (`Dedup Decide`) |
| `Create Row` / `Adopt` / `Backfill` / `Supersede` | REPLACE | `POST /items` (now takes check_number) + `POST /update` (qb_payment_id, job fields, `superseded_split` status) |
| Tier 1 auto-record + WhatsApp "no action needed" | PORT, push-only | item created `confirmed` → job fields → Moraware note (LIVE, TEST jobs) → `/final` fires the push |
| Tier 2 "reply YES to confirm" + dispatch queue | REPLACE | question card (1 candidate + freeform) resuming to **this workflow's second webhook** `/webhook/sweep-decision?item=<id>`; the dispatch queue is DROPPED — the board is the queue |
| Tier 3 "please add invoice to a job" | REPLACE, improved | item `needs_job` + open freeform question — type the job name on the card; multi-round supported |
| Split-check digest + run chatter | REPLACE | `POST /notify` pool pushes: one run summary ("3 recorded · 1 needs a job") + one digest per split check |
| WhatsApp / Evolution | DROP | — |

## 2. Manual test matrix (sandbox)

| # | Scenario (set up in sandbox QB) | Expected |
|---|---|---|
| 1 | Payment on an invoice whose number IS on a Moraware TEST job | Card appears already `confirmed` with job + Moraware link + type (deposit/remainder/PIF via 75% math); push "✅ New payment recorded … No action needed"; Moraware note on the TEST job |
| 2 | Payment on an invoice NOT in Moraware, customer address/name matches a job | Card `needs_job` with amber question: 1 candidate ("matched by address — double-check") + freeform; confirming → job fields, note, final push |
| 3 | Payment matching nothing | Card `needs_job`, freeform question "type the job name"; typing a name/invoice resolves it (or asks again) |
| 4 | Run the sweep twice | Second run records nothing (idempotent — QB id already stamped) |
| 5 | Check submitted via console check-bot, then sweep runs | Existing card gets "verified in QuickBooks — QB id stamped" (backfill), no duplicate row |
| 6 | One QB payment covering 2 invoices | Two cards (one per invoice, amounts split), full-amount check-bot row (if any) superseded, digest push "One check #N covered 2 invoices" |
| 7 | Quiet hour (no new payments) | No pushes at all |

## 3. Import checklist

1. Import `PILOT_CONSOLE_PAYMENT-SWEEP.json`; fill **Sweep Config**:
   `console_base_url`, `pilot_hook_secret` (same value as always). QB realm,
   bridge, webhook URL are prefilled for your instance.
2. Attach your existing **sandbox QuickBooks credential** to the 4 QB nodes.
3. Activate. Two triggers go live: the hourly schedule AND the
   `sweep-decision` webhook (needed for answering tier-2/3 questions).
4. First run: use "Execute workflow" manually instead of waiting an hour.

## 4. Open TODOs / limits

- Sweep-created cards have **no photo** (they're QB-born) — the card shows
  facts + feed only; expected.
- Tier-2/3 answers arrive via the `sweep-decision` webhook; if the workflow
  is INACTIVE, answering a sweep question 502s (console rolls back cleanly —
  re-answer after reactivating).
- ACH/credit-card payments ride the same tiers (method comes from QB's
  payment-method table, ported verbatim).
- Split-group metadata is not persisted in the console (digest is built
  in-run); a split part deleted and re-swept next run re-creates it.
- The unused WhatsApp group-JID constant survives inside transplanted tier
  builders as dead output fields — harmless, kept for byte-fidelity.

## 5. PLAIN WORDS

Every hour the bot asks QuickBooks "what money came in the last three days?"
For each payment: if it can PROVE which job it belongs to (the invoice number
is on a Moraware job), it records everything itself and just tells you — no
buttons to press. If it's only fairly sure (matched the customer's address or
name), it puts a question card on the board with its best guess. If it has no
idea, the card asks you to type the job name. It never records the same money
twice, it recognizes checks your check-bot already photographed and stamps
QuickBooks' id on them, and when one check pays several invoices it splits it
properly and retires the old single row so totals stay honest.
