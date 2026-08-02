// Web Push client helpers (push mechanics slice §4). Registers the service
// worker, subscribes/unsubscribes via the browser PushManager, and syncs the
// subscription with the backend.

import { fetchVapidPublicKey, subscribePush, unsubscribePush } from '../api'

export type PushState =
  | 'unsupported' // this browser lacks the APIs
  | 'denied' // user blocked notifications
  | 'off' // supported, permission not yet granted / not subscribed
  | 'on' // subscribed and ready

export function isPushSupported(): boolean {
  return (
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

/** Register the service worker (idempotent — re-registering the same URL
 * returns the existing registration). Safe to call on every app load. */
export async function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  return navigator.serviceWorker.register('/sw.js')
}

/** Current state without prompting or changing anything. */
export async function getPushState(): Promise<PushState> {
  if (!isPushSupported()) return 'unsupported'
  if (Notification.permission === 'denied') return 'denied'
  const reg = await navigator.serviceWorker.ready
  const sub = await reg.pushManager.getSubscription()
  return sub ? 'on' : 'off'
}

// VAPID public key (base64url) → the Uint8Array applicationServerKey the
// PushManager wants.
function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base64)
  // Construct over an explicit ArrayBuffer so the type is Uint8Array<ArrayBuffer>,
  // which applicationServerKey (BufferSource) requires under TS 5.7+.
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

/** Prompt for permission, subscribe, and register with the backend. Returns
 * the resulting state. Throws with a plain message on unexpected failures. */
export async function enablePush(): Promise<PushState> {
  if (!isPushSupported()) return 'unsupported'
  const reg = await registerServiceWorker()

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') return permission === 'denied' ? 'denied' : 'off'

  const { key } = await fetchVapidPublicKey()
  if (!key) {
    throw new Error('Push is not configured on the server yet (no VAPID key).')
  }

  // Reuse an existing subscription if present, else create one.
  let sub = await reg.pushManager.getSubscription()
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    })
  }
  await subscribePush(sub.toJSON())
  return 'on'
}

/** Unsubscribe locally and tell the backend to drop the row. */
export async function disablePush(): Promise<PushState> {
  if (!isPushSupported()) return 'unsupported'
  const reg = await navigator.serviceWorker.ready
  const sub = await reg.pushManager.getSubscription()
  if (sub) {
    const endpoint = sub.endpoint
    await sub.unsubscribe()
    await unsubscribePush(endpoint)
  }
  return Notification.permission === 'denied' ? 'denied' : 'off'
}
