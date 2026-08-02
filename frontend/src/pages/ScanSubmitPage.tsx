import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { createScan, fetchUsedSlabIds, ocrScanLabel } from '../api'
import { toJpegSafe } from '../lib/image'
import { decodeLabelPhoto, decodeVideoFrame } from '../lib/qr'
import MaterialPicker from '../components/MaterialPicker'
import type { ScanSlab } from '../types'

/** Slab label scanner (slab scans chapter) — the futuristic edition.
 *  - live camera: neon guide brackets + sweeping scan line; a captured QR
 *    plays a three-note chime and freezes the screen behind a fullscreen
 *    glass panel (the ONLY Finish visible) until "Scan next" / "Finish".
 *  - gallery: photos decode on-device; no-QR photos go to server OCR.
 */

const FRAME_INTERVAL_MS = 350

type CameraState = 'starting' | 'ready' | 'unavailable'

export default function ScanSubmitPage() {
  const navigate = useNavigate()
  const [stage, setStage] = useState<'choose' | 'camera'>('choose')
  const [slabs, setSlabs] = useState<ScanSlab[]>([])
  const [manual, setManual] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [captured, setCaptured] = useState<string[] | null>(null)
  const [capturedMaterial, setCapturedMaterial] = useState<string | null>(null)
  const [dismissing, setDismissing] = useState(false)
  const [duplicate, setDuplicate] = useState<string[] | null>(null)
  const usedGlobal = useRef<Set<string>>(new Set())
  const [camera, setCamera] = useState<CameraState>('starting')
  const [cameraNonce, setCameraNonce] = useState(0)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const decoding = useRef(false)
  const audioRef = useRef<AudioContext | null>(null)

  // Preload every slab ID already on a card — a repeat scan is blocked
  // instantly, before it can land on this card (owner: numbers never repeat).
  useEffect(() => {
    void fetchUsedSlabIds()
      .then((r) => { usedGlobal.current = new Set(r.ids) })
      .catch(() => {})
  }, [])

  // Low "denied" buzz for a duplicate scan (distinct from the success chime).
  function buzz() {
    const ctx = audioRef.current
    if (!ctx) return
    try {
      const t = ctx.currentTime
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sawtooth'
      osc.frequency.setValueAtTime(220, t)
      osc.frequency.exponentialRampToValueAtTime(110, t + 0.3)
      gain.gain.setValueAtTime(0.3, t)
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.35)
      osc.connect(gain).connect(ctx.destination)
      osc.start(t)
      osc.stop(t + 0.36)
    } catch {
      // best effort
    }
  }

  function primeAudio() {
    try {
      audioRef.current = audioRef.current ?? new AudioContext()
      void audioRef.current.resume()
    } catch {
      audioRef.current = null
    }
  }

  /** Sci-fi confirmation: quick ascending C5–G5–G6 arpeggio with a soft
   * shimmer tail — unmistakably "got it", not an alarm. */
  function chime() {
    const ctx = audioRef.current
    if (!ctx) return
    try {
      const t = ctx.currentTime
      const master = ctx.createGain()
      master.gain.value = 0.6
      master.connect(ctx.destination)
      const notes: Array<[number, number, number]> = [
        [523.25, 0, 0.3],
        [783.99, 0.07, 0.3],
        [1567.98, 0.14, 0.45],
      ]
      for (const [freq, dt, dur] of notes) {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.type = 'triangle'
        osc.frequency.value = freq
        gain.gain.setValueAtTime(0.0001, t + dt)
        gain.gain.exponentialRampToValueAtTime(0.4, t + dt + 0.02)
        gain.gain.exponentialRampToValueAtTime(0.0001, t + dt + dur)
        osc.connect(gain).connect(master)
        osc.start(t + dt)
        osc.stop(t + dt + dur + 0.05)
      }
    } catch {
      // sound is best-effort
    }
  }

  // Mirror of `slabs` that updates SYNCHRONOUSLY — the decode loop needs an
  // immediate, reliable answer to "was that a NEW number?".
  const slabsRef = useRef<ScanSlab[]>([])
  useEffect(() => {
    slabsRef.current = slabs
  }, [slabs])

  const addIds = useCallback((ids: string[], source: ScanSlab['source']) => {
    const seen = new Set(slabsRef.current.map((s) => s.id))
    const fresh = [...new Set(ids)].filter((id) => !seen.has(id))
    if (fresh.length === 0) return 0
    const next = [...slabsRef.current, ...fresh.map((id) => ({ id, source }))]
    slabsRef.current = next
    setSlabs(next)
    return fresh.length
  }, [])

  function removeId(id: string) {
    slabsRef.current = slabsRef.current.filter((s) => s.id !== id)
    setSlabs(slabsRef.current)
  }

  // Tag the just-captured slab(s) with a material inline (optional).
  function applyMaterialToCaptured(name: string) {
    const ids = new Set(captured ?? [])
    const next = slabsRef.current.map((s) =>
      ids.has(s.id) ? { ...s, material: name } : s,
    )
    slabsRef.current = next
    setSlabs(next)
    setCapturedMaterial(name)
  }

  // Play the exit animation, THEN clear the panel and resume scanning.
  function dismissCapture() {
    primeAudio()
    setDismissing(true)
    window.setTimeout(() => {
      setCaptured(null)
      setCapturedMaterial(null)
      setDismissing(false)
    }, 300)
  }

  function addManual() {
    const id = manual.trim()
    if (!/^\d{5,9}$/.test(id)) {
      setError('A slab number is 5–9 digits.')
      return
    }
    setError(null)
    addIds([id], 'manual')
    setManual('')
  }

  // ---- gallery path -------------------------------------------------------
  async function onGalleryPicked(files: FileList | null) {
    if (!files || files.length === 0) return
    setError(null)
    const unreadable: string[] = []
    const alreadyUsed: string[] = []
    let index = 0
    for (const file of Array.from(files)) {
      index += 1
      setBusy(`Reading photo ${index} of ${files.length}…`)
      let ids: string[] = []
      try {
        ids = await decodeLabelPhoto(file)
      } catch {
        ids = []
      }
      if (ids.length === 0) {
        setBusy(`Photo ${index}: no QR — asking the reader…`)
        ids = await ocrScanLabel(await toJpegSafe(file))
      }
      const dup = ids.filter((id) => usedGlobal.current.has(id))
      const fresh = ids.filter((id) => !usedGlobal.current.has(id))
      if (ids.length === 0) unreadable.push(file.name)
      if (dup.length > 0) alreadyUsed.push(...dup)
      if (fresh.length > 0) {
        addIds(fresh, 'qr')
        fresh.forEach((id) => usedGlobal.current.add(id))
      }
    }
    setBusy(null)
    const notes: string[] = []
    if (unreadable.length > 0)
      notes.push(`${unreadable.length} photo(s) couldn't be read`)
    if (alreadyUsed.length > 0)
      notes.push(`already on another card: ${[...new Set(alreadyUsed)].join(', ')}`)
    if (notes.length > 0) setError(notes.join(' · '))
  }

  // ---- live camera path ---------------------------------------------------
  useEffect(() => {
    if (stage !== 'camera') return
    if (!navigator.mediaDevices?.getUserMedia) {
      setCamera('unavailable')
      return
    }
    let cancelled = false
    setCamera('starting')
    const open = async (attempt: number) => {
      try {
        const constraints: MediaStreamConstraints =
          attempt < 5
            ? {
                video: {
                  facingMode: 'environment',
                  width: { ideal: 2560 },
                  height: { ideal: 1440 },
                },
                audio: false,
              }
            : { video: { facingMode: 'environment' }, audio: false }
        const stream = await navigator.mediaDevices.getUserMedia(constraints)
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          void videoRef.current.play()
        }
        setCamera('ready')
      } catch (err) {
        const name = err instanceof DOMException ? err.name : ''
        if (name === 'NotAllowedError' || name === 'SecurityError') {
          if (!cancelled) setCamera('unavailable')
          return
        }
        if (!cancelled && attempt < 7) {
          window.setTimeout(() => void open(attempt + 1), 800)
          return
        }
        if (!cancelled) setCamera('unavailable')
      }
    }
    void open(0)
    return () => {
      cancelled = true
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
  }, [stage, cameraNonce])

  const attachVideo = useCallback((node: HTMLVideoElement | null) => {
    videoRef.current = node
    if (node && streamRef.current) {
      node.srcObject = streamRef.current
      void node.play()
    }
  }, [])

  // Continuous decode loop while the viewfinder is live; pauses behind the
  // capture panel. If the engine keeps failing, SAY so instead of silence.
  const decodeFailures = useRef(0)
  const [scannerBroken, setScannerBroken] = useState(false)
  useEffect(() => {
    if (stage !== 'camera' || camera !== 'ready' || captured !== null || duplicate !== null) return
    const timer = window.setInterval(() => {
      const video = videoRef.current
      if (!video || video.readyState < 2 || decoding.current) return
      decoding.current = true
      void decodeVideoFrame(video)
        .then((ids) => {
          decodeFailures.current = 0
          const dup = ids.filter((id) => usedGlobal.current.has(id))
          const fresh = ids.filter((id) => !usedGlobal.current.has(id))
          const added = addIds(fresh, 'qr')
          if (added > 0) {
            chime()
            navigator.vibrate?.(150)
            fresh.forEach((id) => usedGlobal.current.add(id))
            setDismissing(false)
            setCapturedMaterial(null)
            setCaptured(fresh)
          } else if (dup.length > 0 && !captured) {
            buzz()
            navigator.vibrate?.([80, 40, 80])
            setDuplicate(dup)
          }
        })
        .catch(() => {
          decodeFailures.current += 1
          if (decodeFailures.current >= 8) setScannerBroken(true)
        })
        .finally(() => {
          decoding.current = false
        })
    }, FRAME_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [stage, camera, addIds, captured, duplicate])

  // ---- finish -------------------------------------------------------------
  async function finish() {
    if (slabsRef.current.length === 0) {
      setError('No slab numbers yet — scan a label or type a number.')
      return
    }
    setBusy('Creating the card…')
    try {
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
      const item = await createScan(slabsRef.current)
      navigate(`/scans/item/${item.id}`)
    } catch (err) {
      setBusy(null)
      setError(err instanceof Error ? err.message : 'Could not create the card.')
    }
  }

  // ---- shared UI pieces ---------------------------------------------------
  const chips = slabs.length > 0 && (
    <div className="flex w-full flex-wrap items-center justify-center gap-2">
      {slabs.map((s) => (
        <span
          key={s.id}
          className="anim-slide-right inline-flex items-center gap-2 rounded-full border border-cyan-400/40 bg-cyan-400/10 px-3.5 py-1.5 font-mono text-[15px] font-semibold tracking-wider text-cyan-200 backdrop-blur"
        >
          {s.id}
          <button
            type="button"
            aria-label={`Remove ${s.id}`}
            onClick={() => removeId(s.id)}
            className="text-cyan-200/50 transition-colors hover:text-red-400"
          >
            ×
          </button>
        </span>
      ))}
    </div>
  )

  const manualRow = (
    <div className="flex w-full items-center justify-center gap-2">
      <input
        type="text"
        inputMode="numeric"
        placeholder="Type a number"
        value={manual}
        onChange={(e) => setManual(e.target.value.replace(/\D/g, '').slice(0, 9))}
        onKeyDown={(e) => {
          if (e.key === 'Enter') addManual()
        }}
        className="min-h-11 w-44 rounded-xl border border-white/15 bg-white/5 px-3 text-center font-mono text-lg font-semibold tracking-widest text-white backdrop-blur placeholder:font-sans placeholder:text-sm placeholder:tracking-normal placeholder:text-slate-500 focus:border-cyan-400/70 focus:outline-none focus:ring-1 focus:ring-cyan-400/40"
      />
      <button
        type="button"
        onClick={addManual}
        className="min-h-11 rounded-xl border border-white/15 bg-white/5 px-4 font-medium text-slate-200 backdrop-blur transition-colors hover:border-cyan-400/60 hover:text-white"
      >
        Add
      </button>
    </div>
  )

  const finishButton = (extra = '') => (
    <button
      type="button"
      onClick={() => void finish()}
      disabled={busy !== null}
      className={`min-h-[54px] w-full max-w-xs rounded-2xl bg-gradient-to-r from-emerald-500 to-teal-500 text-[16px] font-semibold text-white shadow-lg shadow-emerald-500/25 transition-transform hover:scale-[1.02] disabled:opacity-50 ${extra}`}
    >
      Finish — {slabs.length} slab{slabs.length === 1 ? '' : 's'}
    </button>
  )

  return createPortal((
    <div className="fixed inset-0 z-50 overflow-y-auto bg-gradient-to-b from-slate-950 via-[#060b1c] to-slate-950">
      <button
        type="button"
        onClick={() => navigate('/scans')}
        aria-label="Close"
        className="absolute left-4 top-4 z-30 flex h-11 w-11 items-center justify-center rounded-full border border-white/10 bg-white/5 text-2xl leading-none text-white backdrop-blur transition-colors hover:bg-white/15"
      >
        ×
      </button>

      {stage === 'choose' ? (
        /* ================= CHOOSE / GALLERY ================= */
        <div className="anim-rise flex min-h-full flex-col items-center justify-center gap-5 p-6">
          <div className="text-center">
            <h1 className="bg-gradient-to-r from-cyan-300 via-sky-300 to-blue-400 bg-clip-text text-3xl font-bold tracking-tight text-transparent">
              Upload slabs
            </h1>
            <p className="mt-1.5 text-sm text-slate-400">
              Scan labels live, or read them from photos
            </p>
          </div>

          <button
            type="button"
            onClick={() => {
              primeAudio()
              setStage('camera')
            }}
            className="anim-glow min-h-[58px] w-full max-w-xs rounded-2xl bg-gradient-to-r from-cyan-500 to-blue-600 text-[17px] font-semibold text-white transition-transform hover:scale-[1.02]"
          >
            Scan with camera
          </button>
          <label className="flex min-h-[58px] w-full max-w-xs cursor-pointer items-center justify-center rounded-2xl border border-white/15 bg-white/5 text-[17px] font-semibold text-slate-100 backdrop-blur transition-colors hover:border-cyan-400/60">
            Pick from gallery
            <input
              type="file"
              accept="image/*"
              multiple
              className="sr-only"
              onChange={(e) => {
                primeAudio()
                void onGalleryPicked(e.target.files)
                e.target.value = ''
              }}
            />
          </label>

          {busy && (
            <p className="anim-slide-left text-sm text-cyan-300" role="status">
              {busy}
            </p>
          )}
          {error && (
            <p role="alert" className="anim-slide-left max-w-sm text-center text-sm text-red-400">
              {error}
            </p>
          )}

          {(slabs.length > 0 || manual) && (
            <div className="flex w-full max-w-md flex-col items-center gap-4">
              {chips}
              {manualRow}
              {slabs.length > 0 && finishButton('anim-rise')}
            </div>
          )}
          {slabs.length === 0 && manualRow}
        </div>
      ) : (
        /* ================= LIVE SCANNER ================= */
        <div className="relative flex min-h-full flex-col">
          {camera === 'ready' ? (
            <div className="relative h-[56vh] w-full shrink-0 overflow-hidden">
              <video
                ref={attachVideo}
                autoPlay
                playsInline
                muted
                className="absolute inset-0 h-full w-full object-cover"
              />
              <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-slate-950/60 via-transparent to-slate-950/80" />

              {/* Neon guide: corner brackets + sweeping scan line */}
              <div className="pointer-events-none absolute left-1/2 top-1/2 aspect-square w-[min(72vw,46vh)] -translate-x-1/2 -translate-y-1/2">
                {(['top-0 left-0 border-t-2 border-l-2 rounded-tl-xl',
                   'top-0 right-0 border-t-2 border-r-2 rounded-tr-xl',
                   'bottom-0 left-0 border-b-2 border-l-2 rounded-bl-xl',
                   'bottom-0 right-0 border-b-2 border-r-2 rounded-br-xl',
                ] as const).map((pos) => (
                  <span
                    key={pos}
                    className={`absolute h-8 w-8 border-cyan-400 ${pos}`}
                    style={{ filter: 'drop-shadow(0 0 6px rgba(34,211,238,0.9))' }}
                  />
                ))}
                <span className="anim-scan-line absolute left-[6%] right-[6%] h-0.5 rounded bg-gradient-to-r from-transparent via-cyan-300 to-transparent" />
              </div>

              <p className="absolute bottom-3 left-1/2 w-11/12 max-w-sm -translate-x-1/2 rounded-full border border-white/10 bg-slate-950/70 px-4 py-1.5 text-center text-[13px] text-slate-200 backdrop-blur">
                {scannerBroken
                  ? 'Scanner engine failed on this phone — use Pick from gallery or type the numbers'
                  : 'Align the QR inside the brackets'}
              </p>
            </div>
          ) : camera === 'starting' ? (
            <div className="flex h-[56vh] items-center justify-center">
              <p className="anim-rise text-slate-300">Starting camera…</p>
            </div>
          ) : (
            <div className="flex h-[56vh] flex-col items-center justify-center gap-3 p-6 text-center">
              <p className="max-w-sm text-slate-200">
                The in-app camera isn&apos;t available. Pick the label photos from
                the gallery instead, or try again:
              </p>
              <button
                type="button"
                onClick={() => setCameraNonce((n) => n + 1)}
                className="text-sm text-cyan-300 underline hover:text-cyan-200"
              >
                Try the camera again
              </button>
            </div>
          )}

          {/* Bottom glass sheet — the single Finish for this screen */}
          <div className="flex flex-1 flex-col items-center gap-4 border-t border-white/10 bg-white/5 p-5 pb-9 backdrop-blur-xl">
            <p className="text-[13px] font-medium uppercase tracking-[0.2em] text-slate-400" role="status">
              {slabs.length === 0
                ? 'No slabs captured yet'
                : `${slabs.length} slab${slabs.length === 1 ? '' : 's'} captured`}
            </p>
            {chips}
            {manualRow}
            {error && (
              <p role="alert" className="anim-slide-left max-w-sm text-center text-sm text-red-400">
                {error}
              </p>
            )}
            {busy && <p className="text-sm text-cyan-300" role="status">{busy}</p>}
            {finishButton()}
          </div>

          {/* Capture confirmation: FULLSCREEN, so its Finish is the only one.
              Glides in on capture, sinks out on dismiss. */}
          {captured && (
            <div className={`fixed inset-0 z-40 flex flex-col items-center justify-center gap-5 bg-slate-950/85 p-6 backdrop-blur-md ${
              dismissing ? 'anim-overlay-out' : 'anim-fade-in'
            }`}>
              <div className={`flex w-full max-w-sm flex-col items-center gap-2 rounded-3xl border border-emerald-400/40 bg-emerald-400/10 px-8 py-7 shadow-2xl shadow-emerald-500/20 backdrop-blur-xl ${
                dismissing ? 'anim-sink-out' : 'anim-slide-right'
              }`}>
                <span
                  className="flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-br from-emerald-400 to-teal-500 text-3xl text-white"
                  style={{ filter: 'drop-shadow(0 0 14px rgba(52,211,153,0.8))' }}
                >
                  ✓
                </span>
                <span className="mt-1 text-[12px] font-semibold uppercase tracking-[0.3em] text-emerald-300">
                  Slab registered
                </span>
                {captured.map((id) => (
                  <span
                    key={id}
                    className="font-mono text-4xl font-bold tracking-[0.15em] text-white"
                    style={{ textShadow: '0 0 18px rgba(52,211,153,0.6)' }}
                  >
                    {id}
                  </span>
                ))}
              </div>

              {/* Optional: tag the material now, or leave it for the card */}
              <div className="anim-slide-right relative z-30 w-full max-w-xs" style={{ animationDelay: '0.03s' }}>
                {capturedMaterial ? (
                  <button
                    type="button"
                    onClick={() => setCapturedMaterial(null)}
                    className="flex w-full items-center justify-between rounded-2xl border border-emerald-400/40 bg-emerald-400/10 px-4 py-3 text-left"
                  >
                    <span className="text-[15px] font-semibold text-emerald-200">
                      {capturedMaterial}
                    </span>
                    <span className="text-xs text-emerald-300/70 underline">change</span>
                  </button>
                ) : (
                  <>
                    <p className="mb-1.5 text-center text-[12px] uppercase tracking-[0.2em] text-slate-400">
                      Material (optional)
                    </p>
                    <MaterialPicker
                      onPick={(name) => applyMaterialToCaptured(name)}
                    />
                  </>
                )}
              </div>

              <button
                type="button"
                onClick={dismissCapture}
                className={`min-h-[58px] w-full max-w-xs rounded-2xl bg-gradient-to-r from-cyan-500 to-blue-600 text-[17px] font-semibold text-white shadow-lg shadow-cyan-500/25 transition-transform hover:scale-[1.02] ${
                  dismissing ? 'anim-sink-out' : 'anim-slide-right'
                }`}
                style={dismissing ? undefined : { animationDelay: '0.05s' }}
              >
                Scan next slab
              </button>
              <button
                type="button"
                onClick={() => void finish()}
                disabled={busy !== null}
                className={`min-h-[54px] w-full max-w-xs rounded-2xl border border-white/15 bg-white/5 text-[16px] font-semibold text-slate-100 backdrop-blur transition-colors hover:border-emerald-400/60 disabled:opacity-50 ${
                  dismissing ? 'anim-sink-out' : 'anim-slide-right'
                }`}
                style={dismissing ? undefined : { animationDelay: '0.1s' }}
              >
                Finish — {slabs.length} slab{slabs.length === 1 ? '' : 's'}
              </button>
            </div>
          )}

          {/* Duplicate: this slab is already on another card. Blocked. */}
          {duplicate && (
            <div className="anim-fade-in fixed inset-0 z-40 flex flex-col items-center justify-center gap-5 bg-slate-950/85 p-6 backdrop-blur-md">
              <div className="anim-slide-right flex w-full max-w-sm flex-col items-center gap-2 rounded-3xl border border-amber-400/40 bg-amber-400/10 px-8 py-7 text-center shadow-2xl shadow-amber-500/20 backdrop-blur-xl">
                <span
                  className="flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-br from-amber-400 to-orange-500 text-3xl text-white"
                  style={{ filter: 'drop-shadow(0 0 14px rgba(251,191,36,0.8))' }}
                >
                  ⚠
                </span>
                <span className="mt-1 text-[12px] font-semibold uppercase tracking-[0.3em] text-amber-300">
                  Already scanned
                </span>
                {duplicate.map((id) => (
                  <span
                    key={id}
                    className="font-mono text-4xl font-bold tracking-[0.15em] text-white"
                  >
                    {id}
                  </span>
                ))}
                <span className="mt-1 text-[13px] text-slate-300">
                  This slab is already on another card — not added.
                </span>
              </div>
              <button
                type="button"
                onClick={() => {
                  primeAudio()
                  setDuplicate(null)
                }}
                className="anim-slide-right min-h-[58px] w-full max-w-xs rounded-2xl bg-gradient-to-r from-cyan-500 to-blue-600 text-[17px] font-semibold text-white shadow-lg shadow-cyan-500/25"
                style={{ animationDelay: '0.05s' }}
              >
                Keep scanning
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  ), document.body)
}
