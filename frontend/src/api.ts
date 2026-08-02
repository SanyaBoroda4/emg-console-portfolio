// Typed fetch helpers. All requests go to /api, which the Vite dev server
// proxies to the backend container.

import type { AuditList, AuthUser, ItemCard, JobHit, ReviewItem, ReviewItemList, ScanSlab, Stats } from './types'

/** Error carrying the HTTP status and the backend's structured body, so
 * the UI can special-case 409 (stale) and 502 (Airtable). */
export class ApiError extends Error {
  status: number
  body: Record<string, unknown> | null

  constructor(status: number, body: Record<string, unknown> | null, message: string) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    throw new Error(`The server responded with ${res.status} ${res.statusText}.`)
  }
  return res.json() as Promise<T>
}

export function fetchReviewItems(params: {
  status?: string
  limit?: number
  offset?: number
}): Promise<ReviewItemList> {
  const query = new URLSearchParams({ item_type: 'payment' })
  if (params.status) query.set('status', params.status)
  query.set('limit', String(params.limit ?? 50))
  query.set('offset', String(params.offset ?? 0))
  return getJson<ReviewItemList>(`/api/review-items?${query.toString()}`)
}

export function fetchStats(): Promise<Stats> {
  return getJson<Stats>('/api/review-items/stats?item_type=payment')
}

/** Exchange a Google credential for our session cookie. Throws with the
 * backend's message ("not set up for this console", etc.) on failure. */
export async function loginWithGoogle(credential: string): Promise<AuthUser> {
  const res = await fetch('/api/auth/google', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ credential }),
  })
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const message =
      body && typeof body === 'object' && 'message' in body
        ? String((body as { message: unknown }).message)
        : `Sign-in failed (${res.status}).`
    throw new Error(message)
  }
  return body as AuthUser
}

/** Who is logged in, per the session cookie. Null when not authenticated. */
export async function fetchMe(): Promise<AuthUser | null> {
  const res = await fetch('/api/auth/me')
  if (!res.ok) return null
  return (await res.json()) as AuthUser
}

export async function logoutRequest(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {
    // Clearing the local state is what matters; the cookie dies on expiry.
  })
}

/** Stage 3 edit path. `expected` carries what the client believed the old
 * values were — the server 409s if someone changed them in the meantime. */
export async function patchReviewItem(
  id: string,
  changes: Record<string, string | null>,
  expected: Record<string, string | null>,
): Promise<ReviewItem> {
  const res = await fetch(`/api/review-items/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ changes, expected }),
  })
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const record = body && typeof body === 'object' ? (body as Record<string, unknown>) : null
    const message =
      record && typeof record.message === 'string'
        ? record.message
        : `Edit failed (${res.status}).`
    throw new ApiError(res.status, record, message)
  }
  return body as ReviewItem
}

/** The ledger — admin sessions only (managers get 403). */
export function fetchAudit(params: {
  reviewItemId?: string
  limit?: number
  offset?: number
}): Promise<AuditList> {
  const query = new URLSearchParams()
  if (params.reviewItemId) query.set('review_item_id', params.reviewItemId)
  query.set('limit', String(params.limit ?? 50))
  query.set('offset', String(params.offset ?? 0))
  return getJson<AuditList>(`/api/audit?${query.toString()}`)
}

export async function deleteReviewItem(id: string): Promise<void> {
  const res = await fetch(`/api/review-items/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    throw new Error(`Delete failed: the server responded with ${res.status}.`)
  }
}

// --- decision flow ---

/** One request refreshes the whole card: the item plus its ordered feed. */
export function fetchItemCard(id: string): Promise<ItemCard> {
  return getJson<ItemCard>(`/api/review-items/${id}/events`)
}

/** Item ids whose latest bot question is still unanswered (board chips). */
export function fetchNeedsDecision(): Promise<{ ids: string[] }> {
  return getJson<{ ids: string[] }>('/api/review-items/needs-decision')
}

/** Answer the bot's question. Throws ApiError: 409 = someone else already
 * decided (body names them); 502 = workflow unreachable, nothing recorded. */
export async function sendDecision(
  id: string,
  payload: { choice: { label: string; job_id: string | null } } | { text: string },
): Promise<void> {
  const res = await fetch(`/api/review-items/${id}/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null)
    const record = body && typeof body === 'object' ? (body as Record<string, unknown>) : null
    const message =
      record && typeof record.message === 'string'
        ? record.message
        : `Could not send the answer (${res.status}).`
    throw new ApiError(res.status, record, message)
  }
}

export async function sendComment(id: string, body: string): Promise<void> {
  const res = await fetch(`/api/review-items/${id}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  })
  if (!res.ok) throw new Error(`Could not add the comment (${res.status}).`)
}

// --- Web Push (push mechanics slice) ---

/** The browser's applicationServerKey (VAPID public key). Empty string when
 * the server has push disabled. */
export function fetchVapidPublicKey(): Promise<{ key: string }> {
  return getJson<{ key: string }>('/api/push/vapid-public-key')
}

/** Register this browser's PushSubscription (from subscription.toJSON()). */
export async function subscribePush(subscription: unknown): Promise<void> {
  const res = await fetch('/api/push/subscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(subscription),
  })
  if (!res.ok) throw new Error(`Could not register for notifications (${res.status}).`)
}

export async function unsubscribePush(endpoint: string): Promise<void> {
  await fetch('/api/push/unsubscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint }),
  }).catch(() => {
    // Best-effort: the local subscription is already gone either way.
  })
}

/** Admin-only pipe check. scope 'self' = the caller's own devices;
 * 'all' = every subscribed user (phone-verification when onboarding).
 * Returns how many were sent / pruned as dead. */
export async function sendTestPush(
  scope: 'self' | 'all' = 'self',
): Promise<{ sent: number; pruned: number; recipients: Record<string, number> }> {
  const res = await fetch('/api/push/test-send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope }),
  })
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null)
    const message =
      body && typeof body === 'object' && 'message' in body
        ? String((body as { message: unknown }).message)
        : `Test send failed (${res.status}).`
    throw new Error(message)
  }
  return (await res.json()) as {
    sent: number
    pruned: number
    recipients: Record<string, number>
  }
}

/** Upload a captured check photo. XMLHttpRequest, not fetch — fetch cannot
 * report upload progress. qbInvoice is the optional 4-digit fast-path value
 * (decision flow): when present the workflow auto-matches and the card is
 * born resolved. */
export function uploadCheck(
  photo: Blob,
  onProgress: (percent: number) => void,
  qbInvoice?: string,
): Promise<ReviewItem> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/checks')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status === 201) {
        resolve(JSON.parse(xhr.responseText) as ReviewItem)
        return
      }
      let detail = `The server responded with ${xhr.status}.`
      try {
        detail = (JSON.parse(xhr.responseText) as { detail?: string }).detail ?? detail
      } catch {
        // keep the generic message
      }
      reject(new Error(detail))
    }
    xhr.onerror = () => reject(new Error('Network error during upload.'))
    const form = new FormData()
    form.append('file', photo, 'check.jpg')
    if (qbInvoice) form.append('qb_invoice', qbInvoice)
    xhr.send(form)
  })
}

// --- slab deliveries ---


/** Typeahead: served from the console's local jobs directory (~10ms). */
export function searchJobs(q: string): Promise<{ jobs: JobHit[] }> {
  return getJson<{ jobs: JobHit[] }>(`/api/jobs/search?q=${encodeURIComponent(q)}`)
}

export function fetchDeliveries(): Promise<ReviewItemList> {
  return getJson<ReviewItemList>(
    '/api/review-items?item_type=slab_delivery&limit=200',
  )
}

export function uploadDelivery(
  photo: Blob,
  onProgress: (percent: number) => void,
): Promise<ReviewItem> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/deliveries')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status === 201) {
        resolve(JSON.parse(xhr.responseText) as ReviewItem)
        return
      }
      let detail = `The server responded with ${xhr.status}.`
      try {
        detail = (JSON.parse(xhr.responseText) as { detail?: string }).detail ?? detail
      } catch {
        // keep the generic message
      }
      reject(new Error(detail))
    }
    xhr.onerror = () => reject(new Error('Network error during upload.'))
    const form = new FormData()
    form.append('file', photo, 'slip.jpg')
    xhr.send(form)
  })
}

export async function setDeliveryMode(id: string, mode: 'one' | 'split'): Promise<void> {
  const res = await fetch(`/api/deliveries/${id}/mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  if (!res.ok) throw new Error(`Could not set the mode (${res.status}).`)
}

export async function assignMaterial(
  id: string,
  payload: {
    material_index?: number | null
    stock?: boolean
    job_id?: string
    job_name?: string
    moraware_url?: string | null
  },
): Promise<{ assigned: number; total: number }> {
  const res = await fetch(`/api/deliveries/${id}/assign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null)
    const record = body && typeof body === 'object' ? (body as Record<string, unknown>) : null
    throw new ApiError(res.status, record,
      typeof record?.message === 'string' ? record.message
        : `Could not assign (${res.status}).`)
  }
  return (await res.json()) as { assigned: number; total: number }
}

export async function confirmDelivery(id: string): Promise<void> {
  const res = await fetch(`/api/deliveries/${id}/confirm`, { method: 'POST' })
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null)
    const record = body && typeof body === 'object' ? (body as Record<string, unknown>) : null
    throw new ApiError(res.status, record,
      typeof record?.message === 'string' ? record.message
        : `Could not confirm (${res.status}).`)
  }
}

export async function resendDelivery(id: string): Promise<void> {
  const res = await fetch(`/api/deliveries/${id}/resend`, { method: 'POST' })
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null)
    const record = body && typeof body === 'object' ? (body as Record<string, unknown>) : null
    throw new ApiError(res.status, record,
      typeof record?.message === 'string' ? record.message
        : `Could not resend (${res.status}).`)
  }
}

// ---- Slab scans (slab scans chapter) --------------------------------------

export function fetchScans(): Promise<ReviewItemList> {
  return getJson<ReviewItemList>('/api/scans/list')
}

/** Every slab ID already on a card (global uniqueness) — scanner preloads
 * this to reject a repeat scan instantly. */
export function fetchUsedSlabIds(): Promise<{ ids: string[] }> {
  return getJson<{ ids: string[] }>('/api/scans/used-ids')
}

/** Yard-safe scan card (item + events) — scanner staff never touch the
 * manager-only review-items endpoints. */
export function fetchScanCard(id: string): Promise<ItemCard> {
  return getJson<ItemCard>(`/api/scans/${id}/card`)
}

async function scanPost<T>(url: string, payload?: unknown, method = 'POST'): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: payload !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: payload !== undefined ? JSON.stringify(payload) : undefined,
  })
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const record = body && typeof body === 'object' ? (body as Record<string, unknown>) : null
    throw new ApiError(res.status, record,
      typeof record?.message === 'string' ? record.message
        : `The server responded with ${res.status}.`)
  }
  return body as T
}

export function createScan(slabIds: ScanSlab[]): Promise<ReviewItem> {
  return scanPost<ReviewItem>('/api/scans', { slab_ids: slabIds })
}

export function updateScanSlabs(id: string, slabIds: ScanSlab[]): Promise<void> {
  return scanPost<void>(`/api/scans/${id}/slabs`, { slab_ids: slabIds }, 'PUT')
}

export function assignScanJob(
  id: string,
  payload: { job_id: number; job_name: string; moraware_url?: string | null },
): Promise<void> {
  return scanPost<void>(`/api/scans/${id}/assign`, payload)
}

export function confirmScan(id: string): Promise<{ ok: boolean; note: string }> {
  return scanPost<{ ok: boolean; note: string }>(`/api/scans/${id}/confirm`)
}

/** Server-side Claude reads the printed ID from a label whose QR failed. */
export async function ocrScanLabel(photo: Blob): Promise<string[]> {
  const form = new FormData()
  form.append('file', photo, 'label.jpg')
  const res = await fetch('/api/scans/ocr', { method: 'POST', body: form })
  if (!res.ok) return []
  const body = (await res.json()) as { ids?: string[] }
  return body.ids ?? []
}

export function searchMaterials(q: string): Promise<{ materials: { name: string }[] }> {
  return getJson(`/api/materials/search?q=${encodeURIComponent(q)}`)
}

export function addMaterial(name: string): Promise<void> {
  return scanPost<void>('/api/materials', { name })
}
