# EMG Ops Console — complete implementation state

> Regenerated 2026-07-21 after the Decision flow + n8n pilot stage (audience:
> Claude Desktop / the owner). Everything described here is implemented, tested,
> and pushed to `github.com/SanyaBoroda4/emg-console` (main, 84 commits).
> Governing docs in repo root: STAGE1_BUILD_PLAN.md, STAGE1_ADDENDUM_FIELD_MAPPING.md,
> STAGE1_5_BUILD_PLAN.md, STAGE2_BUILD_PLAN.md, STAGE3_BUILD_PLAN.md,
> STAGE_DEPLOY_BUILD_PLAN.md, STAGE_PUSH_MECHANICS_SLICE.md,
> STAGE_DECISION_FLOW_BUILD_PLAN.md. This file supersedes them as the
> description of what EXISTS.
>
> **NEW since 2026-07-16: the full n8n pilot is LIVE** — the decision flow
> (item events, decision cards, pilot hooks, multi-round Q&A) plus two
> console-tailored n8n workflows (CHECK-BOT and PAYMENT-SWEEP clones, sandbox
> QuickBooks, live Moraware TEST-job notes) running against production Azure.
> Detail in §20.
>
> **The app is deployed and LIVE on Azure** (single-origin FastAPI serving the
> React bundle, auto-deployed by GitHub Actions on push to main) — detail in §18.
> **NEW since 2026-07-12: Web Push works end-to-end in production** — two real
> iPhones (installed as Home Screen web apps) receive lock-screen notifications,
> including a cross-account admin broadcast; iOS Google-login fixed via redirect
> mode. Detail in §19. Local docker compose unchanged (dev/prod parity held).

---

## 1. What this is

EMG (countertop fabrication, Charleston) runs production automations in n8n (CHECK-BOT,
SLABBOT, SUPPLYBOT, PAYMENT-SWEEP) that keep state in Airtable and ask humans for
decisions over WhatsApp. This repo is the **EMG Ops Console** — a FastAPI + PostgreSQL +
React web app gradually replacing Airtable as the bots' state store and WhatsApp as the
human decision surface. It runs in shadow mode alongside the bots.

Built so far, in order:
1. **Stage 1** — scaffold, Postgres in Docker, one-way Airtable mirror of the payments
   table, read-only Payments board, pytest suite.
2. **Stage 1.5** — routing + home switchboard, Payments table view (search/sort),
   Google Drive check-photo thumbnails + lightbox.
3. **Check capture** — HTTPS dev serving, photo upload API, bank-style phone camera
   flow with automatic capture, row deletion, brand pass.
4. **Stage 2** — Google sign-in, seven-person roster + roles, server-side role
   enforcement on every payments endpoint, role-aware UI.
5. **Stage 3** — field editing with per-role whitelists, **the console's first and
   only Airtable write path** (PATCH write-through, an owner-approved amendment to
   the read-only constraint), concurrency guard, and a deletion-surviving audit log.
6. **Deploy stage** — production home on Azure (App Service Linux B1 + managed
   Postgres), single-origin serving (FastAPI serves the built React bundle),
   GitHub Actions CI/CD (push to main = test-gated deploy), dependencies vendored
   into the package. Now LIVE and populated with real data (§18).
7. **Push mechanics slice** — Web Push proven on real phones in production:
   push_subscriptions table, VAPID keys, subscribe/unsubscribe/test-send API,
   service worker, PWA manifest (iOS Home Screen install), "Enable notifications"
   toggle + admin test buttons with per-recipient receipts, and (forced by iOS)
   redirect-mode Google sign-in. Verified 2026-07-15 on two iPhones (§19).
8. **Decision flow (parts 1–9)** — the item_events activity feed, the pilot
   hooks contract (items/update/question/final/find/list/notify/photo), the
   phone-first decision card with candidate buttons + freeform answers,
   DB-refereed first-tap-wins decisions with compensating rollback,
   multi-round Q&A (one decision per QUESTION), board chips
   (needs-decision / in-progress), matched-job fields, live-processing UX.
9. **n8n pilot** — script-generated, validated, secret-free console clones of
   the two production money workflows (CHECK-BOT photo flow with Wait-node
   resume; PAYMENT-SWEEP hourly QB scan with a second decision webhook),
   sandbox QuickBooks, Google Drive archiving of full-quality originals,
   strict one-push-per-payment doctrine. Detail in §20.

Runs BOTH locally (owner's laptop + LAN, docker compose — unchanged) AND in
production on Azure. Writes to Airtable happen ONLY via the Stage 3 PATCH path;
zero writes to Moraware, QuickBooks, WhatsApp.

## 2. Tech stack (fixed)

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2 (declarative, sync), Alembic,
  psycopg 3 (binary), pydantic-settings, pyairtable (reads for the mirror, updates
  for the edit path — no new dependency needed), pytest. Justified additions
  (comments in pyproject.toml): `httpx` (TestClient), `python-multipart` (multipart
  parser), `google-auth` + `itsdangerous` (Stage 2 auth), `pywebpush` (push slice —
  the one approved new dependency; brings py-vapid for the key generator).
- **Frontend**: React 18, Vite 5, TypeScript (strict), Tailwind CSS 3,
  react-router-dom 6, @vitejs/plugin-basic-ssl (dev-only). Google Identity Services
  via `<script>` tag. Nothing else — Stage 3 added zero dependencies.
- **Infra (dev)**: docker-compose with `db` (postgres:16), `backend`, `frontend`.
- **Infra (prod, §18)**: Azure App Service Linux B1 (Python 3.12, West US 3) running
  gunicorn + uvicorn workers via `backend/startup.sh`; Azure Database for PostgreSQL
  Flexible Server (B1ms); GitHub Actions (`.github/workflows/deploy.yml`) for CI/CD.
  `gunicorn` was already a declared dependency for exactly this.

## 3. Repository layout

```
emg-console/
├── .github/workflows/deploy.yml # CI/CD: test-gate → build → deploy to Azure (§18)
├── docker-compose.yml          # 3 services; volumes: pgdata, uploads;
│                               # frontend gets VITE_GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
├── .env.example                # every var documented; real .env gitignored
├── README.md / .gitignore
├── STAGE*_BUILD_PLAN.md / STAGE_DEPLOY_BUILD_PLAN.md / PROJECT_STATE.md
├── backend/
│   ├── Dockerfile / pyproject.toml / alembic.ini
│   ├── startup.sh              # PROD launcher (Azure Startup Command); self-locating
│   │                           # PYTHONPATH+FRONTEND_DIST; alembic upgrade + gunicorn
│   ├── alembic/versions/
│   │   ├── 0001_initial_schema.py      # review_items + payment_details + pgcrypto
│   │   ├── 0002_add_photo_path.py
│   │   ├── 0003_users_table.py         # roster + CHECK(role) + idempotent seed
│   │   ├── 0004_add_oleksandr.py       # roster change routine
│   │   ├── 0005_audit_log_and_last_edited.py   # Stage 3 (plan said 0004 — taken)
│   │   ├── 0006_promote_oleksandr_to_manager.py # push slice §0 (2nd pilot account)
│   │   └── 0007_push_subscriptions.py  # push slice §1
│   └── app/
│       ├── main.py             # create_app(): CORS, routers, top-level auth error shapes
│       ├── config.py           # fail-fast Settings; + airtable_write_token (Stage 3)
│       ├── db.py / models.py   # ReviewItem, PaymentDetails, User, AuditLog
│       ├── schemas.py          # + last_edited_*, AuditEntryOut/AuditListOut
│       ├── auth.py             # session cookie, get_current_user, require_role
│       ├── routers/
│       │   ├── health.py       # GET /api/health (open)
│       │   ├── auth.py         # /api/auth/google (popup) + /google/redirect
│       │   │                   # (iOS form-POST), /me (sliding renewal), /logout
│       │   ├── review_items.py # list/stats (admin+mgr), PATCH (whitelists),
│       │   │                   # DELETE (admin, audited)
│       │   ├── checks.py       # POST /api/checks, GET /api/photos (admin+mgr)
│       │   ├── audit.py        # GET /api/audit (admin ONLY)
│       │   └── push.py         # vapid-public-key, subscribe, unsubscribe,
│       │                       # test-send (admin; scope self|all)
│       ├── scripts/            # mirror_airtable.py, generate_vapid_keys.py
│       └── tests/              # conftest + mirror/api/checks/auth/edit/push — 67 tests
└── frontend/
    ├── Dockerfile / package.json / vite.config.ts (https, proxy, polling, .nip.io)
    ├── index.html              # + GIS <script>, manifest + apple-touch-icon links
    ├── public/                 # sw.js (push-only SW), manifest.json (PWA/standalone),
    │                           # icon-192/512.png + apple-touch-icon.png (placeholder E)
    └── src/
        ├── App.tsx             # /login public; RequireAuth everywhere;
        │                       # /payments* RequireRole(admin,manager);
        │                       # /payments/audit RequireRole(admin)
        ├── api.ts              # + ApiError, patchReviewItem, fetchAudit, push helpers
        ├── types.ts            # + last_edited_*, AuditEntry/AuditList
        ├── global.d.ts / lib/ (AuthContext, roles, format, driveImage,
        │                       push.ts — SW registration + subscribe/unsubscribe)
        ├── pages/ (LoginPage, HomePage, PaymentsPage, SubmitCheckPage, AuditPage)
        └── components/ (Layout, RequireAuth, Logo, PaymentCard, PaymentsTable,
                         StatusFilter, CheckThumb, Lightbox, ConfirmDialog,
                         AmountEditor, EditedChip, NotificationsControl)
```

## 4. Environment & running

`.env` (all documented in `.env.example`): `POSTGRES_*`, `DATABASE_URL`,
`AIRTABLE_TOKEN` (read-only PAT — mirror only), **`AIRTABLE_WRITE_TOKEN`** (Stage 3:
second PAT, data.records:write, EMG logs base only — ONLY the edit path uses it;
least privilege: a mirror-token leak still can't modify Airtable),
`AIRTABLE_BASE_ID=appXXXXXXXXXXXXXX`, `AIRTABLE_PAYMENTS_TABLE=tblXXXXXXXXXXXXXX`,
`GOOGLE_CLIENT_ID`, `SESSION_SECRET`, `ENVIRONMENT`, `UPLOAD_DIR` (default
/data/uploads). All required vars fail fast at startup. Push slice adds
**`VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT`** — deliberately
OPTIONAL (airtable_token precedent): missing keys degrade push ("not configured"),
never block boot. Generate with `python -m app.scripts.generate_vapid_keys`; the
SAME pair currently sits in local .env and the Azure app settings. Remember: env
values load at container CREATION — after editing .env run
`docker compose up -d --force-recreate backend`.

Run: `docker compose up --build`. Frontend: **https**://localhost:5173 (self-signed,
accept once). LAN/phone: nip.io hostnames (`https://192.168.1.50.nip.io:5173`)
registered as Google OAuth origins; office-router DNS-rebind protection blocks
nip.io → phones use manual DNS 8.8.8.8 on that Wi-Fi. Mirror (manual):
`docker compose exec backend python -m app.scripts.mirror_airtable [--dry-run]`.
Tests: `docker compose exec backend pytest` → **67 passed**.

## 5. Database schema (migrations 0001–0007)

### review_items
uuid PK · item_type (`payment`) · status text verbatim (confirmed/needs_job/
submitted seen; `pending` never observed) · source (`airtable_mirror`|`console`) ·
airtable_id unique/NULL · photo_drive_url · photo_path · matched_job_* /
moraware_url / match_method · raw jsonb (full Airtable fields; {} console) ·
created_at/updated_at · **last_edited_at timestamptz NULL, last_edited_by text
NULL** (Stage 3 — cheap "edited" chip fields; full history in audit_log).

### payment_details (1:1, PK=FK CASCADE)
amount numeric(12,2) · payment_method · payment_type · payer_name ·
invoice_number · txn_date date · check_number · caption_name · date_received.

### users (roster; changes = migration/SQL, no UI)
email PK lowercase · display_name · role CHECK(admin/manager/yard) · created_at.
7 people: alex@+bills@ admin; natalia@+victor@+**owner-phone@example.com**
manager (promoted from yard in 0006 — second push-pilot test account; the Gmail
needs an External OAuth consent screen); volodymyr@+wes@ yard.

### audit_log (Stage 3 — deliberately NO FK: history survives row deletion)
id uuid PK · review_item_id uuid indexed (plain uuid, not FK) · item_label text
(human snapshot, e.g. "payment $4,850.00 — R. Simmons") · actor_email ·
action CHECK('edit'|'delete') · field NULL · old_value/new_value text NULL
("" vs NULL preserved) · created_at.

### push_subscriptions (push slice, 0007 — one row per browser/device)
id uuid PK · user_email indexed (plain text, no FK — like audit_log) ·
**endpoint text UNIQUE = the upsert key** (re-subscribing from the same browser
updates in place, never duplicates) · p256dh + auth NOT NULL (payload encryption
keys from the browser's PushSubscription) · user_agent NULL · created_at.
Dead endpoints (push service returns 404/410) are pruned during sends.

## 6. Airtable mirror

Unchanged mechanics: read-only token, idempotent upsert by airtable_id, never
deletes, one transaction, --dry-run, field-name logging, status-distribution
summary. Amount → Decimal quantized to cents; tolerant dates incl. live
`M/D/YYYY H:MMpm`; numbers cast to text. LastQBScanTime churn → mass "updated"
expected. **Stage 3 interplay: the PATCH path syncs `raw` after every mirrored-row
edit, so the next mirror run reports the edited row as UNCHANGED — console edits
and the mirror do not fight.**

## 7. API (FastAPI, /api)

### Enforcement map
| endpoint | roles |
|---|---|
| GET /api/health | open |
| POST /api/auth/google, /google/redirect, /logout | open |
| GET /api/auth/me | any valid session (re-issues cookie — sliding renewal) |
| GET /api/review-items, /stats | admin, manager |
| POST /api/checks, GET /api/photos/{id} | admin, manager |
| **PATCH /api/review-items/{id}** | admin, manager — **per-field whitelists below** |
| DELETE /api/review-items/{id} | admin only (audited) |
| **GET /api/audit** | **admin only** |
| GET /api/push/vapid-public-key | any valid session (key is public by design) |
| POST /api/push/subscribe | admin, manager (yard excluded, matches payments) |
| POST /api/push/unsubscribe | any valid session (204 idempotent) |
| **POST /api/push/test-send** | **admin only** — body `{scope: 'self'\|'all'}`, default self |

### POST /api/auth/google/redirect (iOS login path)
GIS `ux_mode='redirect'` target: Google form-POSTs `credential` +
`g_csrf_token` here (double-submit CSRF check against the cookie), the shared
verifier maps it to a roster user, the session cookie is set on a 303 to `/`;
failures 303 to `/login?error=<message>` (top-level navigation — JSON would be
a dead end). Used because the GIS popup flow white-screens on iPhone Safari
and inside Home Screen web apps; desktop keeps the popup flow.
**The login_uri must be listed in the OAuth client's Authorized redirect URIs**
(prod URL + https://localhost:5173 variant are registered).

### POST /api/push/test-send (push slice — pipe check, not the real fan-out)
scope='self' → the caller's own devices; scope='all' → EVERY subscription
(owner-approved amendment: the admin verifies any manager's phone during
onboarding). Message names the sender for 'all'. Sends via pywebpush with
**ttl=3600 + Urgency high** (the library's ttl=0 default silently drops pushes
to locked phones — hard-won lesson). 404/410 endpoints pruned. Returns
`{sent, pruned, recipients: {email: device_count}}` — the UI prints the
recipient list so every click is self-evident about who it targeted. Each send
is logged (scope, caller, recipients) → App Service Log stream. 503 when VAPID
keys are unset. Delete or gate this once the real decision-card push lands.

### PATCH /api/review-items/{id} — the ONLY Airtable write path (Stage 3 §0)
Body `{changes: {field: value}, expected: {field: old_value_client_saw}}`.
- **Whitelists (server-enforced regardless of UI)**: manager → `amount` only;
  admin → amount, payer_name, payment_type, payment_method, invoice_number,
  check_number, txn_date, caption_name. Anything else (status, matching fields,
  photos…) → 403 naming the offending fields. Those belong to the bots.
- **Validation** (422 with plain messages): amount Decimal via str(), >0, ≤500,000,
  quantized to cents; txn_date via the mirror's tolerant parser; texts trimmed,
  ≤200 chars. Empty string = clear (NULL).
- **409 guard**: each changed field's current value is compared (string-normalized)
  against `expected[field]`; mismatch → `{"error":"stale","field","current"}` —
  two people can't silently overwrite each other.
- **Write-through**: mirrored rows PATCH Airtable FIRST (write token,
  `typecast=True`, our columns mapped back: amount→Amount, payer_name→PayerName,
  … txn_date→PaymentDate written as `M/D/YYYY 12:00pm` to match the live text
  format). Airtable failure → 502 `airtable_write_failed`, NOTHING changes
  locally, no audit row. Console rows skip Airtable entirely.
- **Then one local transaction**: apply values, sync `raw[<Airtable field>]`
  (delete key on clear — Airtable omits empty fields), set last_edited_at/by,
  one audit_log row PER CHANGED FIELD. Returns the full serialized item.

### GET /api/audit
`review_item_id` optional filter, limit ≤200 default 50, offset. `{entries,total}`
newest first — raw ledger rows, nothing derived.

### DELETE
Unchanged semantics + writes an action='delete' audit row (item_label snapshot)
before deleting — deletion history survives the deletion. Mirrored rows still
resurrect on the next mirror run (disclosed in the UI dialog).

### Data endpoints
Unchanged from Stage 2: list newest-payment-first (COALESCE fallback chain, no
casts), stats GROUP BY, uploads (submitted/console, 15MB, jpeg/png), photo
serving. Decimals as exact JSON strings; `raw` never exposed. CORS: GET, POST,
DELETE, PATCH from https://localhost:5173.

## 8. Frontend

**Auth shell** (Stage 2 + push-slice login fix): AuthProvider /me on mount
(flash-free), RequireAuth everywhere, RequireRole(admin,manager) on /payments*,
forever-sessions. LoginPage branches by device: iOS → GIS redirect mode
(full-page to Google and back via /api/auth/google/redirect; reads
`?error=` on return), everything else → the proven popup flow. Top bar:
☰ hamburger (all role-visible tabs) · EMG monogram · user badge only.
Home tiles role-filtered (admin 5 / manager 4 / yard 2).

**Notifications (push slice)**: `public/sw.js` = push-only service worker
(`push` → showNotification; `notificationclick` → focus/open the tab; NO
caching/offline). Registered on app load for payments roles. `lib/push.ts`
wraps support detection / subscribe / unsubscribe. The user-badge menu gains
a **NotificationsControl** (admin+manager): "Enable notifications" ↔
"Notifications on ✓" toggle with plain states (unsupported → iOS hint to Add
to Home Screen; denied → point at browser settings), and for admins two test
buttons once subscribed — **"Test my devices"** and **"Test ALL team devices"**
— whose result line names every recipient ("Sent to: alex@… (2), …").
PWA install: `manifest.json` (standalone, EMG-blue) + generated placeholder
icons + apple-touch-icon links in index.html — REQUIRED for iOS: Apple only
exposes Web Push to Home-Screen-installed web apps (16.4+), never browser tabs.

**Editing UI (Stage 3)**:
- **Cards**: pencil beside every amount (all payments-page users) → AmountEditor
  modal: struck-through current, input, then explicit "Change amount $X → $Y?"
  confirm (Enter advances/confirms, Esc cancels); mirrored rows warn "This also
  updates Airtable."; 409 shows "someone just changed this to $X — review and
  retry" and refreshes its baseline; 502 shows "couldn't reach Airtable — nothing
  was changed."
- **Table**: admin gets click-to-edit cells for payer/date/method/type/invoice/
  check # (input in place; Enter commits, Esc/blur cancels — blur NEVER commits);
  amount goes through the same confirm modal for both roles; managers see plain
  cells otherwise. Hover shows a pencil affordance. Inline-edit errors surface in
  a dismissible amber banner; 409s also refresh the view. Known gap: caption_name
  is whitelisted but has no table column, so no cell edits it (flagged to owner).
- **"edited" chip**: any row/card with last_edited_at wears a neutral gray chip;
  tooltip "by {email}, {relative time}". For admins the chip is a link to the
  filtered audit page.
- **Audit page** `/payments/audit` (admin-only route AND endpoint): deliberately
  boring ledger — When · Who · Action · Item · Field · Old → New — newest first,
  Load more, `?review_item_id=` filter with a "showing one payment" banner, empty
  state "No changes recorded yet." Toolbar "Audit" button renders for admins only.

**Payments page**: "Payments · YEAR" heading (year from newest payment),
Cards/Table toggle in ?view=, All/Confirmed/Pending pills, Audit + Submit check
buttons upper-right above the total, admin-only delete affordances (swipe-left on
phone cards / trash icons / table column) with warning-bearing ConfirmDialog.

**Capture flow** (unchanged): auto-capture (sharpness+paper+stillness leaky
bucket, ~0.4s), sideways-ready guide in portrait, crop+rotate, preview
Retake/Rotate/Use, XHR progress, photo kept on failure, native-input fallback.

## 9. Tests (80, SQLite in-memory, Google + Airtable + webpush + resume mocked)

- conftest: https-base TestClient logged in as seeded admin; `login_as()` helper;
  env (UPLOAD_DIR, SESSION_SECRET, GOOGLE_CLIENT_ID, AIRTABLE_WRITE_TOKEN) patched.
- test_mirror (6) / test_api (8) / test_checks (7): as before.
- **test_auth (15)**: Stage 2 twelve + three redirect-mode cases (valid form POST
  → 303 to / with session cookie; CSRF mismatch → 303 /login?error, no cookie;
  unknown email → 303 with the "isn't set up" message, no cookie).
- **test_push (10)**: subscribe creates/upserts-by-endpoint (no duplicates),
  yard 403, unsubscribe removes + idempotent 204, vapid-public-key returns the
  configured key, test-send 503 unconfigured / admin-only (manager+yard 403) /
  self scope hits only the caller's subs / all scope hits every user with ttl>0
  and exact recipients map / mocked-410 endpoint pruned from the table.
- **test_edit (21, Stage 3)**: whitelist matrix (manager amount ok / payer 403,
  admin payer ok, yard 403, status locked for everyone); amounts 0/-5/600000/abc
  → 422, "4850.5" → Decimal("4850.50"); unparseable date 422; stale expected →
  409 with current, fresh → 200; mirrored PATCH sends exactly the mapped payload
  (record id, typecast, `7/4/2026 12:00pm` date, None for clears) and syncs raw
  such that the mirror's own upsert reports "unchanged"; Airtable failure → 502
  with amount/last_edited/audit all provably untouched; console PATCH makes zero
  Airtable calls; one audit row per field with exact old/new; delete tombstone
  outlives the row; audit feed admin-only + filter; last_edited set on edit,
  absent before; unknown item 404.

## 10. Deviations from the plans (each with its reason)

Push slice:
1. **VAPID vars optional, not the plan's fail-fast** — a missing push key must
   degrade push ("not configured"), never take the whole console down
   (airtable_token precedent; the deploy stage's env-crash-loop informed this).
2. **test-send gained scope='all'** (plan said "not a broadcast tool") —
   owner-approved during verification: the admin needs to ring any manager's
   phone to verify it during onboarding; response/UI name every recipient.
3. **Unplanned but forced by iOS**: PWA manifest + icons (Apple only allows Web
   Push for Home-Screen-installed apps) and redirect-mode Google login (the GIS
   popup white-screens on iPhone Safari / inside installed web apps).

Stage 3:
1. **Migration numbered 0005, not the plan's 0004** — 0004 was consumed by the
   Oleksandr roster migration created after the plan was drafted.
2. **caption_name edit gap** — whitelisted per plan §1, but the table has no
   caption column (it only appears as a payer fallback), so no UI edits it.
   Surfaced to the owner; add a column or a detail editor if wanted.
3. PaymentDate write-back format `M/D/YYYY 12:00pm` per plan — the plan itself
   mandates owner verification of the first real date edit against Airtable.

Stage 2 (carried): 400-day sliding sessions (owner's "once in, forever in" vs
plan's 30 days); DELETE admin-only (owner un-deferred per-action permissions);
hamburger replaced the plan's nav links; email_verified hardening; single-source
VITE_GOOGLE_CLIENT_ID via compose; https test client (Secure cookie).

Earlier (carried): newest-payment-first ordering (owner override of both Stage 1.5
directions); no ::date casts (SQLite tests); stats cards removed; QB mark;
no orientation gate; polling file-watch.

## 11. Owner overrides & durable preferences (cumulative)

Newest payment first · payment's own date on cards ("Paid …") · single total, no
stat cards · table full-width, Payer first (width-capped), toggle pinned left,
action buttons upper-right · QB mark for photo-less payments · auto-capture
near-instant but never on non-checks; no orientation gate · deletes admin-only
(Alex + Nora Adams), confirmed, resurrection disclosed · money edits always
confirm old → new · "once in forever in" sessions · hamburger everywhere,
badge-only top bar · PLAIN WORDS + exact verification commands after every part.

## 12. Live-data facts (2026-07-15)

**180 mirrored records** (statuses: confirmed 174 / needs_job 6, +console
submitted) — same 180 loaded into BOTH the local and the Azure prod DB.
Some mirrored records have Drive photos (`/file/d/<ID>/view`). Live PaymentDate
format `5/17/2026 8:00pm`. LastQBScanTime churns (mass "updated" on re-mirror is
normal, but a FRESH DB reports all inserts). Owner's real-Airtable edit drill
(Stage 3 plan §8 steps 2–3, 7–8) was pending at the time of writing.
**Prod push_subscriptions**: ~5 rows — alex@ (iPhone PWA via web.push.apple.com
+ PC Chrome via fcm.googleapis.com) and owner-phone@ (iPhone PWA ×2 — one
stale from a reinstall, prunes itself on the next 410 — + PC Chrome).

## 13. Environment quirks (Windows dev machine)

Bind mounts drop file events → Vite usePolling. `curl` is Invoke-WebRequest in
PowerShell 5.1 → use `curl.exe`. Self-signed cert accepted once per device; old
http:// bookmarks = silent white screen. nip.io blocked by office-router DNS-rebind
protection → phone DNS 8.8.8.8. Containers read .env at creation →
`--force-recreate` after .env changes. LAN IPs: Emg2025/EMG → 192.168.1.50,
CBCI-570E-5 → 192.168.1.50.

## 14. Constraints in force

Airtable writes ONLY via the Stage 3 PATCH path (transition scaffolding — dies at
cutover with the mirror). Zero writes to Moraware/QuickBooks/WhatsApp. No
scheduler, no OCR, no roster UI, no bulk edit, no undo buttons (the audit log is
the undo reference), no status/job-match editing. **Deploy is now DONE (§18)** —
prod uses its own SESSION_SECRET and Postgres, distinct from dev. On plan
conflict: stop and surface the tradeoff.

## 15. Deferred / known follow-ups (agreed backlog)

**NEXT STAGE (owner requirement, 2026-07-15): upload fan-out** — every check
(later: delivery slip) uploaded by a manager pushes to all OTHER subscribed
admins/managers ("New check from {name} — tap to review", link to the item);
exclude the actor; fire-and-forget so a push failure never breaks an upload.
Generalize test-send's loop into a shared notify helper. Owner also weighed
WhatsApp-link / SMS channels for zero-install phones — parked once iPhone PWA
push was proven; revisit if managers resist the Home Screen install.

Then / still open: full push pilot (decision cards, n8n webhooks) · n8n
integration for check uploads (poll status=submitted or webhook) · OCR on
submitted checks (auto-fill amount/payer/check#; also true is-a-check capture
detection) · status workflow actions (confirm/correct — the natural Stage 4) ·
Follow-ups queue (Airtable "Stalled Jobs"), Slab deliveries + Supply log boards ·
caption_name edit surface if wanted · owner's real-Airtable edit verification ·
server-side search past ~1000 rows · Drive share settings/image proxy if
thumbnails break widely · "hidden" flag for mirrored deletes · ConfidenceFlags
badge · roster management UI · real EMG logo file (placeholder "E" PWA icons
too) · auto-capture threshold tuning · audit retention policy · retire or gate
test-send once the real fan-out lands · rotate the Postgres password + VAPID
keys someday (both appeared in a chat session; low risk, owner declined for now).

## 16. Commit history (oldest → newest, abridged to stage boundaries)

Stage 1: 28820d5…b3e1369 (plan, scaffold, schema, mirror, API, board, tests)
Stage 1.5: 11c5963…e938b28 (+ owner reorderings; PROJECT_STATE @ ed790ea)
Check capture: 4777c2f…cc461d9 (https, upload, camera, delete, auto-capture, brand)
Stage 2: 03d5de9…382fa9f (plan, roster, auth, login UI, forever-sessions,
  hamburger, tests, admin-only deletes, Oleksandr; PROJECT_STATE @ ea0f794)
Stage 3: 84729d0 (audit_log migration) · 61536ce (PATCH/write-through/audit
  endpoints) · e294cb2 (editing UI + audit page) · ed1e341 (21 edit tests)
Deploy: d9253de (plan) · c2e81fe (part 1: single-origin serving) · 93e3080
  (part 2: GitHub Actions pipeline) · 4f2b2a1 (fix: universal frontend lockfile)
  · 5a363bd (fix: real Azure hostname in summary) · d96e7cb (fix: vendor deps at
  build time, not Oryx) · 42b0f84 (fix: VITE_GOOGLE_CLIENT_ID into build) ·
  20cda4e (fix: portable manylinux2014 wheels + self-locating startup)
Push slice: e79a004 (roster 0006) · b2f5939 (part 1: table + VAPID generator) ·
  d4ff40a (part 2: endpoints) · 512b274 (part 3: SW + toggle) · 5117b33
  (part 4: test-send) · 284912a (fix: bullseye vendoring — http-ece is
  sdist-only) · 6e37b6c (PWA manifest + icons for iOS) · 1faa0c0 (fix: iOS
  redirect login) · f6fa8c8 (scope=all + real TTL) · 3918cc2 (recipient
  breakdown + labels + send log)

Decision flow: a5af66f (part 1: item_events + one-decision index + qb fields)
  · 6d2c206 (part 2: inbound pilot hooks + push fan-out) · 106ea29 (part 3:
  outbound trigger + resend + QB fast path) · 6fcb0bf (part 4: decision
  endpoint, DB-refereed + compensating) · a8426bf (part 5: decision card UI +
  board chips) · 71b8403 (part 6: multi-round Q&A, one decision per QUESTION)
  · a675f2d (part 7: matched-job fields hook-writable + card Job row) ·
  34fb15d (part 8: live-processing UX, quieter feed, Moraware double-check) ·
  954c99c (part 9: moraware link everywhere, invoice on card, classifier
  write-back)
n8n pilot: c781864 (CHECK-BOT clone generated + validated) · 12c7946
  (flag-off nodes ship disabled) · 982cadb (job fields + Drive all outcomes +
  owner-copy merge) · c167ed0 (sweep hooks: /list /notify, items.check_number)
  · 75306aa (PAYMENT-SWEEP clone) · 6139e7b (duplicate links + Drive
  empty-folder fix) · 8272791 (full-quality originals via /photo hook +
  camera retry) · a571714 (format_hint JSON fix) · 2fd9d34 (classifier: QB
  wins over memo hint) · 2e9fe04 (payment_method backfill) · 8a87ed8
  (payment date = receive day, Eastern) · be54ee6 (2s autofocus grace) ·
  52aab30 + eb93954 (strict one-push doctrine, both workflows)

Full list: `git log --oneline` — 84 commits.

## 17. Verification quick reference

```
docker compose up --build                       # 3 services healthy
curl.exe http://localhost:8000/api/health       # ok/ok — the only open endpoint
docker compose exec backend pytest              # 80 passed
docker compose exec backend python -m app.scripts.mirror_airtable --dry-run
https://localhost:5173                          # login → role-shaped app
https://192.168.1.50.nip.io:5173              # phone (EMG Wi-Fi: DNS 8.8.8.8)
# Stage 3 owner drill: plan §8 — Vince's amount edit must appear in Airtable,
# then the mirror must report that row UNCHANGED; broken write token must 502
# with zero local change; audit page shows everything, deleted rows included.

# PRODUCTION (§18):
curl.exe https://<app-name>.azurewebsites.net/api/health
#   → {"status":"ok","database":"ok"}
# gh run watch <id>                              # deploy pipeline green on push to main
# Prod mirror (from local container, DB pointed at Azure):
# docker compose exec -e DATABASE_URL="postgresql+psycopg://emg:<pwd>@emg-postgres\
#   .postgres.database.azure.com:5432/emg_console?sslmode=require" backend \
#   python -m app.scripts.mirror_airtable [--dry-run]

# PUSH (§19):
docker compose exec backend python -m app.scripts.generate_vapid_keys  # new keypair
# Human loop, on an iPhone: Safari → site → Share → Add to Home Screen → open
# the "E" icon → log in (page redirects to Google and back) → badge menu →
# Enable notifications → Allow. Admin then: "Test ALL team devices" → the
# result line must NAME every recipient and all named phones must buzz.
# Forensics: Azure Log stream shows "push test-send scope=… recipients=…".
```

---

## 18. Production deployment (Azure) — Deploy stage

**Live** at `https://<app-name>.azurewebsites.net`
(Azure issues a regionalized hostname with a unique suffix — NOT
`emg-console.azurewebsites.net`, which does not resolve). `/api/health` →
`{"status":"ok","database":"ok"}`. Prod DB populated 2026-07-12 via the mirror:
**180 payments (174 confirmed / 6 needs_job)**. Governing doc:
STAGE_DEPLOY_BUILD_PLAN.md. No feature changes rode along — the app deployed is
exactly Stage 3 + capture.

### Infrastructure (owner-provisioned in the portal, plan §3)
- **App Service Linux B1**, West US 3, **Python 3.12**, Code deploy. ONE Web App
  serves both tiers (single origin → CORS/cookies collapse to same-origin).
- **Azure Database for PostgreSQL Flexible Server**, Burstable **B1ms**, 32 GiB,
  PG 16, West US 3; public access + "allow Azure services" + owner IP. DB
  `emg_console`, user `emg`. Reachable from the office (firewall verified).
- Photos on App Service persistent storage **/home/data/uploads**
  (`WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`).

### Single-origin serving (Part 1 — app changes, commit c2e81fe)
- **main.py `_mount_frontend`**: when `FRONTEND_DIST` is set, mounts `/assets`
  (StaticFiles) + an SPA catch-all — any non-`/api` path → `index.html` (deep
  links like `/payments/audit` survive hard refresh); `api/*` miss stays JSON
  404; real dist-root files (favicon…) served with a path-traversal guard.
  Registered LAST so it never shadows an `/api` route. Empty `FRONTEND_DIST` in
  dev = no-op (Vite serves). **CORS is added only when `ENVIRONMENT != production`**
  (prod is same-origin — no cross-origin allowed at all).
- **config.py**: added `frontend_dist` and `upload_dir` (canonical `UPLOADS_DIR`,
  still accepts `UPLOAD_DIR`).
- **backend/startup.sh** = the prod launcher (Web App Startup Command:
  `bash startup.sh`). **Self-locating** — resolves `PYTHONPATH` and
  `FRONTEND_DIST` from its own directory, so it works whether App Service runs the
  files from `/home/site/wwwroot` or (with Oryx build on) an extracted `/tmp/<id>`
  dir, and a stale portal `FRONTEND_DIST` can't break boot. Runs
  `python -m alembic upgrade head` (self-migrates on every boot; idempotent), then
  `python -m gunicorn app.main:app` with UvicornWorker × 2, `--forwarded-allow-ips '*'`
  (TLS terminated upstream → `request.scheme=https`, Secure cookies intact),
  binds `$PORT`. Tools invoked as `python -m` so they resolve from PYTHONPATH
  without needing a `bin/` on PATH.

### CI/CD (Part 2 — `.github/workflows/deploy.yml`, commit 93e3080 + fixes)
Push to `main` (or manual `workflow_dispatch`):
- **Job `test`**: setup Python 3.12 → `pip install ./backend` → `pytest`. Dummy env
  vars satisfy `get_settings()` at import; suite is SQLite in-memory. **Gates the
  deploy** (deploy `needs: test`).
- **Job `deploy`**: a guard step skips all work unless the
  `AZURE_WEBAPP_PUBLISH_PROFILE` secret exists (keeps pre-provisioning pushes
  green). Then: build frontend (`npm ci && npm run build`, **`VITE_GOOGLE_CLIENT_ID`
  injected from a GitHub secret** — Vite inlines it at BUILD time); assemble the
  artifact (`app/` + `alembic/` + `alembic.ini` + `startup.sh` + `frontend_dist/`
  + generated `requirements.txt`); **vendor deps into `pydeps/`**; deploy via
  `azure/webapps-deploy@v3` (publish-profile secret); print the real URL to the
  job summary.

### Two hard-won lessons (why the deploy is shaped this way)
1. **Deps are vendored at BUILD time, not built on the server.** Azure's Oryx
   post-deploy build does NOT reliably run for zip/OneDeploy packages → the server
   had no venv → `alembic: command not found`. So the Action `pip install`s deps
   into `pydeps/` and ships them; `startup.sh` puts `pydeps/` on PYTHONPATH. No
   server-side build needed (SCM_DO_BUILD_DURING_DEPLOYMENT is irrelevant).
2. **Vendored wheels must match the server's glibc — build them in a bullseye
   container.** The GitHub runner's glibc is newer than the App Service Python
   image, so a plain install pulled wheels (e.g. `cryptography`'s Rust extension)
   needing `GLIBC_2.33` the server lacks → crash loop (`GLIBC_2.33 not found`,
   workers HaltServer). First fix pinned `--platform manylinux2014_x86_64
   --only-binary=:all:` — worked until `pywebpush` arrived: its dep `http-ece`
   ships sdist-only, and `--platform` forbids sdists (ResolutionImpossible).
   Current fix: the vendor step runs plain `pip install --target pydeps` INSIDE
   `python:3.12-slim-bullseye` (glibc 2.31 = the App Service image's OS) — old
   enough wheels AND sdists build. **Validate prod artifacts on bullseye, NOT
   bookworm** — bookworm's newer glibc masks this class of bug.
3. **(post-slice addendum) A stale Oryx package hijacks every boot.** Leftover
   `oryx-manifest.toml` + `output.tar.zst` in /home/site/wwwroot (from the era
   when Oryx build ran) made Azure extract and run that frozen snapshot on every
   restart — new deploys landed on disk but never ran (new routes 404,
   alembic_version stuck), surviving restarts. Diagnostic tells: SSH prompt shows
   `(antenv) …:/tmp/<id>`; health serves but a new endpoint 404s. Fix: delete
   those two files, restart. With them gone, deploys DO restart onto new code by
   themselves — no manual restart needed (verified repeatedly).

### Secrets & settings
- **GitHub Actions secrets**: `AZURE_WEBAPP_PUBLISH_PROFILE` (deploy target),
  `VITE_GOOGLE_CLIENT_ID` (build-time; a Google client id is public by design —
  it ships in the browser bundle regardless).
- **Web App app settings** (portal): the 6 required app vars — `DATABASE_URL`
  (`postgresql+psycopg://…?sslmode=require`), `SESSION_SECRET` (NEW for prod, not
  the dev value), `AIRTABLE_TOKEN`, `AIRTABLE_WRITE_TOKEN`, `AIRTABLE_BASE_ID`,
  `AIRTABLE_PAYMENTS_TABLE`, `GOOGLE_CLIENT_ID` — plus `ENVIRONMENT=production`,
  `UPLOADS_DIR=/home/data/uploads`, `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`;
  Startup Command `bash startup.sh`; Always On. `FRONTEND_DIST`/`PYTHONPATH` are
  NOT needed (startup.sh self-locates).

### Owner still to do (does NOT block the app being up)
- **Google OAuth**: add the live URL to Authorized JavaScript origins (§3.5) so
  sign-in completes (button renders without it, but Google rejects the flow).
  **DONE** — prod login verified on PC and both iPhones; the push slice also
  added the redirect URI (…/api/auth/google/redirect) to the same client.
- Phone camera test on cellular (§4.4) — still pending. Owner declined DB
  password rotation.

### Prod data load
Ran from the LOCAL backend container with `DATABASE_URL` overridden to the Azure
server (the office can reach it). Prod schema already existed (startup self-migrates
on boot). Upsert-by-airtable_id → 180 inserted into an empty prod DB, verified in
`review_items`.

---

## 19. Push mechanics slice — VERIFIED end-to-end (2026-07-15)

Governing doc: STAGE_PUSH_MECHANICS_SLICE.md. Goal was deliberately narrow:
prove a real push notification reaches a real phone in production BEFORE any
decision-card/n8n complexity. **Achieved**: both pilot accounts (alex@ admin,
owner-phone@ manager) receive lock-screen notifications on their iPhones,
including a cross-account "Test ALL team devices" broadcast clicked in the UI,
whose receipt line named every recipient with device counts.

What exists (details in §5 schema, §7 API, §8 frontend, §9 tests):
push_subscriptions (0007) · VAPID keypair (generator script; same pair in local
.env and Azure app settings; optional-at-boot) · /api/push/* endpoints ·
push-only service worker · PWA manifest + placeholder icons · NotificationsControl
toggle + admin test buttons · iOS redirect-mode Google login.

### iOS lessons (expensive to learn — do not relearn)
1. **Web Push on iPhone exists ONLY for Home-Screen-installed web apps**
   (iOS 16.4+). Browser tabs — Safari or Chrome — never get the API. The install
   requires a manifest with display:standalone + icons; without them "Add to Home
   Screen" makes a plain bookmark that can't push. Install steps must be done from
   the page viewed IN Safari/Chrome, not from a link's share menu.
2. **The GIS popup login white-screens on iPhone Safari** after credentials (and
   is unreliable inside installed web apps): login actually succeeded, the stuck
   blank tab was a dead popup. Fix = ux_mode 'redirect' on iOS (see §7).
3. **pywebpush defaults to ttl=0 = "deliver this instant or silently drop"** —
   pushes to locked phones vanished while the push service returned 2xx. Always
   send with a real TTL (we use 3600) + Urgency header.
4. **Installed PWAs and browsers keep running STALE pages after deploys** —
   force-close the app / hard-refresh (Ctrl+Shift+R) before concluding a feature
   "didn't deploy". Two days of "push is broken" decomposed into: the self-scoped
   test button behaving as designed + stale pages hiding the new broadcast button.
   Delivery itself never failed once. Hence the receipts: every test send now
   returns and displays exactly who was targeted.

### Verification evidence (owner-confirmed)
Self-test → own iPhone ✓ · server-side cross-account sends → gmail iPhone ✓
(three times) · UI "Test ALL team devices" → BOTH phones + PC ✓, receipt line
"Sent to: owner@example.com (2), owner-phone@example.com (3)" ✓ ·
dead-endpoint pruning in place · permission-denied and unsupported states show
plain guidance · 67 tests green · roster promotion (0006) live in prod.

### Explicitly NOT in this slice (next stage)
The owner's production requirement (recorded 2026-07-15): **any check/delivery
slip uploaded by a manager must notify all OTHER subscribed admins/managers**
("New check from {name}", deep link to the item; exclude the actor;
fire-and-forget so push failure never breaks an upload). Build = generalize
test-send's loop into a shared notify helper + hook POST /api/checks. Then the
full pilot: decision cards, n8n webhooks. Alternative channels for phones that
refuse the Home Screen install (WhatsApp-link via n8n, SMS/Twilio) were analyzed
and parked — revisit only if the install proves a rollout blocker.

## 20. Decision flow + n8n pilot — LIVE (2026-07-16 → 2026-07-21)

The stage that makes the console the bots' actual state store and the humans'
actual decision surface. Governing doc: STAGE_DECISION_FLOW_BUILD_PLAN.md
(§1–§7 built; §8 tests partially — the multi-round/hook suites exist, earlier
parts were live-drilled). 24 commits, a5af66f…eb93954.

### Console side (deployed to Azure, migrations 0008–0011)

- **item_events** (0008): one ordered stream per item — system lines, bot
  updates, bot questions (payload: candidates/resume_url/allowed_freeform/
  format_hint), human decisions, comments. No FK to review_items (survives
  deletion). **0009**: `answers_event_id` pairs each decision with the
  question it answers; the unique index moved from one-decision-per-ITEM to
  one-per-QUESTION — first-tap-wins is still DB-enforced per round, and the
  workflow may ask again ("typed name found nothing" → new question).
  **0010/0011**: console checks stamp txn_date (= receive day, Eastern) and
  payment_method='check' at upload; both backfilled.
- **Pilot hooks** (`/api/hooks/pilot/*`, X-Pilot-Secret, console-born rows
  only): POST items / update (payment fields + matched_job_id/name/
  moraware_url routed to the item) / question (one open per item,
  multi-round aware, optional push:false) / final (status + optional push) /
  GET find (keyed dedup) / GET list?days= (register snapshot for the sweep's
  diff brain) / POST notify (run-level pool push) / GET photo/{id} (the
  full-quality original for Drive archiving).
- **Decision endpoint** (`POST /api/review-items/{id}/decision`): exactly one
  of choice|text; races → 409 naming the winner; resume POST to the
  question's stored resume_url carries {secret, choice|text}; resume failure
  → compensating DELETE + 502, the card stays answerable.
- **Decision card** (`/payments/item/{id}`, admin+manager, 5s single-request
  poll): amount hero · facts grid incl. Invoice # (bot's invoice wins,
  manager's qb_invoice fallback) and a full-width Job row with Moraware
  link · purple "CHECK-BOT is working" strip (status=processing, set by the
  outbound trigger) · amber DECISION SLIP with ≥52px tap-twice candidate
  buttons, per-candidate "Double-check in Moraware" links, freeform with
  format_hint · race-loser notice · linkified bot text (in-app links for
  console URLs — duplicates link to the finished card) · activity feed
  collapsed to its spine with "Show all" · comment box. Board: amber
  "Needs decision" chip (thumb-sized) + pulsing purple "In progress" chip on
  cards and table rows. Submit lands ON the new card; the camera gets a 2s
  autofocus grace, is released before navigation, and retries twice on
  reopen (iOS holds the device).

### n8n side (script-generated — never hand-typed)

`n8n/build_pilot_workflow.py` and `n8n/build_sweep_workflow.py` read the
gitignored production exports, transplant the battle-tested nodes
byte-faithfully (OCR prompt, Parse Check Data, smart-search + AV/Vet/Rescue
ranking, Classify Payment, Build Work Items, tier builders), swap only the
input expressions, and validate: connection integrity, every node reference
resolves, zero forbidden strings (live keys, WhatsApp endpoints, Airtable
ids, production QB realm, production Drive folder), secret headers on every
hook call. `make_owner_copy.py` merges the owner's live "… latest.json"
exports (credentials + base-url/secret/key) onto fresh builds → import-ready
"ver N" files, so regeneration never costs a credential remap.

- **PILOT CONSOLE CHECK-BOT** (109 nodes, 58 transplanted): webhook trigger
  takes outbound.py's payload (secret verified first); synthesized caption
  "invoice NNNN" keeps the production OCR prompt/parser verbatim; Wait-node
  webhook resume + Parse Decision ({choice}/{text}); multi-round retry loop;
  dedup via GET /find with "already uploaded — see the finished card: <link>
  — record anyway?" semantics; cash branch first-class; QB verification on
  sandbox realm 9341457257917923; Drive archive of full-quality originals
  (via the photo hook) into a self-created year folder with clean
  Job_Check_Amount names.
- **PILOT CONSOLE PAYMENT-SWEEP** (63 nodes, 26 transplanted): hourly QB
  scan (3-day window) → Rows-As-Register adapter dresses console /list rows
  in Airtable shape so the diff brain runs unmodified → skip/backfill/
  supersede-split/record; tier1 (invoice on a Moraware job) records FULLY
  automatically; tier2/3 become question cards resuming to the workflow's
  second webhook (sweep-decision?item=id) — an hourly batch can't wait;
  split checks produce per-invoice rows + a digest; the WhatsApp dispatch
  queue is gone (the board is the queue).

### Owner rules encoded this stage

- **QuickBooks is the truth**: the classifier's QB verdict (incl. the
  75%-qty full-contract math → deposit/remainder/PIF/progress) beats the
  check's memo hint; a differing memo becomes a footnote.
- **Invoice-proven ⇒ zero questions**: caption/QB-invoice matched to a
  Moraware job → auto-record, card born resolved.
- **One payment = exactly one push**: first question OR final OR run-summary
  — never two; retries, post-question finals, and post-decision outcomes are
  silent (question+final hooks accept push:false); the sweep summary carries
  its news in the title and fires only for silent work.
- Moraware create-activity is the pilot's one LIVE write — it targets the
  fake TEST jobs by design.

### Verified (owner-confirmed on real phones + sandbox)

Check-bot end-to-end: submit → OCR → match → question push → cross-account
decision → job+invoice+type on card and table → Moraware note → Drive
archive. Multi-round ("Meridian Riverfront" → not found → ask again →
candidate tap). Race loser ("Nora got there first"). 502 compensation.
Duplicate detection with card link. Sweep: scan → tier1 auto-record →
backfill of check-bot rows → question cards → decision webhook. 80 tests
green; deploys test-gated on every push.

### Known follow-ups (this stage's backlog)

Hourly QB invoiceless-jobs sweep returns later as its own workflow (export
not yet shared). CC/text-payment entry deferred (no console entry surface).
Bridge endpoint payload shapes taken from the exports — confirm against
bridge source someday. Sweep cards are photo-less by nature. Rotate the
Anthropic key that sat in the old production export. Camera torch on iOS:
impossible for web apps (assessed); Android-only custom camera parked.
