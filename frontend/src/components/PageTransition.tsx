import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'

/** Wraps the router Outlet: re-keys on every path change so the page-in
 * animation replays, giving the whole app a smooth glide-in on navigation.
 * Full-screen capture pages (scanner, camera) portal to <body>, so they
 * sit OUTSIDE this wrapper and are unaffected by its transform. */
export default function PageTransition({ children }: { children: ReactNode }) {
  const location = useLocation()
  return (
    <div key={location.pathname} className="anim-page">
      {children}
    </div>
  )
}
