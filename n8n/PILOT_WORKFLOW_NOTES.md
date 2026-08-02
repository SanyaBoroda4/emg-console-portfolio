# PILOT CONSOLE CHECK-BOT — workflow notes

Companion to `n8n/PILOT_CONSOLE_CHECK-BOT.json` (109 nodes: 58 transplanted
byte-faithfully from EMG CHECK-BOT v21, 51 new). Regenerate any time with
`python n8n/build_pilot_workflow.py` (needs the production export in the repo
root — that file is gitignored and must never be committed).

The console is the human surface (replaces WhatsApp) and the register
(replaces Airtable). Every URL, header, field name, and payload below was
derived from the backend code as built (`hooks.py`, `decisions.py`,
`outbound.py`), not from the plan document.

---

## 1. Disposition of the original workflow

| Original branch (nodes) | Disposition | In the new workflow |
|---|---|---|
| `Receive WhatsApp Photo` webhook | REPLACED | `Console Trigger` (webhook `POST /webhook/pilot-checkbot`) → `Verify Secret` (drops payloads whose `secret` ≠ config) → `Trigger Context` (normalizes `{review_item_id, image_base64, qb_invoice, submitted_by}`; synthesizes caption `"invoice NNNN"` so the transplanted OCR prompt/parser behave exactly as in production) |
| Slabbot/SupplyBot/IG relays, `Real Group Only`, `Route by Type` | DROPPED | console sends only check photos to this webhook |
| `Get Full Image` (Evolution fetch) | REPLACED | image arrives as `image_base64` in the trigger payload |
| `Read Check with Claude` + `Parse Check Data` | **PORTED VERBATIM** | only swapped: image source, caption source, API key → `PASTE_ANTHROPIC_KEY` via Pilot Config |
| `Is Cash Photo?` + cash branch (17) | PORTED, first-class | dedup via `GET /find`, need-info/not-found are **questions** (freeform, multi-round), row writes via `POST /update`, Moraware note kept, outcome via `POST /final` |
| Smart Search, AV subsystem, Vet/Rescue, Pick Best Match, Match Result | **PORTED VERBATIM** | QB-reading steps sit behind `qb_verification_enabled` (default **false**); with the flag off, AV falls back to smart-search + 2-word-name ranking (its own built-in fallback path) and Vet/Rescue are bypassed |
| Duplicate detection (Airtable searches) | REPLACED | `GET /find?check_number&amount` (main), `?check_number&invoice_number` (fast path), `?invoice_number&amount` (cash), always excluding the current `review_item_id`. Check duplicates ask **Record anyway / Ignore**; cash duplicates finalize as `duplicate` (matches the original's terminal reply) |
| QB-invoice fast path (`Photo Has Invoice?` → AC chain) | PORTED | driven by the manager's `qb_invoice` (rides the synthesized caption) or the OCR'd invoice; QB verification reads gated |
| WhatsApp confirmation / candidates / not-found / still-not-found replies + cross-execution reply matching | REPLACED | `POST /question` with candidate buttons + `resume_url = $execution.resumeUrl` → **Wait node (webhook resume)** → `Parse Decision` handling `{choice:{label,job_id}}` and `{text}` exactly as `decisions.py` sends them |
| `Parse Correction` → re-search | PORTED (adapted) | typed text: Moraware lead URL → job id; 4-digit number → `job-by-invoice`; anything else → smart-search by name; not found → **asks again** (multi-round, backed by console part 6) |
| Confirm chain (`Mark Confirmed`, `Build Payment Note`, `Create Moraware Activity`) | PORTED | Airtable update → `POST /update`; **Moraware note is LIVE** (targets the fake TEST jobs); `Build Confirmation` was re-derived as `Build Final Message` (formatting only — reports QB detail when the flag is on, Moraware success/failure always) |
| Post-confirm QB verification (`Get QB Invoice` → `Classify Payment`) | CONFIG-FLAGGED, default off | `Classify Payment` (8.8k chars) ported verbatim; company URL is `PASTE_QB_COMPANY_URL_WHEN_ENABLING` so prod QB cannot be hit accidentally |
| 5× Google Drive archive chains | CONFIG-FLAGGED, default off | ONE chain (after Final: Confirmed), folder id `PASTE_DRIVE_FOLDER_ID_WHEN_ENABLING`; fast-path/cash outcomes do not archive in the pilot |
| Dispatch queue + digest | DROPPED | one push per item (console design decision) |
| Hourly trigger, Evolution session health, QB invoiceless-jobs sweep | DROPPED | sweep returns later as its own workflow (owner decision 2026-07-17) |
| CC text branch (23 nodes) | DROPPED, documented | future console feature: text-payment entry |

## 2. Scenario test matrix (execute manually after import)

Send each through the console's Submit check page (or `/resend`). "Card" =
`/payments/item/{id}`.

| # | Scenario | Expected card behavior | Expected workflow path |
|---|---|---|---|
| 1 | Happy path, QB invoice entered at submit | Feed: "Sent to CHECK-BOT" → "Read the check: …" → "Matched to X by invoice #N" → green final "✓ … recorded", status `confirmed`, push fires. No question ever shown. | Trigger → OCR → `Photo Has Invoice?` → `Photo Job By Invoice` → dedup → (QB gate off) → AC Build Row → update → Moraware note → final |
| 2 | No invoice, smart-search finds candidates, tap one | Amber question with big candidate buttons; tap-confirm; card resolves; final line + push | Smart Search → AV (no QB) → Pick Best Match → Match Result → question → Wait → ChoseJob → invoice-by-job → update → note → final |
| 3 | Freeform reply: 4-digit invoice | "None of these" → type `1042` → card resolves after workflow matches by invoice | Wait → Parse Decision (`invoice_text`) → `Text Job By Invoice` → Select Job → confirm chain |
| 4 | Freeform reply: job name | Type a name → workflow smart-searches it → resolves, or asks again with "Still couldn't find…" (card reopens — round 2) | Wait → Parse Decision (text) → Parse Correction → Search Corrected Job → found/not-found loop |
| 5 | Cash photo | Question "That looks like cash — amount + invoice?" → reply `500 1042` → matched, `confirmed`, Moraware note | Is Cash → (no caption amount) → Ask: Cash Info → Wait: Cash → Parse Cash Info → job-by-invoice → dedup → record |
| 6 | Duplicate check submitted twice | Second submission asks "already recorded — Record anyway / Ignore"; Ignore → status `duplicate`, nothing recorded; Record anyway → normal confirm | `GET /find` dedup (excludes self) → Ask: Duplicate → Wait → IgnoreDuplicate or ChoseJob |
| 7 | Unreadable OCR (blurry photo) | Question "couldn't read a usable name or address — type name or invoice #" (freeform only) | `Readable?` false → Ask: Unreadable → Wait → text routes |
| 8 | Resume endpoint down (kill the n8n execution, then answer) | Decision POST returns 502; card shows "workflow couldn't be reached — nothing recorded"; options stay open; `/resend` restarts | console-side compensating rollback (part 4) — nothing to configure in n8n |

## 3. Config checklist (on import)

1. **Pilot Config node** — fill every placeholder:
   - `console_base_url` — the Azure console URL, no trailing slash
   - `pilot_hook_secret` — must equal the console's `PILOT_HOOK_SECRET` env var
   - `anthropic_api_key` — a **fresh** key (rotate the one that sat in the old export)
   - `bridge_base_url` — prefilled `https://bridge.emgcheckbot.us`
   - `qb_verification_enabled` — leave **false** for sandbox. The 7 QB nodes
     ship **disabled** so the workflow activates without a QB credential.
     When enabling later: attach a QuickBooks OAuth2 credential, re-enable
     those nodes, set `qb_company_url` (sandbox realm:
     `https://sandbox-quickbooks.api.intuit.com/v3/company/<realmId>`), flip
     the flag.
   - `drive_archive_enabled` — leave **false**. The 3 Drive nodes ship
     **disabled** likewise; to enable: attach a Google Drive credential,
     re-enable them, set `drive_folder_id`, flip the flag.
2. **Console side** — set `N8N_PILOT_WEBHOOK_URL` to this workflow's production
   webhook URL (`…/webhook/pilot-checkbot`) in the console's env, both locally
   and on Azure, then restart the app (deploys don't restart it).
3. **Activate** the workflow (it imports inactive on purpose).
4. Credentials were **stripped** from every transplanted node — nothing from
   production n8n leaks in; attach fresh ones only for the flags you enable.

## 4. Open TODOs / known limits

- **Bridge endpoint paths taken from the export, not verified against bridge
  source** — confirm manually: `POST /api/checkbot/job-by-invoice`
  (`{invoiceNumber}` → `{Found, JobId, CustomerName, LeadUrl, Source}`),
  `POST /api/checkbot/invoice-by-job` (`{jobId}` → `{InvoiceNumber…}`),
  `POST /api/checkbot/find-job-by-name` (`{job_name, limit}`),
  `POST /api/checkbot/smart-search`, `POST /api/checkbot/create-activity`
  (`{jobId, notes, activityTypeId: 17}` — **live writes to TEST jobs**).
- A `/resend` while an old execution still waits on a question will make the
  new execution's first question 409 (one open question per item). Answer or
  ignore the stale question first; the failed new execution can be re-fired.
- Fast-path and cash outcomes don't run the Drive archive chain (main confirm
  path only) — extend later if wanted.
- Chosen-candidate buttons don't carry the Moraware URL back through the
  decision endpoint (`{label, job_id}` only), so the final line shows the URL
  only when the workflow re-derived it. Cosmetic.
- Multi-round Q&A requires console ≥ part 6 (`71b8403`).
- The hourly QB invoiceless-jobs sweep is intentionally absent — it arrives
  later as its own workflow from a separate export.

## 5. PLAIN WORDS

The old bot lived in WhatsApp and kept its records in Airtable. This new
workflow is the same brain — the exact same battle-tested code reads the
check photo, tells cash from checks, hunts for the right Moraware job, spots
duplicates, and writes the note on the job — but its mouth and its memory
have moved into your console. When it needs a human, it doesn't send a
WhatsApp message: it puts an amber question card on the payment, sends a
push, and then literally goes to sleep until someone taps a button or types
an answer, at which point it wakes up exactly where it left off and keeps
going — asking again if the answer didn't pan out. Everything risky
(QuickBooks, Google Drive) ships switched OFF, and every secret is a blank
you fill in on import. The Moraware notes are the one live wire — they land
on the fake TEST jobs on purpose, so you can watch the whole loop run for
real without touching production money.
