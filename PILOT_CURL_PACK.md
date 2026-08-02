# Pilot hooks — curl pack (drive the console from your terminal)

Every inbound hook, ready to run in **PowerShell** from the repo root against
LOCAL (`http://localhost:8000`). This is exactly what the cloned n8n workflows
will send — you can watch the card change live in the browser before any n8n
exists. For PROD, swap `$B` for the Azure URL (the secret must match the
`PILOT_HOOK_SECRET` app setting there).

**Run these two lines first** (loads the secret from .env — never paste it):

```powershell
$s = (Select-String -Path .env -Pattern '^PILOT_HOOK_SECRET=(.+)$').Matches[0].Groups[1].Value.Trim()
$B = 'http://localhost:8000/api/hooks/pilot'
```

## 1. Create a photo-less payment (the sweep discovering a QB payment)

```powershell
curl.exe -s -X POST "$B/items" -H "X-Pilot-Secret: $s" -H "Content-Type: application/json" -d '{"amount":"4850.50","payer_name":"R. Simmons","payment_method":"check","qb_payment_id":"QBP-777","body":"Found in QB by the sweep: $4,850.50 from R. Simmons"}'
```
→ `{"review_item_id":"<uuid>"}` — **copy that uuid into a variable:**
```powershell
$id = '<paste-the-uuid>'
```

## 2. Find (the sweep's dedup check — call BEFORE creating)

```powershell
curl.exe -s "$B/find?qb_payment_id=QBP-777" -H "X-Pilot-Secret: $s"
curl.exe -s "$B/find?amount=4850.50"       -H "X-Pilot-Secret: $s"
curl.exe -s "$B/find?check_number=1234&invoice_number=5521" -H "X-Pilot-Secret: $s"
```
→ `{"items":[...]}` (empty list = safe to create). Any combination of
`qb_payment_id`, `check_number`, `invoice_number`, `amount`; at least one required.

## 3. Update (OCR results landing on the card)

```powershell
curl.exe -s -X POST "$B/update" -H "X-Pilot-Secret: $s" -H "Content-Type: application/json" -d "{`"review_item_id`":`"$id`",`"body`":`"OCR: check #1234 dated 7/10, payer R. Simmons`",`"fields`":{`"check_number`":`"1234`",`"txn_date`":`"7/10/2026`"}}"
```
Settable fields: amount, payer_name, payment_type, payment_method,
invoice_number, check_number, txn_date, caption_name, qb_payment_id.
(`status` rides as its own top-level key, not in fields. `qb_invoice` is
manager-only — hooks get 403.)

## 4. Question (the decision request → push to both pilot phones)

```powershell
curl.exe -s -X POST "$B/question" -H "X-Pilot-Secret: $s" -H "Content-Type: application/json" -d "{`"review_item_id`":`"$id`",`"body`":`"Which job is this check for?`",`"candidates`":[{`"label`":`"Simmons Kitchen`",`"sublabel`":`"123 Main St - installs 7/20`",`"job_id`":`"J-100`",`"moraware_url`":`"https://example.moraware.net/j/100`"},{`"label`":`"Simmons Bath`",`"sublabel`":`"9 Oak Ave - invoice 5521`",`"job_id`":`"J-101`",`"moraware_url`":`"https://example.moraware.net/j/101`"}],`"resume_url`":`"http://host.docker.internal:9999/resume`",`"allowed_freeform`":true,`"format_hint`":`"4-digit invoice number`"}"
```
→ `{"ok":true,"pushed":N}` · sets status `needs_job` · a SECOND question while
one is unanswered → **409** (by design). `pushed` is 0 locally unless a browser
has subscribed against the LOCAL backend — phones are subscribed to PROD.

## 5. Final (resolution — fast path and post-decision both)

```powershell
curl.exe -s -X POST "$B/final" -H "X-Pilot-Secret: $s" -H "Content-Type: application/json" -d "{`"review_item_id`":`"$id`",`"body`":`"[OK] $4,850.50 -> Simmons Kitchen (J-100)`",`"status`":`"confirmed`"}"
```
→ sets the final status + pushes the body line to the pilot pool.

## Guardrails you can verify (all return the shown code)

```powershell
# no/wrong secret -> 401
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "$B/items" -H "Content-Type: application/json" -d '{}'
# mirrored (Airtable-born) row -> 403: grab any mirrored id from the board and try /update on it
# forbidden field -> 403 (names the fields):
curl.exe -s -X POST "$B/update" -H "X-Pilot-Secret: $s" -H "Content-Type: application/json" -d "{`"review_item_id`":`"$id`",`"body`":`"x`",`"fields`":{`"qb_invoice`":`"1234`"}}"
# invalid value (amount <=0, >500000, non-numeric; unparseable txn_date) -> 422
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "$B/items" -H "X-Pilot-Secret: $s" -H "Content-Type: application/json" -d '{"amount":"-5"}'
```

Watch the feed while you drive:
```powershell
docker compose exec db psql -U emg -d emg_console -c "SELECT kind, left(body,60), created_at FROM item_events ORDER BY created_at DESC LIMIT 10;"
```
(Once part 5 lands, the card page at `/payments/item/<id>` re-renders live
every 5s instead — that's the intended way to watch.)
