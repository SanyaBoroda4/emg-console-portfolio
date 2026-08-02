import type { ReactElement } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext'
import Logo from './Logo'

export function FullPageLoading() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-gray-50">
      <Logo className="h-12 w-12" />
      <p className="text-sm text-gray-500" role="status">
        Loading…
      </p>
    </div>
  )
}

/** Logged-out users are sent to /login; nothing renders until the initial
 * session check settles (no login-page flash for logged-in users). */
export function RequireAuth({ children }: { children: ReactElement }) {
  const { user, loading } = useAuth()
  if (loading) return <FullPageLoading />
  if (!user) return <Navigate to="/login" replace />
  return children
}

/** Role gate on top of RequireAuth: wrong role → home with a brief inline
 * message, never a raw 403 page. */
export function RequireRole({
  roles,
  children,
}: {
  roles: string[]
  children: ReactElement
}) {
  const { user, loading } = useAuth()
  if (loading) return <FullPageLoading />
  if (!user) return <Navigate to="/login" replace />
  if (!roles.includes(user.role)) {
    return <Navigate to="/" replace state={{ roleMessage: 'Not available for your role.' }} />
  }
  return children
}
