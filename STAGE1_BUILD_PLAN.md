# EMG Ops Console — Stage 1 build plan

> Audience: Claude Code, implementing this inside the `emg-console` repo.
> Owner: Alex. Read this whole file before writing any code, then implement it exactly.
> Stage 1 is READ-ONLY. Nothing in this stage writes to Moraware, QuickBooks, Airtable, or WhatsApp.

---

## 1. Context (do not skip)

EMG is a countertop fabrication business. Production automations built in n8n (CHECK-BOT,
SLABBOT, SUPPLYBOT, PAYMENT-SWEEP and others) currently store their working state in
Airtable and make human-approval requests over WhatsApp. Moraware (job management, accessed
through a custom Azure "bridge" API) and QuickBooks remain the systems of record and are
NOT duplicated here.

This repo is the **EMG Ops Console**: a FastAPI + PostgreSQL + React web app that will
gradually replace Airtable/Sheets as the bots' state store and replace WhatsApp as the
human decision surface. It will run in shadow mode alongside the existing bots until cutover.

Stage 1 scope: scaffold the repo, stand up Postgres in Docker, mirror the Airtable
"Pending Checks" table into our own schema, and render a read-only Payments board.

## 2. Stage 1 goal / definition of done

With Docker Desktop running, the owner can:

1. `cp .env.example .env`, fill in one Airtable token, and run `docker compose up`.
2. Run the mirror script once and see real pending-check rows land in Postgres.
3. Open `http://localhost:5173`, see the Payments board with live counts and rows,
   filter by status, and see proper loading / empty / error states.
4. Run `docker compose exec backend pytest` and see all tests pass.

## 3. Tech stack (fixed — do not substitute)

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x (declarative, sync), Alembic,
  psycopg (binary), pydantic-settings, pyairtable, pytest.
- Frontend: React 18 + Vite + TypeScript, Tailwind CSS. No component library,
  no state-management library, no router in Stage 1 (single page).
- Infra: docker-compose with three services: `db` (postgres:16), `backend`, `frontend`.
- Do not add other dependencies without a comment in the PR/commit explaining why.

## 4. Repository layout

```
emg-console/
├── docker-compose.yml
├── .env.example              # every variable documented; real .env is gitignored
├── .gitignore                # must include: .env, __pycache__, node_modules, dist, .venv
├── README.md                 # how to run, one screen max
├── STAGE1_BUILD_PLAN.md      # this file
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   │   └── versions/         # one initial migration created in this stage
│   └── app/
│       ├── main.py           # FastAPI app factory, CORS, router registration
│       ├── config.py         # pydantic-settings Settings class; reads env vars
│       ├── db.py             # engine, SessionLocal, get_db dependency
│       ├── models.py         # SQLAlchemy models (schema below)
│       ├── schemas.py        # Pydantic response models
│       ├── routers/
│       │   ├── health.py     # GET /api/health
│       │   └── review_items.py
│       ├── scripts/
│       │   └── mirror_airtable.py
│       └── tests/
│           ├── conftest.py   # test DB session fixture (SQLite in-memory is acceptable)
│           ├── test_mirror.py
│           └── test_api.py
└── frontend/
    ├── Dockerfile
    ├── package.json
    ├── vite.config.ts        # dev server on 0.0.0.0:5173, /api proxied to backend
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── api.ts            # typed fetch helpers
        ├── types.ts          # ReviewItem, PaymentDetails, Stats interfaces
        └── components/
            ├── PaymentsBoard.tsx
            ├── PaymentCard.tsx
            ├── StatsRow.tsx
            └── StatusFilter.tsx
```

## 5. Environment variables (.env.example)

```
# --- Postgres (docker-compose wires these together) ---
POSTGRES_USER=emg
POSTGRES_PASSWORD=change-me-locally
POSTGRES_DB=emg_console
DATABASE_URL=postgresql+psycopg://emg:change-me-locally@db:5432/emg_console

# --- Airtable mirror (read-only Personal Access Token, scope: data.records:read) ---
AIRTABLE_TOKEN=
AIRTABLE_BASE_ID=appXXXXXXXXXXXXXX
AIRTABLE_PENDING_CHECKS_TABLE=tblXXXXXXXXXXXXXX

# --- App ---
ENVIRONMENT=local
```

Rules: no secret may appear anywhere in code or in docker-compose.yml; everything flows
from env. `config.py` must fail fast with a clear message if a required variable is missing.

## 6. Database schema

Create via ONE initial Alembic migration. Use UUID primary keys (server-side
`gen_random_uuid()`; enable the `pgcrypto` extension in the migration).

### Table `review_items` — one row per thing a human may need to review

| column            | type                        | why it exists |
|-------------------|-----------------------------|---------------|
| id                | uuid PK                     | Stable internal identity, safe to expose in URLs later. |
| item_type         | text NOT NULL               | `payment` now; `slab_delivery`, `supply_delivery` in Stage 4. Same boards, same table. |
| status            | text NOT NULL               | Verbatim from Airtable (`pending`, `confirmed`, `needs_job`, ...). Deliberately not an enum in the mirror era — imported vocabulary may grow; the mirror must never crash on a new value. |
| source            | text NOT NULL               | `airtable_mirror` for every Stage 1 row. Later: `whatsapp`, `console`, `sweep`. Tells us forever where a record originated. |
| airtable_id       | text UNIQUE                 | Airtable record id (`rec...`). The upsert key that makes the mirror idempotent. Nullable after cutover when rows are born in the console. |
| photo_drive_url   | text NULL                   | Link to the check photo already filed in Google Drive by CHECK-BOT. |
| matched_job_id    | text NULL                   | Moraware job id, when the bots matched one. Text, not FK — Moraware owns job identity, we only reference it. |
| matched_job_name  | text NULL                   | Denormalized display name so the board renders without calling the bridge. |
| moraware_url      | text NULL                   | Deep link into Moraware for one-click jump. |
| match_method      | text NULL                   | e.g. `invoice-recheck:...` — preserves the bots' audit trail of *how* the match was made. |
| raw               | jsonb NOT NULL              | The complete original Airtable `fields` object. Safety net: if the column mapping misses something, no data is lost and we can re-derive later. |
| created_at        | timestamptz NOT NULL default now() | |
| updated_at        | timestamptz NOT NULL default now() | Set by the app on every update (mirror refreshes it when a row changes). |

Indexes: `(item_type, status)` — the board's main query; unique index on `airtable_id`.

### Table `payment_details` — payment-specific fields, 1:1 with a review item

| column          | type            | why it exists |
|-----------------|-----------------|---------------|
| review_item_id  | uuid PK, FK → review_items.id ON DELETE CASCADE | 1:1 extension row; cascade so a deleted item never leaves an orphan. |
| amount          | numeric(12,2) NULL | `numeric`, never float — float arithmetic corrupts money (0.1 + 0.2 ≠ 0.3). Nullable: OCR sometimes fails to read an amount. |
| payment_method  | text NULL       | check / zelle / cash / cc — verbatim from Airtable. |
| payment_type    | text NULL       | deposit / final / etc., as the bots recorded it. |
| payer_name      | text NULL       | Name OCR'd from the check. |
| invoice_number  | text NULL       | Text, not integer — real invoice numbers grow prefixes and dashes. |
| txn_date        | date NULL       | Payment date from the document, distinct from created_at (when we ingested it). |

### Known Airtable field names (from the production n8n workflows)

`Status`, `InvoiceNumber`, `Amount`, `PaymentMethod`, `PaymentDate`, `PayerName`,
`PaymentType`, `JobName`, `JobId`, `MorawareURL`, `MatchMethod`, `GroupJID`, `DriveURL`.

The live table may contain more. REQUIRED: the mirror script's first action on each run
is to log the set of field names it actually received; map the known ones above,
put everything into `raw`, and print a warning listing any unmapped field names.

## 7. Mirror script — `backend/app/scripts/mirror_airtable.py`

Purpose: copy every row of Airtable "Pending Checks" into Postgres. Read-only toward
Airtable. Idempotent toward Postgres.

Behavior:
1. Fetch ALL records via pyairtable (it handles pagination and rate limits).
2. For each record, upsert by `airtable_id`:
   - not present → insert `review_items` (item_type=`payment`, source=`airtable_mirror`)
     + its `payment_details` row;
   - present → compare `raw` to the fresh fields; if changed, update both rows and
     `updated_at`; if identical, skip (log as unchanged).
3. Never delete. Rows that disappear from Airtable stay in Postgres (history is an asset).
4. Wrap the whole run in one transaction: any crash = zero partial writes (all-or-nothing).
5. Exit summary log: `fetched=N inserted=N updated=N unchanged=N unmapped_fields=[...]`.
6. Runnable as `docker compose exec backend python -m app.scripts.mirror_airtable`.
   A `--dry-run` flag prints the summary without committing.

Parsing rules: `Amount` → Decimal via `str()` round-trip, never float(); `PaymentDate` →
date with tolerant parsing; a record missing every payment field still gets a
`payment_details` row of NULLs (simplifies queries later).

## 8. Backend API

- `GET /api/health` → `{"status": "ok", "database": "ok"}`; database checked with a
  real `SELECT 1`, returns 503 with `"database": "unreachable"` on failure.
- `GET /api/review-items?item_type=payment&status=pending&limit=50&offset=0`
  → `{ "items": [...], "total": <int> }`, newest first. Each item embeds its
  `payment_details` (Pydantic response models in `schemas.py`; `orm_mode`/`from_attributes`).
  Validate: `limit` ≤ 200, `offset` ≥ 0 (FastAPI Query validators → automatic 422).
- `GET /api/review-items/stats?item_type=payment`
  → `{ "by_status": {"pending": 3, "needs_job": 1, ...}, "total": 12 }` via GROUP BY.
- CORS: allow `http://localhost:5173` only.
- Logging: uvicorn defaults plus one INFO line per mirror-run summary. No print().

## 9. Frontend — Payments board (single page)

Layout (top to bottom): header "EMG ops console" with a nav placeholder (Payments active;
Follow-ups, Leads shown disabled) → stats row of metric cards built from `/stats`
(clicking a card applies that status filter) → status filter (All + statuses present) →
list of payment cards.

Each card: photo thumbnail if `photo_drive_url` (fallback: gray photo icon), amount
formatted `$4,850.00` (Intl.NumberFormat; show "amount unreadable" if null), payer +
method + invoice line, matched-job line linking to `moraware_url` (or "No job match yet"),
status badge color-coded (confirmed=green, pending=amber, needs_job=red, others gray),
relative created time ("2h ago").

Required states: loading (skeleton cards, no spinner-only screens), empty ("No payments
yet — run the mirror script", show the exact command), error (readable message + Retry
button; must appear if the backend is down), success (the list). "Load more" button using
limit/offset (no infinite scroll).

Responsive: cards stack vertically on mobile, photo above text; tap targets ≥ 44px.
Accessibility: semantic HTML (`<main>`, `<nav>`, `<button>`), alt text on photos, visible
focus states, badge colors paired with text (never color alone).

Keep it clean and flat; no dark mode, no animations beyond hover states in Stage 1.

## 10. docker-compose.yml requirements

- `db`: postgres:16, env from `.env`, named volume `pgdata`, healthcheck `pg_isready`.
- `backend`: build `./backend`; command runs `alembic upgrade head` then
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`; source bind-mounted for
  hot reload; `depends_on: db: condition: service_healthy`; ports `8000:8000`.
- `frontend`: build `./frontend`, Vite dev server, ports `5173:5173`, source bind-mounted.
- Only configuration in compose comes from `.env`. No secrets inline.

## 11. Tests (pytest)

- `test_mirror.py`: inserting a fake Airtable record creates both rows with correct
  mapping; running the same input twice yields one row (idempotency); a changed field
  updates the row and `updated_at`; an unknown status string is stored, not rejected;
  `Amount: 4850.5` becomes `Decimal("4850.50")`. Structure the mirror so its
  transform/upsert logic is a pure function testable without Airtable network calls.
- `test_api.py`: `/api/health` 200; `/api/review-items` empty-DB shape; seeded rows
  filter by status; `limit=999` → 422.

## 12. Explicitly OUT of scope for Stage 1

No auth/login; no writes to Moraware/bridge/Airtable/QuickBooks/WhatsApp; no slab or
supply boards; no assistant/AI calls; no Azure deploy; no scheduler (mirror is manual).
If implementation reveals a needed deviation from this plan, stop and explain the
tradeoff instead of silently improvising.

## 13. Verification checklist (owner runs after implementation)

1. `docker compose up --build` → three services healthy, no error logs.
2. `curl http://localhost:8000/api/health` → `{"status":"ok","database":"ok"}`.
3. `docker compose exec backend python -m app.scripts.mirror_airtable --dry-run` →
   summary with fetched > 0, unmapped fields listed.
4. Same command without `--dry-run` → inserted > 0. Run again → inserted=0,
   unchanged=all (idempotency proven).
5. `http://localhost:5173` → stats match Airtable counts; filters work.
6. Stop backend (`docker compose stop backend`) → frontend shows the error state
   with Retry; start it again → Retry recovers.
7. `docker compose exec backend pytest` → all green.
8. `git log` shows small commits with clear messages; `.env` is not in the repo.
