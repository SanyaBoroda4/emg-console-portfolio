import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/** "Good job!" celebration when a scan card confirms — plays the owner's
 * clip (bundled in /public, ~690KB). Tries to play with sound; if the
 * browser blocks autoplay audio it falls back to muted. Auto-dismisses
 * when the clip ends (or after a safety timeout), and tap-to-skip. */
const COLORS = ['#22d3ee', '#34d399', '#fbbf24', '#f472b6', '#60a5fa', '#a78bfa']

export default function Celebration({ onDone }: { onDone: () => void }) {
  const [leaving, setLeaving] = useState(false)
  const doneRef = useRef(false)

  const finish = useCallback(() => {
    if (doneRef.current) return
    doneRef.current = true
    setLeaving(true)
    window.setTimeout(onDone, 900) // let the slow fade-out finish
  }, [onDone])

  const attachVideo = useCallback((node: HTMLVideoElement | null) => {
    if (!node) return
    node.muted = true // owner: no sound
    node.playbackRate = 2 // owner: half as long (plays 2x faster)
    node.play().catch(() => {})
  }, [])

  // Safety net if `onEnded` never fires (clip is ~8s → ~4s at 2x).
  useEffect(() => {
    const t = window.setTimeout(finish, 6000)
    return () => window.clearTimeout(t)
  }, [finish])

  const pieces = Array.from({ length: 36 }, (_, i) => ({
    left: Math.random() * 100,
    delay: Math.random() * 0.5,
    duration: 1.8 + Math.random() * 1.3,
    color: COLORS[i % COLORS.length],
    size: 7 + Math.random() * 8,
  }))

  return createPortal(
    <div
      onClick={finish}
      className={`fixed inset-0 z-[70] flex items-center justify-center overflow-hidden bg-slate-950/80 backdrop-blur-sm ${
        leaving ? 'anim-soft-out' : 'anim-soft-in'
      }`}
    >
      {pieces.map((p, i) => (
        <span
          key={i}
          className="confetti-piece pointer-events-none absolute top-0 rounded-[2px]"
          style={{
            left: `${p.left}%`,
            width: p.size,
            height: p.size * 1.4,
            background: p.color,
            animation: `confetti-fall ${p.duration}s linear ${p.delay}s both`,
          }}
        />
      ))}
      <div className="anim-soft-rise flex flex-col items-center gap-3">
        <video
          ref={attachVideo}
          src="/goodjob.mp4"
          autoPlay
          muted
          playsInline
          onEnded={finish}
          className="max-h-[74vh] w-auto max-w-[90vw] rounded-2xl shadow-2xl"
        />
        <span className="text-xs font-medium uppercase tracking-[0.2em] text-white/50">
          tap to skip
        </span>
      </div>
    </div>,
    document.body,
  )
}
