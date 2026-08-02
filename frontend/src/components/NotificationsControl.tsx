import { useEffect, useState } from 'react'
import { sendTestPush } from '../api'
import { useAuth } from '../lib/AuthContext'
import {
  disablePush,
  enablePush,
  getPushState,
  isPushSupported,
  registerServiceWorker,
  type PushState,
} from '../lib/push'

/** Badge-menu control to turn Web Push on/off for this browser (push
 * mechanics slice §4). Rendered only for admin/manager (who can subscribe).
 * The "Send test notification" button is added in Part 4. */
export default function NotificationsControl() {
  const { user } = useAuth()
  const [state, setState] = useState<PushState | 'loading'>('loading')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    if (!isPushSupported()) {
      setState('unsupported')
      return
    }
    // Ensure the SW is registered before reading state (serviceWorker.ready
    // only resolves once something is registered).
    registerServiceWorker()
      .then(getPushState)
      .then((s) => {
        if (alive) setState(s)
      })
      .catch(() => {
        if (alive) setState('off')
      })
    return () => {
      alive = false
    }
  }, [])

  async function toggle() {
    setError(null)
    setTestResult(null)
    setBusy(true)
    try {
      setState(state === 'on' ? await disablePush() : await enablePush())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  async function sendTest(scope: 'self' | 'all') {
    setError(null)
    setTestResult(null)
    setBusy(true)
    try {
      const { sent, recipients } = await sendTestPush(scope)
      // Name exactly who was targeted — removes all guesswork about which
      // button sent what during phone verification.
      const who = Object.entries(recipients)
        .map(([email, count]) => `${email} (${count})`)
        .join(', ')
      setTestResult(sent > 0 ? `Sent to: ${who}` : 'No active devices to send to.')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not send test.')
    } finally {
      setBusy(false)
    }
  }

  if (state === 'loading') return null
  if (state === 'unsupported') {
    // iPhones only expose Web Push to sites installed on the Home Screen —
    // in a regular Safari/Chrome tab the API simply doesn't exist. Tell the
    // user the actual way in instead of a dead-end "not supported".
    const isIos = /iPad|iPhone|iPod/.test(navigator.userAgent)
    return (
      <p className="px-3 py-1.5 text-xs text-gray-400">
        {isIos
          ? 'On iPhone: add this site to your Home Screen (tap Share → "Add to Home Screen"), then open it from that icon to enable notifications.'
          : 'Notifications aren’t supported on this browser'}
      </p>
    )
  }

  const label = busy
    ? 'Working…'
    : state === 'on'
      ? 'Notifications on ✓'
      : state === 'denied'
        ? 'Notifications blocked'
        : 'Enable notifications'

  return (
    <>
      <button
        type="button"
        role="menuitem"
        disabled={busy || state === 'denied'}
        onClick={() => void toggle()}
        className="block min-h-11 w-full px-3 text-left text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:text-gray-400 disabled:hover:bg-transparent focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-600"
      >
        {label}
      </button>
      {/* Admin-only pipe checks, visible once subscribed: to my own devices,
          and to everyone (verifying a manager's phone during onboarding). */}
      {state === 'on' && user?.role === 'admin' && (
        <>
          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={() => void sendTest('self')}
            className="block min-h-11 w-full px-3 text-left text-sm font-medium text-blue-800 hover:bg-gray-50 disabled:text-gray-400 disabled:hover:bg-transparent focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-600"
          >
            Test my devices
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={() => void sendTest('all')}
            className="block min-h-11 w-full px-3 text-left text-sm font-medium text-blue-800 hover:bg-gray-50 disabled:text-gray-400 disabled:hover:bg-transparent focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-600"
          >
            Test ALL team devices
          </button>
        </>
      )}
      {state === 'denied' && (
        <p className="px-3 pb-1.5 text-xs text-gray-400">
          Turn them back on in your browser’s site settings.
        </p>
      )}
      {testResult && <p className="px-3 pb-1.5 text-xs text-gray-500">{testResult}</p>}
      {error && <p className="px-3 pb-1.5 text-xs text-red-600">{error}</p>}
    </>
  )
}
