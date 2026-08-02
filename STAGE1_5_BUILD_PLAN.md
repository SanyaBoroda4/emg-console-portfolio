# EMG Ops Console — Stage 1.5 build plan (UI structure + Payments v2)

> Audience: Claude Code. Read fully before coding. Frontend-only stage except one
> small, additive backend change (search/limit on the list endpoint). No schema
> changes, no writes to any external system. Stage 1 rules still apply
> (STAGE1_BUILD_PLAN.md sections 3 and 12), with one amendment: react-router-dom
> is now an approved dependency. No other new packages.

## Goal

1. A home page with large navigation tiles; the app no longer lands on Payments.
2. Payments page gets two view modes: the existing card list, and a new
   table mode with search and sorting.
3. Check photo thumbnails on cards and table rows, opening a lightbox on click.

## 1. Routing and layout

Add react-router-dom. Routes:

- `/` → HomePage
- `/payments` → PaymentsPage (the existing board, upgraded per section 3)
- Any unknown route → redirect to `/`

Create `src/components/Layout.tsx`: slim top bar (left: "EMG ops console" as a
link to `/`; right: nav links Payments · Follow-ups · Leads — the last two
render as disabled "soon" labels, not links). All pages render inside Layout.
The current nav in the header is replaced by this.

## 2. Home page (`src/pages/HomePage.tsx`)

A centered grid of large tappable tiles (2 columns desktop, 1 column mobile,
generous padding — these are finger targets for phones):

| tile            | state                                   |
|-----------------|------------------------------------------|
| Payments        | live → navigates to /payments. Shows a small live badge with the count of items whose status is `pending` (from the stats endpoint); hide badge when 0. |
| Slab deliveries | disabled, "coming soon" |
| Supply log      | disabled, "coming soon" |
| Follow-ups      | disabled, "coming soon" |
| Leads           | disabled, "coming soon" |

Disabled tiles: reduced opacity, not focusable, aria-disabled="true".
No marketing text, no dashboard widgets — this page is a switchboard.

## 3. Payments page v2 (`src/pages/PaymentsPage.tsx`)

Top of page keeps the stats cards (clicking a card still applies that status
filter — including needs_job; this remains the only entry point to needs_job).

Add a view-mode toggle (two-segment control: "Cards" / "Table"), persisted in
the URL as `?view=table` so a refresh keeps the mode. Default: cards.

### Cards mode (existing list, small changes)
- Filter tabs become exactly three: **All · Confirmed · Pending**. No
  needs_job tab (stats card covers it). "All" genuinely means all statuses.
- ORDERING (changed from Stage 1): cards are sorted by Payment Date
  ascending — the EARLIEST payment is at the top. Sort key is
  COALESCE(txn_date, date_received, created_at). This ordering comes from
  the backend (see "Data loading" below), not the client, so pagination
  pages stay consistent.
- Each card's photo placeholder becomes a real thumbnail (section 4).

### Table mode (new, `src/components/PaymentsTable.tsx`)
- Columns: Received (date_received, fallback created_at) · Amount · Payer
  (payer_name, fallback caption_name with a "(caption)" hint) · Method · Type ·
  Invoice · Check # · Job (matched_job_name as link to moraware_url, else "—") ·
  Status (same color badges) · Photo (thumbnail, clickable).
- A single search input above the table: filters rows where ANY displayed
  column contains the query, case-insensitive. Client-side filtering.
- Click a column header to sort by it; click again to reverse. DEFAULT sort:
  Payment Date (txn_date) ascending — the most recent payment is the LAST row,
  matching the owner's Airtable habit. Rows with no txn_date use
  date_received, then created_at, as the sort key (fallback chain). Amount
  sorts numerically, dates chronologically.
- Add a Payment Date column (txn_date) to the column list; it is the primary
  ordering column.
- Row density: compact (this mode exists to scan many rows).
- Visual design — this table should look polished, not utilitarian:
  sticky header row with a subtle background; zebra striping; row hover
  highlight; Amount right-aligned in tabular figures; Status as the existing
  color badges; PaymentType as small tinted chips with a consistent color per
  value (deposit / progress / remainder / PIF each get their own hue, PIF the
  "success" one); Job links styled as links. Colorful but disciplined: color
  only on badges/chips/links, neutral background elsewhere. No new libraries.
- Empty search result state: "No payments match '<query>'".
- Mobile: the table area scrolls horizontally (overflow-x-auto) — do not try
  to responsively collapse columns in this stage.

### Data loading for table mode
Table mode needs the full set, not a page. Extend the backend list endpoint
minimally: raise the `limit` validation ceiling to 1000 and have the frontend
request `limit=1000` in table mode. At the current ~200 rows this is instant;
ASSUMPTION stated for the owner: when the mirror grows past ~1000 rows we move
search server-side (a `?q=` parameter with ILIKE) — out of scope today.
Cards mode keeps its existing pagination ("Load more").

### Backend ordering change (applies to the list endpoint globally)
The list endpoint's ORDER BY changes from newest-created-first to
chronological ascending by payment date:
`ORDER BY COALESCE(payment_details.txn_date, payment_details.date_received::date,
review_items.created_at::date) ASC, review_items.created_at ASC`
(second key breaks ties stably). This requires the existing join to
payment_details in the query. Update the API tests accordingly: seeded rows
must come back oldest-payment-first, and a row with NULL txn_date must sort
by its date_received fallback.

## 4. Drive thumbnails + lightbox

`photo_drive_url` values are Google Drive links. Create
`src/lib/driveImage.ts` with:
- `extractDriveFileId(url)`: handles the two shapes that occur in this data —
  `.../uc?id=<ID>&export=download` and `.../file/d/<ID>/view...` — returns the
  ID or null.
- `driveThumbUrl(id, width)` → `https://drive.google.com/thumbnail?id=<ID>&sz=w<width>`

`src/components/CheckThumb.tsx`: renders an <img> (w200 for cards, w120 for
table rows) with loading="lazy" and alt text "check photo". onError → swap to
the gray photo-icon placeholder BUT keep it clickable. Click → open Lightbox.

`src/components/Lightbox.tsx`: full-screen fixed overlay (dark backdrop),
centered image at `sz=w1600`, close on: backdrop click, X button, Escape key.
While loading show a small spinner; on image error show "Preview unavailable"
plus an "Open in Google Drive" link (the original photo_drive_url,
target=_blank). Also always show a small "Open in Drive ↗" link under the
image. Accessibility: role="dialog", aria-modal, focus moves to the close
button on open and returns to the trigger on close. No new libraries — plain
React + Tailwind.

Note for the owner (include in PLAIN WORDS): thumbnails render only if the
Drive file's sharing allows the viewer; broken previews degrade to the icon
and the Drive link, and we treat widespread breakage as a known follow-up
(share settings or a backend image proxy), not a bug in this stage.

## 5. Out of scope

No auth, no uploads, no writes anywhere, no styling overhaul beyond what these
components need, no needs_job tab, no server-side search, no new dependencies
beyond react-router-dom.

## 6. Verification checklist

1. `docker compose up -d` → open the app root: home page with 5 tiles, only
   Payments active; pending badge shows the live count (compare with stats).
2. Payments tile → /payments; browser Back returns to home (real routes).
3. Toggle to Table: all rows present (count matches "all payments" stat);
   default order is Payment Date ascending with the newest payment as the
   LAST row; search for a known payer narrows rows across columns; header
   click re-sorts; amount column sorts numerically (13,425 above 3,750, not
   alphabetically); PaymentType chips show distinct colors per value.
4. `?view=table` in the URL survives refresh.
5. Cards mode: exactly three tabs (All/Confirmed/Pending); the EARLIEST
   payment date is at the top of the list and "Load more" continues forward
   in time; needs_job reachable only via its stats card; thumbnails visible
   where sharing permits.
6. Click thumbnail → lightbox; Esc closes; focus returns to the thumbnail;
   broken-image case shows the Drive link fallback.
7. Phone-width check (devtools responsive mode): home tiles stack; table
   scrolls horizontally; lightbox usable.
8. `docker compose exec backend pytest` still green; the limit=1000 request
   returns the full set (add/adjust one API test for the new ceiling).
