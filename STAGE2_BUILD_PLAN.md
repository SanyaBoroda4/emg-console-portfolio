# EMG Ops Console — Stage 2 build plan (login + roles)

> Audience: Claude Code. Read fully before coding. Read PROJECT_STATE.md first —
> it is the current source of truth for what exists; this plan describes the delta.
> All prior constraints hold unless amended here. Approved new dependencies:
> backend — `google-auth` (verify Google ID tokens), `itsdangerous` (sign our own
> session cookie). Frontend — none (Google Identity Services loads via a <script>
> tag, no npm package). Nothing else without asking.

## 1. Goal

Nobody can use the console anonymously anymore. A person signs in with their
Google account; the backend checks their email against a fixed roster with a
role; the frontend shows only the tabs their role allows; the backend refuses
API calls their role doesn't allow, independent of what the frontend shows.

## 2. The roster (seed data, not a UI — see section 4)

| email | display name | role |
|---|---|---|
| owner@example.com | Alex Sorokin | admin |
| office@example.com | Nora Adams | admin |
| manager1@example.com | Nora Bennett | manager |
| manager2@example.com | Vince Cole | manager |
| yard1@example.com | Wade | yard |
| yard2@example.com | Sam | yard |

## 3. Role matrix (authoritative — enforce on BOTH frontend and backend)

| area | admin | manager | yard |
|---|---|---|---|
| Payments (view + submit check) | yes | yes | **no — hidden AND blocked** |
| Slab deliveries (tile only; page not built yet) | yes | yes | yes |
| Supply log (tile only; page not built yet) | yes | yes | yes |
| Leads (tile only; page not built yet) | yes | yes | no |
| Follow-ups (tile only; page not built yet) | yes | **no** | no |

Anyone not in the roster: authenticated by Google, but rejected by us —
"not authorized for this console" — no session issued.

## 4. Database (migration 0003)

New table `users`: `email` (text, PRIMARY KEY, lowercase-normalized on write),
`display_name` text, `role` text NOT NULL (one of admin/manager/yard — check
constraint, not a DB enum type, matching the project's "free text, checked in
code" philosophy elsewhere... EXCEPT here a CHECK constraint is appropriate
since this vocabulary is OURS, not an imported one — add
`CHECK (role IN ('admin','manager','yard'))`), `created_at` timestamptz default
now(). The SAME migration seeds the six rows from section 2 (idempotent
INSERT ... ON CONFLICT (email) DO NOTHING, so re-running upgrade is safe).
There is no UI to manage this table in this stage — the owner edits the roster
by adding a new migration or a direct SQL insert when someone joins/leaves.
State this limitation in the PLAIN WORDS.

## 5. Backend auth flow

### Config additions (.env / config.py)
`GOOGLE_CLIENT_ID` (from the owner's Google Cloud Console), `SESSION_SECRET`
(a long random string the owner generates once, same pattern as the Postgres
password — alphanumeric, 40+ chars, never reused elsewhere). Fail fast with a
clear message if either is missing.

### POST /api/auth/google
Body: `{credential: "<google id token>"}`. Verify it with `google.oauth2.id_token
.verify_oauth2_token(credential, requests.Request(), GOOGLE_CLIENT_ID)` — this
call itself checks the signature and audience; do not skip verification or trust
the token's claims unchecked. Extract the verified `email` (lowercase it).
Look up `users` by email:
- not found → 403 `{"error": "not_authorized", "message": "This Google account
  isn't set up for the EMG console. Contact Alex."}`. No cookie set.
- found → issue our own session token: `itsdangerous.URLSafeTimedSerializer
  (SESSION_SECRET).dumps({"email": ..., "role": ...})`, set as an **httpOnly,
  Secure, SameSite=Lax** cookie named `emg_session`, max_age 30 days. Return
  `{email, display_name, role}` in the body too (so the frontend doesn't need a
  second round trip).

### GET /api/auth/me
Reads the `emg_session` cookie, verifies+decodes it (itsdangerous raises on
tampering or expiry — catch and treat as logged out), re-fetches the user row
from `users` by email (so a role change on next deploy takes effect without
waiting for cookie expiry), returns `{email, display_name, role}` or 401
`{"error": "not_authenticated"}` if no valid cookie.

### POST /api/auth/logout
Clears the cookie (set it expired). Always 204, even if nothing was set.

### Authorization dependency
`get_current_user` (FastAPI dependency): decodes the cookie as above, 401 if
absent/invalid, re-fetches the user row, 403 if the email is no longer in
`users` (covers someone being removed from the roster), returns the user.

`require_role(*allowed_roles)`: a dependency factory wrapping
`get_current_user`; 403 `{"error": "forbidden"}` if the user's role isn't in
`allowed_roles`.

### Apply to existing endpoints (this is the part that actually matters)
- `GET /api/review-items`, `GET /api/review-items/stats`,
  `DELETE /api/review-items/{id}`, `POST /api/checks`,
  `GET /api/photos/{id}` → `Depends(require_role("admin", "manager"))`.
  Yard gets a clean 403 from every payments-related call, matching the fact
  they have no UI for it at all.
- `GET /api/health` stays open (no auth) — deployment tooling needs it
  reachable without a session.

### CORS / cookies note
The dev proxy already makes the browser same-origin with the API (see
PROJECT_STATE §4), so cookies set by the backend are delivered through the
proxy with no extra CORS-credentials configuration needed. Verify this
assumption in Part B's checklist rather than assuming it holds.

## 6. Frontend

### Google Identity Services (no npm package)
Add `<script src="https://accounts.google.com/gsi/client" async defer></script>`
to `index.html`. Read `GOOGLE_CLIENT_ID` into the frontend via a Vite env var
(`VITE_GOOGLE_CLIENT_ID` in `.env`, wired through `vite.config.ts` the same way
other env values are handled — do not hardcode the client ID in source).

### `/login` route (public, no auth required)
Simple centered card: "EMG ops console" + the Google button rendered via
`google.accounts.id.initialize({client_id, callback})` +
`google.accounts.id.renderButton(...)`. The callback receives a credential;
POST it to `/api/auth/google`. On success: store the returned user in
AuthContext (below), navigate to `/`. On 403 not_authorized: show the
"not set up for this console" message from the backend, stay on `/login`, do
NOT retry automatically. On network error: readable error + Retry.

### `AuthContext` (`src/lib/AuthContext.tsx`)
React context + provider wrapping the whole app. On mount, calls
`GET /api/auth/me`; while pending render nothing but a full-page loading
state (avoid a flash of the login page for an already-logged-in user).
Holds `{user, loading, logout}`. `logout()` calls `POST /api/auth/logout`,
clears context, navigates to `/login`.

### Route protection
Wrap all existing routes in a `RequireAuth` component: if `loading`, show
the loading state; if no `user`, redirect to `/login`; else render children.
Additionally wrap `/payments` and `/payments/submit` in a `RequireRole`
component (roles: admin, manager) — a yard user hitting the URL directly gets
redirected to `/` with a brief inline message ("Not available for your role"),
not a raw 403 page.

### Home page tiles — filter by role
- **admin**: all 5 tiles as they exist today.
- **manager**: Payments (live), Slab deliveries, Supply log, Leads — no
  Follow-ups tile at all (not shown disabled — simply absent).
- **yard**: Slab deliveries, Supply log only — Payments and Leads and
  Follow-ups tiles absent, not shown disabled.
(All non-Payments tiles remain "Coming soon" regardless of role — role only
controls WHETHER the tile appears, not whether the page behind it exists yet.)

### Layout nav — mirror the same filtering
Top bar nav links follow the same per-role list as the tiles. Add a small user
badge in the top-right (display_name initial or full name) with a dropdown/
menu containing "Sign out" → calls `logout()`.

## 7. Explicitly out of scope this stage

No UI to manage the `users` table (roster changes = a migration or direct SQL,
documented in PLAIN WORDS). No "forgot my role" self-service. No fine-grained
per-action permissions beyond the role matrix (e.g., manager vs admin both get
full payments access for now — differentiating "admin can also do X" waits
until there's an X to restrict). No change to how Payments itself behaves
(Stage 3 covers real confirm/correct actions) — this stage only gates ACCESS,
not payment WORKFLOW. No production secret rotation strategy (SESSION_SECRET
is a plain env var, fine for LAN/dev; revisit at Azure deploy).

## 8. Verification checklist

1. `docker compose up --build`. `docker compose exec backend alembic upgrade
   head` creates `users` with 6 seeded rows —
   `docker compose exec db psql -U emg -d emg_console -c "SELECT email, role FROM users;"`
   shows all six correctly.
2. Visit the app logged out (clear cookies / incognito) → redirected to
   `/login`, Google button renders, no console errors about the client ID.
3. Sign in as owner@example.com → lands on `/`, sees all 5 tiles, top bar
   shows all nav links, user badge shows a name, `GET /api/auth/me` (devtools
   Network tab) returns role "admin".
4. Sign in as manager1@example.com (manager) → 4 tiles (no Follow-ups),
   Payments works fully (list, table, submit check, delete).
5. Sign in as yard1@example.com (yard) → only Slab + Supply tiles;
   manually navigating to /payments redirects home with the role message;
   confirm via devtools that a direct `fetch('/api/review-items')` from the
   yard session returns 403, not data (this is the test that actually proves
   server-side enforcement, not just hidden buttons).
6. Try a Google account NOT in the roster (any personal Gmail) → backend
   returns not_authorized, login page shows the friendly message, no session
   cookie is set (check devtools Application tab).
7. Sign out → redirected to `/login`; `GET /api/auth/me` now 401.
8. Restart the backend container (`docker compose restart backend`) with an
   already-logged-in browser tab → session persists (cookie survives backend
   restarts — proves the session isn't held in server memory).
9. `docker compose exec backend pytest` — add tests for: unauthenticated
   request to a protected endpoint → 401; yard role hitting a payments
   endpoint → 403; unknown email verified by Google → 403 with no cookie;
   valid roster email → 200 + correct role in the session. All existing tests
   still pass (may need a test fixture that creates an authenticated client).
10. Test on phone over LAN — Google Sign-In must work from the LAN IP origin
    (this is why both known IPs were pre-registered in Google Cloud Console).
    If Google's button errors about an unregistered origin, the fix is adding
    that exact origin in Cloud Console, not a code change.
