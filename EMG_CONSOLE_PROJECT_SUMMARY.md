# EMG Ops Console — Comprehensive Project Summary

_A single-file, end-to-end account of what this project is, why it exists,
how it's built, every chapter shipped, the integrations, the decisions, and
the hard-won gotchas. Written to be self-contained source material (e.g. for
a future RAG system) — no external context required._

Last updated: 2026-07-26.

---

## 1. What this is, in one paragraph

EMG (a countertop/stone fabrication shop in Charleston, SC) ran its daily
operations on **Airtable + WhatsApp bots**. This project migrates that into a
purpose-built web app — the **EMG Ops Console** — that becomes the single
place where managers and yard staff act on payments, slab deliveries, and
slab scans. The console is a mobile-first PWA on Azure. Automation bots
(originally WhatsApp/n8n) were re-pointed so their **state store** is the
console database and their **human surface** is console cards instead of
WhatsApp chats. Battle-tested bot logic (OCR prompts, classifiers) was
preserved verbatim by generating n8n workflow clones with scripts, never by
hand-typing JSON.

**The owner (Alex Sorokin)** is a hands-on non-engineer who wants plain-words
explanations, exact commands with expected output, and to be shown work as
it happens (not built silently). He uses an iPhone in the field.

---

## 2. Business domain glossary

- **Slab**: a physical stone slab (granite, marble, quartzite, quartz,
  dolomite, soapstone, porcelain). Each has a **unique numeric ID** (5–9
  digits, e.g. `2287478`) printed on a label and encoded in a QR code. IDs
  never repeat.
- **Job / Lead**: a customer project in **Moraware** (the shop's job-management
  SaaS at `granite-marble-tops.moraware.net`). Jobs have **forms** ("Job
  Summary" and "Details"), each with fields incl. a multiline **Notes** box.
- **Supplier**: a stone wholesaler (AGM, Vitoria, Cambria, MSI, Cosentino,
  Daltile, Cosmos, CRS, TVS, Triton, Bottega, Encore, StoneBasyx, UMI, Easy
  Stones, ARC…). Many suppliers carry the same stone under slightly different
  names.
- **Moraware**: system of record for jobs. Reached READ-only via an MCP
  connector; WRITTEN to only through "the bridge" (below).
- **QuickBooks (QB)**: source of truth for payment classification. The
  classifier's QB verdict (incl. 75%-qty full-contract math →
  deposit/remainder/PIF/progress) beats a check-memo hint.
- **The bridge**: a separate small service (`bridge.emgcheckbot.us`,
  maintained by Alex with its own Claude Code) that wraps the Moraware .NET
  DLL. It exposes HTTP endpoints the console calls (job directory, add
  activity, job-form-note). Protected by header `X-Console-Key:
  emg-console-2026-vD7pN3qX8sWk`.

---

## 3. Tech stack & architecture

**Backend**: Python **FastAPI** + **SQLAlchemy 2** + **Alembic** migrations,
**PostgreSQL**. Runs in Docker. Served on **Azure App Service**.
**Frontend**: **React 18 + Vite + TypeScript + Tailwind**, PWA. Built to
`dist/` and served by FastAPI in production (same origin; CORS only needed in
dev for `https://localhost:5173`).
**Auth**: Google Sign-In → server mints a signed session cookie (`emg_session`).
Roles: `admin`, `manager`, `yard` (CHECK constraint). A role matrix
(`frontend/src/lib/roles.ts`) is mirrored/enforced independently on the
backend via `require_role(*roles)`.
**Deploy**: GitHub Actions on push to `main`. Backend tests run first (must be
green), then deploy to Azure. `startup.sh` self-migrates via `alembic upgrade
head` on boot. **Deploys DO restart the app.** Live URL:
`https://<app-name>.azurewebsites.net`
(note: the bare `emg-console.azurewebsites.net` does NOT resolve — the
region-suffixed host is the real one). Health: `GET /api/health` →
`{"status":"ok","database":"ok"}`.
**Automation**: n8n cloud (`alexemg.app.n8n.cloud`) hosts the bots. Also a
GitHub Actions cron robot for the materials catalog (no n8n needed there).

Data flow patterns:
- Console UI → FastAPI → Postgres.
- Bots: console → n8n webhook (outbound) and n8n → console pilot hooks
  (inbound, header `X-Pilot-Secret`).
- Moraware writes: console → bridge → Moraware. Moraware reads (for
  verification/tooling): MCP connector.

Core table: **`review_items`** — one row per thing a human may review, with
`item_type` in {`payment`, `slab_delivery`, `slab_scan`}, `status`, `source`,
photo path, matched-job fields, timestamps. Per-type detail tables hang off it
(`payment_details`, `delivery_details`, `scan_details`). **`item_events`** is a
shared, ordered activity feed (bot updates, questions, human decisions,
comments, system lines) — deliberately NO foreign key so history survives row
deletion.

---

## 4. Stage / chapter history (chronological)

### Stage 1 & 1.5 — Payments board (foundation)
- Mirror Airtable payments into `review_items` (`item_type='payment'`).
- Owner UX rules that recur everywhere: newest-first ordering, payer-first
  table, no stat cards, a "mark to QB" action, uniform single font color in
  table rows, zebra striping removed.
- Live statuses: `confirmed`, `needs_job`, etc. (status text is free-form
  from Airtable — the mirror must never crash on a new value).
- Windows/dev gotchas surfaced here: firewall/polling quirks; use `curl.exe`
  (not `curl`) on Windows.

### Stage 2 — Users & roles
- `users` table, Google auth, role matrix. Roles `admin`/`manager`/`yard`.
- Areas: `payments`, `slabs`, `supply`, `leads`, `followups` gated per role.

### Stage 3 — Audit log
- `audit_log` (who changed what, when), no FK to review_items.

### Deploy stage — Azure
- Live on Azure 2026-07-12, region-suffix URL.
- Gotchas: rollup lockfile platforms; glibc/bullseye wheels vendored;
  a stale Oryx package could hijack boot; **deploys restart the app**.

### Push mechanics slice — Web Push
- VAPID keys set in both envs; service worker (`/sw.js`) is push-only (no
  offline caching). Payload `{title, body, url}`.
- `push_subscriptions` (one row per browser, endpoint is the upsert key).
- Test-send limited to own devices; cross-account fan-out came later.
- iOS PWA specifics: home-screen caching, close/reopen twice to refresh.

### Decision flow stage — the shared activity feed & one-decision rule
- `item_events` feed; multi-round bot Q&A; **first-tap-wins enforced by the
  DATABASE**: a partial unique index allows at most one `kind='decision'` per
  question (`answers_event_id`), so a race → clean 409.
- Compensating rollback on resume failure (502).
- Pilot hooks contract (header `X-Pilot-Secret`):
  `/api/hooks/pilot/{items,update,question,final,find,list,notify,photo/{id}}`
  and `/api/hooks/slab/{update,final,find,list}`. `question`/`final` accept
  `push:false`.

### n8n bot ports (CHECK-BOT, PAYMENT-SWEEP, SLABBOT)
- Bots rebuilt as **script-generated** n8n JSON (never hand-typed):
  `build_pilot_workflow.py`, `build_sweep_workflow.py`,
  `build_slabbot_workflow.py`. Byte-faithful transplants of the original
  logic with expression swaps; a **validation pass** checks unique node ids,
  connection integrity, `$()` refs resolve, forbidden strings, secret
  headers, and — critically — **cross-execution reachability** (a node may
  only reference nodes reachable from the same trigger).
- `make_owner_copy.py` merges the owner's live credential attachments +
  filled config onto fresh builds → import-ready "ver N" files (gitignored;
  they contain live secrets — NEVER commit).
- Key n8n gotchas learned:
  - `{{ null }}` interpolates to empty text → breaks JSON bodies; emit literal
    `null`.
  - Wait-node webhook resume via `$execution.resumeUrl`.
  - `alwaysOutputData: true` needed so empty-folder Drive searches don't
    silently kill the chain.
  - Resource-locator values need `={{ }}` form.
  - **Root cause of cards stuck "filing"**: decision-webhook executions are a
    SEPARATE execution and referenced the ingest flow's config node (which
    never ran there) → expression dies → no Moraware notes/final. Fix:
    per-flow "Config D" nodes + the reachability validator.
- QB truth rules: invoice-proven ⇒ auto-record with a single push, card born
  resolved. Classifier's QB verdict beats the memo.

### Slab Deliveries chapter
- ONE table (materials embedded as JSONB on the delivery row, no second
  table), ONE card, ONE push per slip. Multi-material slips get a 2-button
  poll (one job / different jobs) then step-by-step per-material assignment on
  the same card. Dedup mandatory (Supplier+Doc# else Supplier+Total+SlabCount).
- Typeahead job picker (must TYPE to match; no empty-state suggestions),
  Stock always available.
- `delivery_details` (materials JSONB). `/api/deliveries`
  (upload/mode/assign/confirm→`N8N_SLAB_DECISION_URL`). Slab hooks
  (update/final/find/list). SLABBOT reads the slip verbatim, files the photo
  to a Google Drive tree (Year→Supplier), posts Moraware notes on confirm.
- **One push doctrine** (owner: "it sends tons of pushes"): server-side gate —
  slab update pushes ONLY on the FIRST transition into `needs_job`; reruns
  stay silent.
- Register button + `filing` status; table mode = max detail; card mode quiet.

### Deliveries board polish (owner UX pass)
- Cards → compact square-ish tiles (2/3/4/5 cols) with the delivery date;
  Cards|Table toggle at the LEFT edge; swipe-left-to-delete on cards, trash in
  table; **dark mode killed app-wide** (`darkMode:'class'` with no `.dark`
  ever set → every `dark:` variant inert; the console is always the light
  theme, navy brand bar stays navy). Bot messages render console-card URLs as
  in-app "open that card →" links.

### Jobs directory (typeahead backbone)
- Bridge `GET /api/console/job-directory` (X-Console-Key) → console
  `jobs_directory` table, synced every ~10 min by a daemon thread (disabled
  without `BRIDGE_CONSOLE_KEY`). `GET /api/jobs/search` ranks locally in
  Postgres (~7–11 ms on ~3.5k jobs; word-prefix ranking).
- Later: search sorts **newest-first** (recency primary; match quality breaks
  ties) and **hides Done/Cancelled** jobs (needs the bridge to send a
  `status` field per job; console side ready via `jobs_directory.status`).

---

## 5. Slab Scans chapter (the largest, most recent build)

**Goal**: Wade (yard staff) scans printed slab labels daily; the numbers
are captured, he picks the job, and ONE appended note lands in the Moraware
**Job Summary** form's Notes with the scan date and every slab ID + material
on its own line. One card = one scanning session = one job, 1+ slabs.

**Key architectural insight — NO n8n for this chapter.** QR decoding happens
on the phone; the OCR fallback is a direct Anthropic (Claude) call from the
console backend; Moraware writes go through the bridge. Fewer moving parts
than deliveries.

### Data & endpoints
- Migration `scan_details` (slab_ids JSONB, scanned_date). Slab item shape:
  `{id, source: 'qr'|'ocr'|'manual', material}`.
- `/api/scans`: `POST` create (dedupes ids; **rejects any id already on
  another card**), `PUT /{id}/slabs` (same cross-card guard), `POST
  /{id}/assign` job, `POST /{id}/confirm` (composes note, posts to bridge
  `job-form-note` with `form:"summary"`, requires job + a material on every
  slab; failure keeps card pending — compensating), `POST /ocr` (Claude vision
  reads printed IDs from photos whose QR failed; needs `ANTHROPIC_API_KEY`),
  `GET /list` + `GET /{id}/card` (yard-safe), `GET /used-ids` (global dedup
  preload).
- Note format posted to Moraware:
  ```
  Slabs scanned Jul 26, 2026:
  Taj Mahal — 2287478
  Namib White — 1956658
  ```

### QR / scanning (verified on real label photos)
- Label QR encodes EXACTLY the printed slab ID (no URL). Labels can carry TWO
  QRs (big + small, same ID); a single photo can hold multiple labels.
- Frontend decode uses the platform's native **BarcodeDetector** first (iOS
  Vision engine, same as the Camera app), falling back to **zxing-wasm**
  (self-hosted `.wasm`, no CDN). Multi-scale passes. Live camera scans full
  frame at 1440px + a 70% center crop at native res for far labels.
- **jsqr was tested and rejected** (decoded only 1/6 real photos).
- Only failures were photos where the QR was physically cut off at the edge →
  the printed ID is still legible → Claude OCR fallback, else manual entry.

### Scanner UX (ScanSubmitPage — full-screen, portaled to `<body>`)
- Two entry paths: **Scan with camera** (live viewfinder, neon corner
  brackets + sweeping scan line) and **Pick from gallery** (multi-select).
- iOS gotcha: the file picker's "Photo Library / Take Photo / Choose File"
  action sheet **cannot** be bypassed by any web app (Apple privacy gate);
  the camera-scan flow is the popup-free path. `accept="image/*"` gets closest
  (jumps toward Photos). Picked HEIC photos are converted to JPEG on-device
  (`toJpegSafe`) so the backend/Claude can read them.
- On a capture: three-note chime + a FULLSCREEN green "Slab registered <id>"
  panel that freezes scanning until the user taps **Scan next** / **Finish**
  (owner: no silent rolling capture). iOS has NO `navigator.vibrate`, so
  feedback is Web Audio (AudioContext primed on the Scan tap; muted by the
  ring/silent switch). Optional inline material pick right on the panel.
- **Global dedup**: scanner preloads `used-ids` and blocks a repeat scan
  instantly with an amber "⚠ Already scanned" panel + a distinct denied buzz;
  backend is the hard backstop (409).

### Card (ScanCardPage)
- Slab list; unassigned slabs **pre-ticked on first load** (a gallery batch is
  usually one material → skip the ticking step). Tick + MaterialPicker only
  appears while some slab still needs a material; already-named slabs have no
  checkbox and are skipped by "tick all" (can't be bulk-overwritten); tap a
  material to change it. Job typeahead → **Register** → celebration → green
  receipt with slab list + job hyperlink.
- **Celebration on confirm**: plays the owner's own video
  (`/public/goodjob.mp4`, transcoded 8 MB→690 KB with ffmpeg, bundled in the
  app — never fetched from Drive at runtime), muted, 2× speed (~4 s), slow
  soft fade in/out, confetti, tap-to-skip, portaled.

### Scanner-only "yard" role (for Wade)
- `yard` scoped to Slab Scans ONLY. Scans router + job/material search opened
  to yard; new yard-safe `GET /api/scans/list` and `/{id}/card` so scanner
  staff never touch the manager-only review-items endpoints (payments stay
  invisible/blocked). Frontend `SLAB_SCAN_ROLES` gates `/scans` routes; menu +
  home tile show Slab Scans to yard, deliveries hidden.

### Moraware note target — Job Summary form (final requirement)
- Job 5829 (TEST) has exactly 2 forms, each with a MultilineText **Notes**
  field: **"Job Summary"** (top) and **"Details"**. Owner wants slab notes in
  **Job Summary**.
- Console sends `{"jobId","text","form":"summary"}`. The bridge was updated
  (bridge commit `56fb9ca`) to honor an optional `form` field: `"summary"` →
  the form whose template name contains "Summary", else/missing → "Details"
  (default unchanged; append-only via `UpdateJobForm`). **Verified on job
  5829**: the append landed in Job Summary Notes; Details untouched.

---

## 6. Materials catalog (stone-name typeahead & self-updating database)

**Why**: Wade must tag each scanned slab with a material name, chosen fast
from a typeahead — not typed in full.

**Where stored**: console Postgres table **`materials_catalog`**
(`name_key = lower(name)` primary key → identical names dedupe automatically),
plus `supplier`, `source`, `last_seen`. Search endpoint
`/api/materials/search` (jobs-style word-prefix ranking, ~ms). Bulk intake
door `POST /api/materials/upsert` (header `X-Pilot-Secret`).

**Self-feeding from operations** (zero effort): every delivery slip SLABBOT
reads and every scan Wade registers upserts its material names.

**Owner display rules**:
- **No supplier shown** — many suppliers carry the same stone; Wade files
  the material name only.
- **Collapse variants to a base name** — computed at query time (no data
  lost, reversible). Rules: always strip trailing size (`3CM`, `2CM`) and
  format codes (`FF`, `DF`, `LF`, `TEK`…); strip trailing finish/grade/category
  words (`Polished`, `Honed`, `Leather`, `Extra`, `Premium`, `Quartzite`,
  `Marble`…) but **never reduce a name to a bare color** — so "Super White"
  stays "Super White" and "Blue Onyx"/"Green Onyx" stay whole. So
  "Taj Mahal 3CM" / "Taj Mahal Quartzite" / "Taj Mahal Extra Honed FF 3CM" all
  collapse to one **"Taj Mahal"** (engineered-quartz lines like "Genesis Taj
  Mahal" stay distinct on purpose).

**Automated weekly/daily refresh** — a **GitHub Actions cron robot**
(`scripts/materials_refresh.py` + `.github/workflows/materials-refresh.yml`),
**not n8n**:
- Daily 09:05 UTC: rescan the **Airtable** materials table
  (base `appXXXXXXXXXXXXXX`, table `tblIgskDn26ykBEok`, "Material"+"Supplier"
  columns; the existing `AIRTABLE_TOKEN` reads it).
- Monday 10:05 UTC (+ manual dispatch): full sweep of ~25 supplier pages,
  three strategies:
  - **StoneProfits** (AGM, Vitoria, Easy Stones): these inventory sites load
    their ENTIRE inventory in ONE JSON call
    (`…getInventoryGallery…` → `items[].ItemName`); the "77 pages" is
    display-only pagination. Captured with a **headed** Chromium under
    **xvfb** (headless-shell doesn't fire the SPA's inventory call — the site
    detects it).
  - **Shopify** (`/collections/x/products.json`) — e.g. Cambria.
  - **browser+ai**: render in Chromium, scroll / click "load more" to the
    bottom, then **Claude Haiku extracts the stone names** from page text —
    survives redesigns.
- Idempotent: catalog dedupes on lowercase name; per-site failures never kill
  the run. Secrets: `PILOT_HOOK_SECRET`, `AIRTABLE_TOKEN`, `ANTHROPIC_API_KEY`.
- Seeded by hand initially: 101 cleaned in-stock names + 1,394 AGM names +
  Vitoria 327 + Airtable ~75; first weekly sweep added Encore 638, Bottega
  208, ARC 202, Cosentino 189, TVS, MSI, Cosmos (×6), CRS (×5), UMI, Daltile,
  StoneBasyx — several thousand names total.

**What is StoneProfits (plain words)**: inventory-management software that many
stone suppliers rent to run their websites — "Shopify for slab yards." Because
they share the software, all their sites expose inventory the same way, so
cracking one cracked all three.

---

## 7. Integrations & external systems (quick reference)

| System | Direction | How | Auth |
|---|---|---|---|
| Airtable | read | REST API (payments mirror, materials scan) | PAT `AIRTABLE_TOKEN` |
| QuickBooks | read (via n8n) | classifier truth for payments | QB OAuth in n8n |
| Google Drive | read/write (via n8n & MCP) | slip photo archive tree | n8n creds / MCP |
| Moraware | read | MCP connector (`job_details`, etc.) | claude.ai MCP |
| Moraware | write | **the bridge** → .NET DLL `UpdateJobForm` | `X-Console-Key` |
| n8n cloud | both | webhooks in/out | `X-Pilot-Secret` |
| Web Push | out | VAPID + service worker | per-browser subscription |
| Anthropic API | out | OCR fallback + materials AI extraction | `ANTHROPIC_API_KEY` |

Bridge endpoints used: `GET /api/console/job-directory`,
`POST /api/console/job-form-note {jobId,text,form?}`,
`POST /api/slabbot/add-activity {jobId,note}`.

---

## 8. Cross-cutting engineering patterns & decisions

- **One-push doctrine**: any single real-world event (a payment, a delivery, a
  scan) triggers at most ONE push; reruns/retries stay silent; run summaries
  carry news in the title. Enforced server-side by "first transition" gates.
- **Compensating transactions**: if a downstream write (bridge/n8n) fails on
  confirm, the card stays in its pre-confirm state with an error event and the
  action button re-enabled — nothing is half-committed.
- **Database-enforced invariants** over app logic: first-tap-wins (partial
  unique index), global slab-ID uniqueness, dedupe keys.
- **Self-hosted assets, no CDN**: the PWA must not depend on a CDN (CSP /
  offline). wasm, fonts, the celebration video are all bundled.
- **Auto-updating PWA**: iOS keeps the home-screen app alive across deploys;
  a `useAutoUpdate` hook compares the running bundle hash to a fresh
  `index.html` on foreground (and every 10 min) and reloads once when they
  differ — ending the "force-close twice" ritual.
- **App-wide page transitions**: `PageTransition` re-keys the router Outlet on
  path change (glide-in). Keyframes settle at `transform:none` so they never
  leave a lingering stacking context that would trap `position:fixed`
  overlays; full-screen capture pages portal to `<body>` to be safe.
- **iOS field realities**: no web vibration; ring/silent switch mutes web
  audio; input font < 16px triggers focus-zoom (fixed via viewport
  `maximum-scale=1` + 16px inputs); slow camera hardware release between
  captures (retry ~5.5 s, drop to bare constraints, don't give up at 1.6 s);
  the file-picker action sheet can't be bypassed.
- **Plain-words + show-the-work** communication style for the owner: exact
  commands, expected output, narrate builds/deploys, never build silently.

---

## 9. Repo map (where things live)

```
backend/app/
  models.py            # SQLAlchemy: ReviewItem, ItemEvent, users, audit,
                       #   push_subscriptions, JobDirectory, DeliveryDetails,
                       #   ScanDetails, MaterialCatalog
  config.py            # Settings (env): DB, bridge, n8n, anthropic, pilot secret
  auth.py              # Google auth, session cookie, require_role()
  routers/
    review_items.py    # payments board list (admin/manager)
    checks.py          # check submit / QB fast-path
    decisions.py       # item events feed + decision endpoint (first-tap-wins)
    hooks.py           # pilot hooks (X-Pilot-Secret)
    push.py            # web push subscribe / send
    jobs.py            # /api/jobs/search (typeahead; newest-first; hide Done)
    deliveries.py      # slab delivery upload/mode/assign/confirm
    slab_hooks.py      # /api/hooks/slab/* ; feeds materials catalog
    scans.py           # slab scans: create/slabs/assign/confirm/ocr/list/card/used-ids
    materials.py       # catalog search + upsert + base_name() collapse
  jobs_sync.py         # daemon: pull bridge job-directory every ~10 min
  tests/               # pytest suite (105+ tests, run in Docker on deploy)
alembic/versions/      # 0001..0016 (0014 scan_details, 0015 jobs status,
                       #   0016 materials_catalog)
frontend/src/
  pages/               # Home, Login, Payments, Deliveries(+card+submit),
                       #   Scans(+card+submit), SubmitCheck (shared camera),
                       #   Audit, ItemCard
  components/          # JobPicker, MaterialPicker, Celebration, PageTransition,
                       #   LinkifiedText, Layout, EdgeSwipeBack, DeliveriesTable…
  lib/                 # roles, format, qr (BarcodeDetector+zxing), image
                       #   (HEIC→JPEG), useAutoUpdate, push
  index.css            # Tailwind + animation keyframes
  public/goodjob.mp4   # bundled celebration video (690 KB)
scripts/materials_refresh.py         # cron robot: Airtable + supplier sweep
.github/workflows/
  deploy.yml                         # test-gated Azure deploy on push to main
  materials-refresh.yml              # daily/weekly catalog refresh
n8n/ (gitignored "ver N" files hold live secrets)
  build_pilot_workflow.py, build_sweep_workflow.py, build_slabbot_workflow.py
  make_owner_copy.py
Stage docs: STAGE1_BUILD_PLAN.md … STAGE_SLAB_SCAN_BUILD_PLAN.md, PROJECT_STATE.md
```

---

## 10. Status & open items (as of 2026-07-26)

**Shipped & live**: payments board, users/roles, audit, Azure deploy, web
push, decision flow, three n8n bot ports, slab deliveries + polish, jobs
typeahead, the full **Slab Scans** chapter (QR/gallery/OCR capture, materials
with base-name collapse + self-updating catalog, global dedup, yard-only role,
Job-Summary note target, celebration video). 105+ backend tests green.

**Open (owner actions)**:
- Real smoke test on TEST job 5829 (scan → material → Register → verify slab
  numbers appear in the **Job Summary** Notes box), then clear the leftover
  test lines in 5829's Details + Job Summary Notes.
- Provide **Wade's email** to activate his `yard` scanner-only login.
- (Nice-to-have) A couple of StoneProfits sites (Vitoria/Easy Stones) still
  need a location-selection click for the automated weekly refresh; their data
  is already seeded so nothing is missing.

**Deferred/backlog**: hourly QB invoiceless-jobs sweep; CC/text payment entry;
rotate the old Anthropic key out of the earliest n8n export.

---

## 11. Reusable lessons (portable to future projects, incl. a RAG build)

1. **Put the fast-read data in your own store.** Typeaheads (jobs, materials)
   feel instant because they query a local Postgres table synced from the slow
   source — never the slow source live. (For RAG: mirror/embed into your own
   vector store; don't call the source per query.)
2. **Idempotent, self-healing ingestion.** Dedupe on a normalized key; make
   every writer safe to re-run; let per-item failures skip, not abort the run.
3. **Normalize at query time when the transform is lossy or evolving.** The
   material base-name collapse is computed on read, so raw data is preserved
   and the rules can change without a migration. (For RAG: keep raw chunks;
   apply cleaning/aliasing in the retrieval layer.)
4. **AI where structure is unreliable, rules where it's stable.** Supplier
   sites → AI extraction (survives redesigns); label QR → deterministic decode
   with AI only as fallback. Right tool per surface.
5. **Enforce invariants in the database**, not just the UI (uniqueness,
   one-decision, one-push).
6. **Verify against reality, then narrate it.** Every feature was checked in a
   real browser / against the live Moraware job before claiming done, and the
   evidence was shown to the owner. (For RAG: evaluate retrieval on real
   queries; show sources.)
7. **Respect the platform's hard limits** (iOS action sheet, no web vibration,
   autoplay rules) instead of fighting them — design the flow around them.
8. **Self-updating clients** beat asking users to refresh.
```
```
