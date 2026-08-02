import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { fetchStats } from '../api'
import { useAuth } from '../lib/AuthContext'
import { canSee, PAYMENTS_ROLES } from '../lib/roles'

// Each area of the business gets its own hue, so tiles are recognizable
// at a glance long before Stage 4 turns them all on.
function TileIcon({ tint, children }: { tint: string; children: React.ReactNode }) {
  return (
    <span className={`inline-flex h-11 w-11 items-center justify-center rounded-lg ${tint}`}>
      <svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {children}
      </svg>
    </span>
  )
}

const ICONS = {
  payments: (
    <>
      <rect x="2" y="6" width="20" height="12" rx="2" />
      <circle cx="12" cy="12" r="2.5" />
      <path d="M5.5 9.5h.01M18.5 14.5h.01" />
    </>
  ),
  slabs: (
    <>
      <path d="m12 3 9 5-9 5-9-5 9-5Z" />
      <path d="m3 13 9 5 9-5" />
    </>
  ),
  scans: (
    <>
      <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" />
      <path d="M7 12h10" />
    </>
  ),
  supply: (
    <>
      <path d="M21 8 12 3 3 8v8l9 5 9-5V8Z" />
      <path d="M3 8l9 5 9-5M12 13v8" />
    </>
  ),
  followups: (
    <>
      <path d="M21 12a8 8 0 1 0-3.1 6.3L21 19l-.8-3A7.9 7.9 0 0 0 21 12Z" />
      <path d="M8.5 12h.01M12 12h.01M15.5 12h.01" />
    </>
  ),
  leads: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
      <path d="M18 8v6M15 11h6" />
    </>
  ),
}

function DisabledTile({ label, tint, icon }: { label: string; tint: string; icon: React.ReactNode }) {
  return (
    <div
      aria-disabled="true"
      className="flex items-start gap-4 rounded-xl border border-gray-200 bg-white p-6 opacity-60"
    >
      <TileIcon tint={tint}>{icon}</TileIcon>
      <span>
        <span className="block text-xl font-semibold text-gray-500">{label}</span>
        <span className="mt-1 block text-sm text-gray-400">Coming soon</span>
      </span>
    </div>
  )
}

export default function HomePage() {
  const { user } = useAuth()
  const role = user?.role
  const location = useLocation()
  // Set by RequireRole when someone lands on a page their role can't use.
  const roleMessage = (location.state as { roleMessage?: string } | null)?.roleMessage

  const [pendingCount, setPendingCount] = useState(0)
  const showPayments = canSee('payments', role)

  useEffect(() => {
    if (!showPayments) return // yard can't call payments stats — don't try
    let cancelled = false
    fetchStats()
      .then((stats) => {
        if (!cancelled) setPendingCount(stats.by_status['pending'] ?? 0)
      })
      .catch(() => {
        // The switchboard still works without the badge.
      })
    return () => {
      cancelled = true
    }
  }, [showPayments])

  return (
    <div className="mx-auto max-w-2xl">
      <h2 className="sr-only">Home</h2>
      {roleMessage && (
        <p
          role="status"
          className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800"
        >
          {roleMessage}
        </p>
      )}
      {/* Tiles a role can't use are ABSENT, not disabled (plan §6). */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {showPayments && (
          <Link
            to="/payments"
            className="relative flex items-start gap-4 rounded-xl border border-blue-200 bg-white p-6 hover:border-blue-500 hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            <TileIcon tint="bg-blue-100 text-blue-700">{ICONS.payments}</TileIcon>
            <span>
              <span className="block text-xl font-semibold text-blue-950">Payments</span>
              <span className="mt-1 block text-sm text-gray-500">Checks &amp; deposits board</span>
            </span>
            {pendingCount > 0 && (
              <span className="absolute right-4 top-4 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800">
                {pendingCount} pending
              </span>
            )}
          </Link>
        )}
        {canSee('slabs', role) && PAYMENTS_ROLES.includes(role ?? '') && (
          <Link
            to="/deliveries"
            className="relative flex items-start gap-4 rounded-xl border border-purple-200 bg-white p-6 hover:border-purple-500 hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-purple-600"
          >
            <TileIcon tint="bg-purple-100 text-purple-700">{ICONS.slabs}</TileIcon>
            <span>
              <span className="block text-xl font-semibold text-purple-950">Slab deliveries</span>
              <span className="mt-1 block text-sm text-gray-500">Delivery slips &amp; job assignment</span>
            </span>
          </Link>
        )}
        {canSee('slabs', role) && (
          <Link
            to="/scans"
            className="relative flex items-start gap-4 rounded-xl border border-teal-200 bg-white p-6 hover:border-teal-500 hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600"
          >
            <TileIcon tint="bg-teal-100 text-teal-700">{ICONS.scans}</TileIcon>
            <span>
              <span className="block text-xl font-semibold text-teal-950">Slab scans</span>
              <span className="mt-1 block text-sm text-gray-500">Label QR scans &rarr; Moraware notes</span>
            </span>
          </Link>
        )}
        {canSee('supply', role) && (
          <DisabledTile label="Supply log" tint="bg-amber-100 text-amber-700" icon={ICONS.supply} />
        )}
        {canSee('followups', role) && (
          <DisabledTile label="Follow-ups" tint="bg-green-100 text-green-700" icon={ICONS.followups} />
        )}
        {canSee('leads', role) && (
          <DisabledTile label="Leads" tint="bg-rose-100 text-rose-700" icon={ICONS.leads} />
        )}
      </div>
    </div>
  )
}
