import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import EdgeSwipeBack from './EdgeSwipeBack'
import PageTransition from './PageTransition'
import { useAuth } from '../lib/AuthContext'
import { canSee, PAYMENTS_ROLES } from '../lib/roles'
import { isPushSupported, registerServiceWorker } from '../lib/push'
import { useAutoUpdate } from '../lib/useAutoUpdate'
import Logo from './Logo'
import NotificationsControl from './NotificationsControl'

function MenuLink({ to, label, onPick }: { to: string; label: string; onPick: () => void }) {
  return (
    <NavLink
      to={to}
      onClick={onPick}
      className={({ isActive }) =>
        `block min-h-11 px-4 py-2.5 text-sm hover:bg-gray-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-600 ${
          isActive ? 'font-semibold text-blue-950' : 'font-medium text-gray-700'
        }`
      }
    >
      {label}
    </NavLink>
  )
}

function MenuSoon({ label }: { label: string }) {
  return (
    <span
      aria-disabled="true"
      className="block min-h-11 px-4 py-2.5 text-sm font-medium text-gray-300"
    >
      {label}
      <span className="ml-1.5 text-[10px] uppercase tracking-wide">soon</span>
    </span>
  )
}

/** Top-left hamburger: every tab, from anywhere, filtered by role. */
function MainMenu() {
  const { user } = useAuth()
  const role = user?.role
  const [open, setOpen] = useState(false)
  const close = () => setOpen(false)

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Menu"
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex h-11 w-11 items-center justify-center rounded-md text-white hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>
      {open && (
        <>
          {/* Click-away backdrop */}
          <div className="fixed inset-0 z-30" onClick={close} />
          <div
            role="menu"
            className="absolute left-0 top-full z-40 mt-1 w-56 rounded-md border border-gray-200 bg-white py-1 shadow-lg"
          >
            <MenuLink to="/" label="Home" onPick={close} />
            {canSee('payments', role) && <MenuLink to="/payments" label="Payments" onPick={close} />}
            {canSee('slabs', role) && PAYMENTS_ROLES.includes(role ?? '') && (
              <MenuLink to="/deliveries" label="Slab deliveries" onPick={close} />
            )}
            {canSee('slabs', role) && (
              <MenuLink to="/scans" label="Slab scans" onPick={close} />
            )}
            {canSee('supply', role) && <MenuSoon label="Supply log" />}
            {canSee('followups', role) && <MenuSoon label="Follow-ups" />}
            {canSee('leads', role) && <MenuSoon label="Leads" />}
          </div>
        </>
      )}
    </div>
  )
}

function UserBadge() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  if (!user) return null

  const name = user.display_name ?? user.email
  const initial = name.charAt(0).toUpperCase()

  async function signOut() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="inline-flex min-h-11 items-center gap-2 rounded-md px-2 text-sm font-medium text-blue-100 hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-white/15 font-semibold text-white">
          {initial}
        </span>
        <span className="hidden sm:inline">{name}</span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-40 mt-1 w-44 rounded-md border border-gray-200 bg-white py-1 shadow-lg"
        >
          <p className="truncate px-3 py-1.5 text-xs text-gray-500">{user.email}</p>
          {PAYMENTS_ROLES.includes(user.role) && (
            <>
              <div className="my-1 border-t border-gray-100" />
              <NotificationsControl />
              <div className="my-1 border-t border-gray-100" />
            </>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => void signOut()}
            className="block min-h-11 w-full px-3 text-left text-sm font-medium text-gray-700 hover:bg-gray-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-600"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}

export default function Layout() {
  const { user } = useAuth()
  useAutoUpdate()

  // Register the service worker on app load for subscribe-capable roles, so a
  // subscribed browser can receive pushes even without opening the menu.
  useEffect(() => {
    if (user && PAYMENTS_ROLES.includes(user.role) && isPushSupported()) {
      registerServiceWorker().catch(() => {
        // Non-fatal: the toggle re-registers on demand.
      })
    }
  }, [user])

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="bg-gradient-to-r from-blue-950 to-blue-900">
        <div className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 sm:px-5">
          <div className="flex items-center gap-2">
            <MainMenu />
            <Link
              to="/"
              className="inline-flex min-h-11 items-center gap-2.5 rounded-md text-lg font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
            >
              <Logo className="h-8 w-8" />
              EMG ops console
            </Link>
          </div>
          {/* Just the user badge — navigation lives in the hamburger and
              the home tiles (owner: no words next to the login name). */}
          <UserBadge />
        </div>
      </header>

      {/* Full-width canvas; each page sets its own max width if it wants one. */}
      <main className="w-full px-4 py-6 sm:px-6">
        <PageTransition>
          <Outlet />
        </PageTransition>
      </main>
      <EdgeSwipeBack />
    </div>
  )
}