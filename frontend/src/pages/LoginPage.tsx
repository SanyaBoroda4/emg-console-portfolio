import { useEffect, useRef, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { loginWithGoogle } from '../api'
import Logo from '../components/Logo'
import { FullPageLoading } from '../components/RequireAuth'
import { useAuth } from '../lib/AuthContext'

// iPhones/iPads: the GIS popup flow white-screens in Safari and inside Home
// Screen web apps, so those devices use full-page redirect mode instead.
const IS_IOS = /iPad|iPhone|iPod/.test(navigator.userAgent)

export default function LoginPage() {
  const { user, loading, setUser } = useAuth()
  const navigate = useNavigate()
  const buttonRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(() => {
    // The redirect endpoint reports failures via /login?error=...
    return new URLSearchParams(window.location.search).get('error')
  })
  const [pending, setPending] = useState(false)

  useEffect(() => {
    if (loading || user) return
    const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined
    if (!clientId) {
      setError('GOOGLE_CLIENT_ID is not configured — check .env and restart docker compose.')
      return
    }

    let cancelled = false
    // The GIS <script> loads async; poll briefly until it's there.
    function init() {
      if (cancelled) return
      const google = window.google
      if (!google?.accounts?.id) {
        window.setTimeout(init, 100)
        return
      }
      google.accounts.id.initialize(
        IS_IOS
          ? {
              // Redirect mode: Google form-POSTs the credential to our
              // backend, which sets the cookie and redirects into the app.
              client_id: clientId as string,
              ux_mode: 'redirect',
              login_uri: `${window.location.origin}/api/auth/google/redirect`,
            }
          : {
              client_id: clientId as string,
              callback: (response) => {
                setPending(true)
                setError(null)
                loginWithGoogle(response.credential)
                  .then((me) => {
                    setUser(me)
                    navigate('/', { replace: true })
                  })
                  .catch((err: unknown) => {
                    // Includes the backend's "not set up for this console"
                    // message. Deliberately no automatic retry.
                    setError(err instanceof Error ? err.message : 'Sign-in failed.')
                  })
                  .finally(() => setPending(false))
              },
            },
      )
      if (buttonRef.current) {
        google.accounts.id.renderButton(buttonRef.current, {
          theme: 'outline',
          size: 'large',
          width: 280,
        })
      }
    }
    init()
    return () => {
      cancelled = true
    }
  }, [loading, user, navigate, setUser])

  if (loading) return <FullPageLoading />
  if (user) return <Navigate to="/" replace />

  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
      <div className="flex w-full max-w-sm flex-col items-center gap-5 rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
        <Logo className="h-14 w-14" />
        <h1 className="text-xl font-semibold text-blue-950">EMG ops console</h1>
        <p className="text-center text-sm text-gray-500">
          Sign in with your EMG Google account
        </p>

        {pending ? (
          <p className="text-sm text-gray-500" role="status">
            Signing in…
          </p>
        ) : (
          <div ref={buttonRef} />
        )}

        {error && (
          <div role="alert" className="w-full rounded-md bg-red-50 p-3 text-center">
            <p className="text-sm text-red-800">{error}</p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-2 min-h-11 rounded-md px-4 text-sm font-medium text-red-700 underline hover:text-red-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600"
            >
              Retry
            </button>
          </div>
        )}
      </div>
    </main>
  )
}
