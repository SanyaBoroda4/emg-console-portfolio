import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

/** iPhone-style back gesture for the whole app: a rightward swipe that
 * STARTS at the left screen edge navigates back — Home-Screen web apps have
 * no native back gesture, and every board→card hop deserves one. Starting
 * at the edge keeps it clear of in-content gestures (swipe-to-delete moves
 * LEFT and starts mid-screen). */
const EDGE_PX = 28
const TRIGGER_DX = 70
const MAX_DY = 60

export default function EdgeSwipeBack() {
  const navigate = useNavigate()
  const start = useRef<{ x: number; y: number } | null>(null)
  const fired = useRef(false)

  useEffect(() => {
    function onStart(event: globalThis.TouchEvent) {
      const touch = event.touches[0]
      start.current = touch.clientX <= EDGE_PX
        ? { x: touch.clientX, y: touch.clientY }
        : null
      fired.current = false
    }
    function onMove(event: globalThis.TouchEvent) {
      if (!start.current || fired.current) return
      const touch = event.touches[0]
      const dx = touch.clientX - start.current.x
      const dy = Math.abs(touch.clientY - start.current.y)
      if (dx > TRIGGER_DX && dy < MAX_DY) {
        fired.current = true
        navigate(-1)
      }
    }
    function onEnd() {
      start.current = null
    }
    window.addEventListener('touchstart', onStart, { passive: true })
    window.addEventListener('touchmove', onMove, { passive: true })
    window.addEventListener('touchend', onEnd, { passive: true })
    return () => {
      window.removeEventListener('touchstart', onStart)
      window.removeEventListener('touchmove', onMove)
      window.removeEventListener('touchend', onEnd)
    }
  }, [navigate])

  return null
}
