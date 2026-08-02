# EMG Ops Console — Push mechanics slice (prove the pipe works)

> Audience: Claude Code. Read CLAUDE.md + PROJECT_STATE.md first. This is
> deliberately NOT the full push pilot — it proves Web Push itself works, on
> real phones, against production, before any decision-card/n8n complexity is
> built. The full pilot (item feed, candidate buttons, n8n hooks) is a
> SEPARATE later stage.
>
> Approved new dependency: `pywebpush` (backend) only. Nothing else.

## 0. Roster fix (separate tiny migration, do first)

Flip owner-phone@example.com from role `yard` to `manager` — same idempotent
migration pattern as prior roster changes (e.g. 0004_add_oleksandr.py). This
is the second of the two pilot test accounts (alex@ is the first, already
admin).

## 1. Schema

New table `push_subscriptions`: id uuid PK · user_email text NOT NULL indexed ·
endpoint text UNIQUE · p256dh text NOT NULL · auth text NOT NULL ·
user_agent text NULL · created_at timestamptz. Subscribing again from the
same browser (same endpoint) upserts, does not duplicate.

## 2. Config + keys

New env vars (documented in .env.example, fail-fast if missing):
`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (mailto:
owner@example.com). One-shot generator script
`app/scripts/generate_vapid_keys.py` that prints a keypair to paste into
.env (local) and into the Web App's Environment variables (prod) — these
must be THE SAME keys in both places eventually, but for now dev and prod
can each have their own pair since this is a mechanics test, not shared data.

## 3. Backend endpoints

- `GET /api/push/vapid-public-key` — open to any authenticated user; returns
  `{key: VAPID_PUBLIC_KEY}` (public by design, same category as
  GOOGLE_CLIENT_ID — safe to expose to the browser).
- `POST /api/push/subscribe` — any authenticated payments-role user
  (admin/manager; yard excluded, matching their lack of payments access).
  Body = the browser's PushSubscription JSON (endpoint, keys.p256dh,
  keys.auth). Upsert by endpoint.
- `POST /api/push/unsubscribe` — body `{endpoint}`, deletes if present,
  204 either way (idempotent).
- `POST /api/push/test-send` — **admin only**. Sends a fixed test
  notification ("Test notification from EMG console — if you see this, push
  works!") to ALL of the CALLER's own subscriptions (not other users' — a
  human, self-service pipe check, not a broadcast tool). Use pywebpush with
  the VAPID keys. On a 404/410 response from the push service, delete that
  dead subscription (standard cleanup — expired subscriptions are normal).
  Return `{sent: N, pruned: N}`.

## 4. Frontend

- `frontend/public/sw.js`: minimal service worker —
  `push` event → `self.registration.showNotification(title, {body, data:{url}})`;
  `notificationclick` → focus an existing tab at that url or open one.
  No caching logic, no offline support — push delivery only, this stage.
- Register the service worker on app load (only for admin/manager sessions,
  matching who can subscribe).
- User badge menu: add "Enable notifications" (or "Notifications on ✓" once
  subscribed) — on click: request Notification permission → if granted,
  `pushManager.subscribe()` using the VAPID public key fetched from the API →
  POST the subscription. Show plain states for: not supported by this
  browser, permission denied, subscribed successfully. A small "Send test
  notification" button (admin only) appears once subscribed, calling
  test-send and showing its result count.

## 5. Tests

Subscribe upserts by endpoint (no duplicates on repeat); unsubscribe removes;
test-send only reachable by admin (403 for manager/yard); dead-subscription
(mocked 410) gets pruned on send. Existing 54 tests stay green.

## 6. Out of scope (this slice — comes in the full pilot stage later)

Item feed, decision cards, candidate buttons, n8n webhooks/hooks contract,
push fan-out tied to actual check submissions, comments. This slice ends at
"a human can trigger a real push notification to their own phone and see it
arrive."

## 7. Verification checklist (owner)

1. Migration applied locally AND in production (roster fix + new table) —
   confirm owner-phone@example.com shows role=manager in both DBs.
2. Generate VAPID keys, add to local .env AND the Azure Web App's
   Environment variables (can be different keys in each — this is a
   mechanics test). Restart both.
3. On your phone: visit the PRODUCTION url, log in as alex@, tap "Enable
   notifications," grant the browser permission prompt, confirm the badge
   shows "Notifications on."
4. Tap "Send test notification" → a real notification appears on the phone's
   lock screen within a few seconds.
5. Repeat steps 3–4 logged in as owner-phone@example.com on a second
   device/browser profile — confirms two independent people can each
   subscribe and receive.
6. Turn off notifications for the browser at the OS level, retry — confirm
   the app shows a "permission denied" state rather than failing silently.
7. pytest green; PROJECT_STATE.md regenerated noting this slice as done and
   the full pilot (candidate cards + n8n) as the next planned stage.
