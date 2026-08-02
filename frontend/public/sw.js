/* EMG ops console — service worker (push mechanics slice §4).
 * Push delivery ONLY: no caching, no offline. Payload is JSON
 * { title, body, url }. Kept deliberately tiny. */

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch (_e) {
    data = {}
  }
  const title = data.title || 'EMG ops console'
  const body = data.body || ''
  const url = (data.data && data.data.url) || data.url || '/'
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      data: { url },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((wins) => {
        // Focus an existing tab on this app if one is open, else open a new one.
        for (const w of wins) {
          if ('focus' in w && new URL(w.url).pathname === url) return w.focus()
        }
        for (const w of wins) {
          if ('focus' in w) {
            w.navigate(url)
            return w.focus()
          }
        }
        if (self.clients.openWindow) return self.clients.openWindow(url)
      }),
  )
})
