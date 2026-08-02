import { useEffect } from 'react'

/** Self-updating PWA: iOS keeps the home-screen app's page alive across
 * deploys, so the owner used to force-close twice after every release.
 * Instead: whenever the app comes to the foreground (and every 10 min),
 * fetch a fresh index.html and compare its bundle hash with the one this
 * page is actually running. Different hash → reload once, silently. */
export function useAutoUpdate() {
  useEffect(() => {
    // The hash of the bundle THIS page is running (prod builds only —
    // the vite dev server has no hashed bundle, so do nothing there).
    const running = document
      .querySelector('script[type="module"][src*="/assets/index-"]')
      ?.getAttribute('src')
    if (!running) return

    let reloading = false
    const check = async () => {
      if (reloading) return
      try {
        const res = await fetch('/', { cache: 'no-store' })
        if (!res.ok) return
        const html = await res.text()
        const latest = /\/assets\/index-[\w-]+\.js/.exec(html)?.[0]
        if (latest && !running.endsWith(latest.slice(1)) && latest !== running) {
          reloading = true
          window.location.reload()
        }
      } catch {
        // offline / flaky network: try again next time
      }
    }

    const onVisible = () => {
      if (document.visibilityState === 'visible') void check()
    }
    void check()
    const timer = window.setInterval(() => void check(), 10 * 60 * 1000)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])
}
