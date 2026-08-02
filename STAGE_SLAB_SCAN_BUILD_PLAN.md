# Slab Scans chapter — build plan (2026-07-23)

Wade scans slabs daily and prints labels (QR + printed 7-digit slab ID).
He photographs the labels through the console, the numbers are captured
automatically, he picks the job by typing its name, and one note lands in
the Moraware **Job Details form → Notes** (bottom box) with the scan date
and every slab ID on its own line.

## Verified facts (tests done 2026-07-23)

- Label QR encodes EXACTLY the printed slab ID (e.g. `2287478`). No URL.
- zxing decodes real label photos reliably, including 2+ labels per photo
  (photo 4 of the test set: `1922383` + `1922394` from one frame).
- Only failure mode seen: QR physically cut off at the photo edge → the
  printed ID remains legible → Claude vision reads it (fallback), or the
  user types it.
- Bridge endpoint LIVE (bridge commit ed79d54):
  `POST /api/console/job-form-note` `{jobId, text}`, header `X-Console-Key`
  (same key as job-directory). Append-only to the Details form Notes field,
  `\n` renders as real line breaks. Multi-area jobs: writes first Details
  form. 400 on empty text/bad job, 401 on bad key.
- Console backend already has `bridge_base_url` + `bridge_console_key`
  settings (jobs sync uses them).

## Owner decisions

- Cards ONLY (no table). One card = one scanning session = ONE job,
  1+ slabs. No Drive, no QB, no push. Access: Alex's 2 emails first,
  Wade's login added after testing.
- Upload UX: "Upload slabs" → two choices: pick all photos from gallery,
  OR frame capture loop (shot → Next → shot → … → Finish).
- Note format:
  ```
  Slabs scanned Jul 23, 2026:
  2287478
  1945313
  ```

## Part 1 — backend

- Migration 0014: `scan_details` table — review_item_id FK,
  `slab_ids JSONB` (list of {id, source: 'qr'|'ocr'|'manual'}),
  `scanned_date date`, `job_form_note_id`/confirm bookkeeping.
- `item_type='slab_scan'` on review_items; statuses: `pending` →
  `confirmed`. (deliveryStatus() already maps these colors.)
- Router `/api/scans`:
  - `POST /api/scans` — create card (empty), returns id.
  - `POST /api/scans/{id}/photos` — upload a label photo (stored like
    check photos, for audit + OCR fallback).
  - `POST /api/scans/{id}/slabs` — replace/merge slab id list (client
    sends QR results + manual edits).
  - `POST /api/scans/{id}/ocr` — run Claude vision on photos that had no
    QR hit; returns candidate IDs. Needs `ANTHROPIC_API_KEY` env on Azure
    (new for the console backend). Model: claude sonnet, prompt: "return
    every slab ID visible (7-digit numbers next to 'ID:'), JSON array".
  - `POST /api/scans/{id}/assign` — set job (id, name, moraware url) from
    typeahead.
  - `POST /api/scans/{id}/confirm` — compose the note text server-side
    (Eastern date + one ID per line), POST to bridge job-form-note; on
    2xx → status confirmed + event logged; on failure → card stays
    pending with error event, Register re-enabled (compensating pattern
    like deliveries).
- Job search: reuse existing `/api/jobs/search`.
- Bridge jobId: jobs_directory rows carry the Moraware job id already
  (verify field name during build).

## Part 2 — frontend

- Menu entry "Slab scans"; routes `/scans`, `/scans/submit`,
  `/scans/item/:id`.
- Board: compact tiles like deliveries (status, date, job or "needs a
  job", N slabs). Swipe-delete for admins.
- Scanner page (`/scans/submit`):
  - chooser: **Scan with camera** / **Pick from gallery** (multi-select
    `<input multiple>`).
  - camera mode: live viewfinder + zxing-wasm decoding loop (~5 fps on
    downscaled frames). On decode: vibrate/beep, show chip with the
    number, dedupe within session, buttons **Next label** / **Finish**.
    Manual shutter fallback (photo kept for OCR fallback).
  - gallery mode: decode every picked image client-side; images with no
    QR are uploaded and sent through `/ocr`; leftovers become "couldn't
    read — type it" prompts.
  - zxing-wasm vendored via npm (no CDN).
- Card page (`/scans/item/:id`):
  - slab ID chips with ✕ remove + "add number" input (7-digit validate).
  - JobPicker typeahead (required, no Stock button).
  - **Register** → confirm endpoint → green "✓ Posted to Moraware —
    <job>" + link to the job; card shows `confirmed`.
  - Activity feed (reuse events, LinkifiedText).

## Part 3 — verification

- Backend tests: scan CRUD, confirm composes note correctly (date +
  newline-separated IDs), bridge failure keeps card pending, dedupe of
  repeated IDs.
- Browser E2E vs mock bridge (in-container, like mock SLABBOT): gallery
  path with the 6 real test photos; assert 5 IDs captured via QR and the
  cut-off ones recovered via OCR/manual.
- Prod smoke: real scan card against TEST job 5829, verify note lands in
  Details form Notes bottom box; then clear 5829's notes.

## Env / config

- Azure app settings: add `ANTHROPIC_API_KEY` (console backend OCR
  fallback). `BRIDGE_CONSOLE_KEY`, `bridge_base_url` already set.
- No n8n workflow, no VAPID/push changes.

## Later / out of scope now

- Wade's login + possibly a restricted role (only Slab scans section).
- Cross-card duplicate slab detection (same ID posted twice on different
  days) — flag on card, not blocking.
- Table mode if Alex ever wants it.
