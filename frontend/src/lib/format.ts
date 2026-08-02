// Shared display formatting — cards and table must agree on how money,
// dates, and status badges look.

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

const shortDate = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

/** "4850.50" → "$4,850.50"; null/unparseable → null. */
export function formatAmount(amount: string | null | undefined): string | null {
  if (amount == null) return null
  const value = Number(amount)
  if (Number.isNaN(value)) return null
  return usd.format(value)
}

// A bare "2026-01-05" parsed with new Date() lands on UTC midnight and can
// display as the previous day in US timezones — parse it as a local date.
export function parseDateOnly(value: string): Date {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day)
}

/** ISO date or datetime string → "Jan 5, 2026"; null/invalid → null. */
export function formatDateish(value: string | null | undefined): string | null {
  if (!value) return null
  const parsed = /^\d{4}-\d{2}-\d{2}$/.test(value) ? parseDateOnly(value) : new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return shortDate.format(parsed)
}

const dateTimeFmt = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

/** ISO datetime → "Jul 11, 2026, 3:42 PM". */
export function formatDateTime(iso: string): string {
  return dateTimeFmt.format(new Date(iso))
}

export function relativeTime(iso: string): string {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.round(days / 30)
  if (months < 12) return `${months}mo ago`
  return `${Math.round(months / 12)}y ago`
}

// Badge colors are always paired with the status text — never color alone.
const BADGE_STYLES: Record<string, string> = {
  confirmed: 'bg-green-100 text-green-800',
  pending: 'bg-amber-100 text-amber-800',
  needs_job: 'bg-red-100 text-red-800',
  // Console-captured checks awaiting processing.
  submitted: 'bg-blue-100 text-blue-800',
  // CHECK-BOT is working on it right now (set by the outbound trigger).
  processing: 'bg-purple-100 text-purple-800',
  duplicate: 'bg-gray-200 text-gray-700',
  // Delivery assignments registered; the workflow is filing to Moraware.
  filing: 'bg-purple-100 text-purple-800',
  stock: 'bg-green-100 text-green-800',
  complete: 'bg-green-100 text-green-800',
}

export function statusBadgeClass(status: string): string {
  return BADGE_STYLES[status] ?? 'bg-gray-100 text-gray-700'
}

/** Deliveries show only three statuses to the user (owner 2026-07-22):
 * green "confirmed" once everything is filed (confirmed/stock/complete),
 * gray "duplicate", and yellow "pending" for everything in between. */
export function deliveryStatus(status: string): { label: string; klass: string } {
  if (status === 'confirmed' || status === 'stock' || status === 'complete') {
    return { label: 'confirmed', klass: 'bg-green-100 text-green-800' }
  }
  if (status === 'duplicate') {
    return { label: 'duplicate', klass: 'bg-gray-200 text-gray-700' }
  }
  return { label: 'pending', klass: 'bg-amber-100 text-amber-800' }
}
