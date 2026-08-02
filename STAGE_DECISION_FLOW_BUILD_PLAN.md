# EMG Ops Console — Decision flow build plan (sandbox pilot, payments)

> Audience: Claude Code. Read CLAUDE.md + PROJECT_STATE.md first. Push mechanics
> are DONE (subscriptions, VAPID, test-send, PWA). This stage builds the layer
> that makes push useful: the shared decision card, the item activity feed, and
> the webhook contract that lets the owner's cloned n8n workflows drive the
> console instead of WhatsApp.
>
> CONTEXT: everything runs against a SANDBOX ecosystem — the owner's cloned
> n8n workflows use QuickBooks sandbox and fake Moraware jobs. Production
> WhatsApp/Airtable flows continue untouched in parallel. The console side you
> build here is environment-agnostic (it doesn't know or care that QB is
> sandbox — that's n8n's business).
>
> HARD CONSTRAINTS: no QuickBooks credentials or calls in the console, ever.
> No Airtable code changes. Hooks operate ONLY on console-born rows
> (source='console', airtable_id NULL) — reject mirrored rows with 403.
> No new dependencies (pywebpush already present).

## 1. Schema (one migration)

### item_events — the shared activity feed
id uuid PK · review_item_id uuid NOT NULL indexed (plain uuid, no FK — feed
survives deletion, same rationale as audit_log) · kind text CHECK IN
('system','bot_update','bot_question','decision','comment') · body text NOT
NULL (human-readable line) · payload jsonb NULL · actor_email text NULL
(NULL = bot/system) · created_at timestamptz default now().

For kind='bot_question', payload shape:
`{candidates: [{label, sublabel, job_id, moraware_url}], resume_url,
allowed_freeform: bool, format_hint: str|null}`.

**Partial UNIQUE index on (review_item_id) WHERE kind='decision'** — first
tap wins is enforced by the database, not by application politeness.

### payment_details additions
`qb_invoice` text NULL (the manager-entered 4-digit fast-path value — distinct
from invoice_number, which the bot fills from QB) · `qb_payment_id` text NULL
indexed (set by the sweep workflow via hooks; the dedup key for QB payments).

## 2. Config additions (.env.example documented, fail-fast where marked)

- `N8N_PILOT_WEBHOOK_URL` — optional; empty = outbound trigger disabled (dev
  without n8n keeps working).
- `PILOT_HOOK_SECRET` — required; 40-char shared secret authenticating
  n8n ⇄ console in both directions.
- `PILOT_PUSH_EMAILS` — comma list; push fan-out for questions/resolutions is
  restricted to this list ∩ role admin/manager. Currently the two pilot
  accounts.

## 3. Capture flow addition — the QB invoice fast path

On the photo PREVIEW screen (after capture, before Use photo): one optional
field labeled "QB invoice # (optional)": numeric keypad (inputmode=numeric),
accepts exactly 4 digits or empty — client validates, server re-validates
(422 otherwise). Stored to payment_details.qb_invoice on submit. Include it
in the outbound webhook payload. When present, the n8n workflow takes the
auto-match path and the card is born resolved — no question ever appears.

## 4. Outbound trigger (console → n8n)

On successful POST /api/checks, if N8N_PILOT_WEBHOOK_URL is set: background
task POSTs `{review_item_id, image_base64 (server-side re-encode, longest
side ≤1568px, JPEG q85), qb_invoice, submitted_by, secret}`. Failure → log +
system item_event "couldn't reach the workflow — will need manual retry";
never blocks the upload response. Also append system event "sent to
CHECK-BOT" on success. Add POST /api/review-items/{id}/resend (admin only) to
re-fire the trigger for a stuck item.

## 5. Inbound hooks (n8n → console) — header X-Pilot-Secret required (401),
console-born rows only (403 for mirrored), all field values pass the same
validators as the Stage 3 PATCH path.

- **POST /api/hooks/pilot/items** — CREATE a payment that arrived without a
  photo (the sweep's QB-discovered payments). Body: payment fields (amount,
  payer_name, payment_method, payment_type, invoice_number, txn_date,
  qb_payment_id) + optional initial body line. Creates review_items
  (item_type=payment, source='console', status from body default
  'needs_job') + payment_details + a system event. Returns the item id.
- **POST /api/hooks/pilot/update** — `{review_item_id, body, fields?,
  status?}`: append bot_update event; optionally set whitelisted
  payment_details fields and/or status. This is OCR results landing.
- **POST /api/hooks/pilot/question** — `{review_item_id, body, candidates[],
  resume_url, allowed_freeform, format_hint?}`: append bot_question event,
  set status='needs_job', SEND PUSH to the pilot pool: title from item
  ("Check $4,850 needs a job"), deep link to /payments/item/{id}. Reject
  (409) if an unanswered bot_question already exists for the item.
- **POST /api/hooks/pilot/final** — `{review_item_id, body, status}`: append
  system event, set final status, SEND RESOLUTION PUSH to the pool
  ("✓ $4,850 → Simmons"). This fires for both the fast path (invoice
  entered) and the post-decision completion.
- **GET /api/hooks/pilot/find** — the dedup/query brain the workflows used to
  get from Airtable. Query params (any combination): qb_payment_id,
  check_number, invoice_number, amount. Returns matching console-born items
  (id, status, key fields). The sweep calls this before creating anything.

## 6. Decision + conversation endpoints (human-facing, session auth)

- **POST /api/review-items/{id}/decision** — require_role(admin, manager).
  Body: `{choice: {job_id, label}}` OR `{text: "<freeform reply>"}`. Steps:
  (1) locate the latest unanswered bot_question (404 if none);
  (2) INSERT decision event — the partial unique index turns a race into a
  clean 409: respond with the existing decision's actor + time;
  (3) POST `{choice|text, secret}` to that question's resume_url;
  (4) resume failure → DELETE the decision event (compensating rollback) and
  return 502 "workflow unreachable — nothing was recorded" so the card stays
  answerable. On success append nothing extra — the workflow's own /final
  hook narrates the outcome.
- **GET /api/review-items/{id}/events** — payments roles; the ordered feed.
- **POST /api/review-items/{id}/comments** — `{body}` → comment event. No
  push for comments in v1.

## 7. Frontend — the decision card and its surroundings

**This is a flagship UI surface — apply the frontend-design skill in
.claude/skills/frontend-design deliberately.** It will be used primarily on
phones, from a push notification, by a manager standing in a warehouse. The
approved direction (mockup reviewed with the owner): a single card page —
header (amount + status badge) · photo thumb opening the existing Lightbox ·
OCR facts row (payer, check #, submitted by, QB invoice or "none entered") ·
the bot's question as a tinted bubble · large full-width candidate buttons
(label + sublabel: address, install date, invoice, open balance) · a
"None of these" freeform input with the format_hint as placeholder · the
Activity feed (icon-led lines, relative times, actor names) · a comment box.

States that must be first-class, not afterthoughts:
- **Open question** → buttons active, subtle attention cue on the question.
- **Resolved** → buttons replaced by a success banner "✓ {label} — {actor},
  {relative time}"; feed shows the full arc ending in the Moraware line.
- **Race loser** → tapping after someone else decided shows a calm inline
  notice naming the winner (from the 409 body), card re-renders resolved.
- **Workflow unreachable (502)** → readable error, decision NOT recorded,
  retry stays available.
- **Fast path** → card born resolved; feed tells the whole story with no
  question ever shown.

Liveness: while the card is open, poll /events every 5s and re-render
incrementally (feed grows, resolution appears) — no websockets. The payments
board: cards/rows with an open bot_question wear a "needs decision" chip
linking to the card; resolution clears it on next poll/refresh.

Push deep links: notification click routes to /payments/item/{id} (the sw.js
notificationclick handler already opens URLs — verify the route survives the
PWA cold start).

Design bar: this should look and feel high-end — confident type hierarchy,
generous touch targets (buttons ≥52px tall), restrained color (status
semantics only), motion limited to state transitions (resolution banner
appearing), dark-mode correct, thumb-reachable actions. Use Playwright MCP
to screenshot the card at 390px width in open/resolved/race states and
self-review against this section before presenting each part.

## 8. Tests

Hooks: secret required (401); mirrored row → 403; create-item builds
row+details+event; question sets status, stores payload, fires push to
pool∩roles only (mock webpush), 409 on double-question; final sets status +
resolution push; find matches by each key and combinations. Decision: happy
path calls resume with correct body (mock); DB-enforced single decision
(simulate race → one row, second gets 409 with actor); resume failure →
compensating delete + 502 + card still answerable; freeform path posts text;
role gating (yard 403). Events/comments: ordering, role gating. qb_invoice:
4-digit validation both ends; rides the outbound payload. Existing suite
stays green.

## 9. Out of scope

The n8n workflow clones themselves (owner's side — the hooks above are the
contract). Slab/supply/leads/follow-ups. WhatsApp, Airtable, production
QuickBooks, Moraware credentials in the console. Digest batching. Comment
push. Websockets. Roster changes.

## 10. Verification checklist (owner)

1. Migration applied locally + prod. PILOT_HOOK_SECRET + PILOT_PUSH_EMAILS
   set in both; N8N_PILOT_WEBHOOK_URL set once the cloned workflow exists.
2. **Curl pack**: Claude Code delivers a ready-to-run curl example for every
   hook (items/update/question/final/find) with the secret header — the
   owner tests each against LOCAL first, watching the card change live in
   the browser, BEFORE wiring real n8n.
3. Submit a check with a QB invoice # → card born resolved, resolution push
   arrives on both pilot phones, feed narrates the fast path.
4. Submit without invoice → (simulated via curl question) both phones get
   the push; tap on phone A → candidate buttons; choose one → resolved
   banner; phone B taps its notification → sees resolved card with A's name.
5. Race test: two browsers, same open question, near-simultaneous taps →
   exactly one decision recorded, loser sees the winner named.
6. Freeform: type an invoice number in "None of these" → resume receives
   text (visible in the curl-mock or n8n execution).
7. Kill the resume endpoint → decision attempt → 502, nothing recorded,
   card still answerable after restore.
8. pytest green, count grown; PROJECT_STATE.md regenerated with the hooks
   contract documented as the n8n integration surface.
