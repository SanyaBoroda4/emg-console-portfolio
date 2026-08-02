import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { uploadCheck, uploadDelivery } from '../api'
import { toJpegSafe } from '../lib/image'

// Capture-guide aspect per document: a US personal check (~2.2:1) vs a
// portrait letter-size delivery slip (~0.77:1).
const GUIDE_ASPECTS = { check: 2.2, delivery: 0.77 } as const
export type CaptureVariant = keyof typeof GUIDE_ASPECTS
const JPEG_QUALITY = 0.9

// --- Auto-capture tuning ---------------------------------------------------
// The analyzer samples the guide area 10×/second and fires when the frame
// looks like a CHECK, not just any scene: SHARP (Laplacian variance — focused
// text has strong edges), PAPER-LIKE (bright, mostly light pixels — desks and
// rooms fail this), and reasonably STILL. Big motion (rotating the phone)
// hard-resets the countdown, and nothing can fire in the first TWO seconds
// after the camera opens (autofocus settle time). After that, ~0.4s of good
// framing takes the shot.
const ANALYZE_INTERVAL_MS = 100
const REQUIRED_STEADY_SAMPLES = 4
const SHARPNESS_MIN = 140 // Laplacian variance; blurry/empty scenes sit well below
const MOTION_MAX = 14 // mean per-pixel gray delta between samples
const BIG_MOTION = 30 // above this (rotation, swings) the countdown restarts
const MIN_BRIGHTNESS = 95 // mean gray — paper is bright
const MIN_BRIGHT_RATIO = 0.3 // ≥30% of pixels clearly light (gray > 150)
// 2s: give the phone's autofocus a real chance to settle before the analyzer
// may fire — 1s produced occasional blurry captures (owner feedback 2026-07-18).
const STARTUP_GRACE_MS = 2000
const SAMPLE_W = 176
const SAMPLE_H = 80

type CameraState = 'starting' | 'ready' | 'unavailable'

/** Map the on-screen guide rectangle to video source pixels (object-cover). */
function computeSourceRect(video: HTMLVideoElement, guide: HTMLElement) {
  const videoRect = video.getBoundingClientRect()
  const guideRect = guide.getBoundingClientRect()
  const scale = Math.max(
    videoRect.width / video.videoWidth,
    videoRect.height / video.videoHeight,
  )
  // How much of the (scaled) source is cropped off each edge by cover.
  const offsetX = (video.videoWidth * scale - videoRect.width) / 2
  const offsetY = (video.videoHeight * scale - videoRect.height) / 2
  const sx = Math.max(0, (guideRect.left - videoRect.left + offsetX) / scale)
  const sy = Math.max(0, (guideRect.top - videoRect.top + offsetY) / scale)
  const sw = Math.min(guideRect.width / scale, video.videoWidth - sx)
  const sh = Math.min(guideRect.height / scale, video.videoHeight - sy)
  return { sx, sy, sw, sh }
}

/** Sharpness (Laplacian variance) and motion (mean abs diff vs previous). */
function analyzeFrame(
  gray: Float32Array,
  prev: Float32Array | null,
): { sharpness: number; motion: number } {
  let motion = Number.POSITIVE_INFINITY
  if (prev) {
    let sum = 0
    for (let i = 0; i < gray.length; i++) sum += Math.abs(gray[i] - prev[i])
    motion = sum / gray.length
  }
  let mean = 0
  const lap = new Float32Array(gray.length)
  let count = 0
  for (let y = 1; y < SAMPLE_H - 1; y++) {
    for (let x = 1; x < SAMPLE_W - 1; x++) {
      const i = y * SAMPLE_W + x
      const v = 4 * gray[i] - gray[i - 1] - gray[i + 1] - gray[i - SAMPLE_W] - gray[i + SAMPLE_W]
      lap[i] = v
      mean += v
      count++
    }
  }
  mean /= count
  let variance = 0
  for (let y = 1; y < SAMPLE_H - 1; y++) {
    for (let x = 1; x < SAMPLE_W - 1; x++) {
      const i = y * SAMPLE_W + x
      variance += (lap[i] - mean) ** 2
    }
  }
  return { sharpness: variance / count, motion }
}

export default function SubmitCheckPage({
  variant = 'check',
}: {
  variant?: CaptureVariant
}) {
  const GUIDE_ASPECT = GUIDE_ASPECTS[variant]
  const navigate = useNavigate()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const guideRef = useRef<HTMLDivElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)

  const [camera, setCamera] = useState<CameraState>('starting')
  // Deliveries first offer a choice (camera vs gallery); checks open the
  // camera immediately (owner preference 2026-07-22).
  const [stage, setStage] = useState<'choose' | 'camera'>(
    variant === 'delivery' ? 'choose' : 'camera',
  )
  const [isPortrait, setIsPortrait] = useState(false)
  const [photo, setPhoto] = useState<Blob | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  // Fast path (decision flow §3): 4 digits here = the workflow auto-matches
  // the job and no question is ever asked.
  const [qbInvoice, setQbInvoice] = useState('')
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const exit = useCallback(
    () => navigate(variant === 'delivery' ? '/deliveries' : '/payments'),
    [navigate, variant],
  )

  // Esc exits cleanly (Back button works because this is a real route).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') exit()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [exit])

  // Orientation is only a HINT (never a gate): iOS freezes screen
  // orientation while the phone points down at a table, so demanding
  // landscape forces an awkward point-ahead-rotate-then-point-down dance.
  // The crop is always exactly what's inside the frame, so alignment
  // self-corrects — the user just turns the phone until the check fits.
  useEffect(() => {
    const mq = window.matchMedia('(orientation: portrait)')
    setIsPortrait(mq.matches)
    const onChange = (event: MediaQueryListEvent) => setIsPortrait(event.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // Bumping this re-runs camera acquisition (the "Try again" button on the
  // fallback screen — after a Register round-trip iOS may need a moment).
  const [cameraNonce, setCameraNonce] = useState(0)
  // Last getUserMedia failure reason, surfaced on the fallback screen so a
  // report from the field says WHY (busy hardware vs denied permission).
  const [cameraError, setCameraError] = useState<string | null>(null)

  // Camera lifecycle: acquire when the camera stage opens, ALWAYS stop
  // tracks on unmount/stage exit.
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
        // iOS keeps the hardware busy for a few seconds after the previous
        // capture session; late attempts drop to bare constraints, which
        // succeed in cases where the 4K request keeps failing.
        const constraints: MediaStreamConstraints =
          attempt < 5
            ? {
                video: {
                  facingMode: 'environment',
                  width: { ideal: 3840 },
                  height: { ideal: 2160 },
                },
                audio: false,
              }
            : { video: { facingMode: 'environment' }, audio: false }
        const stream = await navigator.mediaDevices.getUserMedia(constraints)
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop())
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
        if (!cancelled) setCameraError(name || String(err))
        // A real permission denial won't fix itself — fall back right away.
        if (name === 'NotAllowedError' || name === 'SecurityError') {
          if (!cancelled) setCamera('unavailable')
          return
        }
        // NotReadableError etc.: the camera is still being released — keep
        // retrying for ~5.5s before giving up.
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
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
  }, [stage, cameraNonce])

  // (Re)attach the stream whenever the <video> element (re)mounts — it
  // unmounts during preview and returns on Retake.
  const attachVideo = useCallback((node: HTMLVideoElement | null) => {
    videoRef.current = node
    if (node && streamRef.current) {
      node.srcObject = streamRef.current
      void node.play()
    }
  }, [])

  // Object URL for the preview image; revoke the old one on change/unmount.
  useEffect(() => {
    if (!photo) {
      setPreviewUrl(null)
      return
    }
    const url = URL.createObjectURL(photo)
    setPreviewUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [photo])

  // Crop the live frame to the guide rectangle at full resolution.
  const capture = useCallback(() => {
    const video = videoRef.current
    const guide = guideRef.current
    if (!video || !guide || video.videoWidth === 0) return
    const { sx, sy, sw, sh } = computeSourceRect(video, guide)
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(sw)
    canvas.height = Math.round(sh)
    const context = canvas.getContext('2d')
    if (!context) return
    context.drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height)

    // Tall-guide capture means the phone was physically sideways while the
    // frozen UI stayed portrait — straighten the photo. We assume the more
    // common counterclockwise turn; the preview's Rotate button fixes a
    // wrong guess in one tap.
    let output = canvas
    if (isPortrait) {
      const rotated = document.createElement('canvas')
      rotated.width = canvas.height
      rotated.height = canvas.width
      const rotatedContext = rotated.getContext('2d')
      if (rotatedContext) {
        rotatedContext.translate(0, rotated.height)
        rotatedContext.rotate(-Math.PI / 2)
        rotatedContext.drawImage(canvas, 0, 0)
        output = rotated
      }
    }
    output.toBlob(
      (blob) => {
        if (blob) setPhoto(blob)
      },
      'image/jpeg',
      JPEG_QUALITY,
    )
  }, [isPortrait])

  // Preview escape hatch: spin the shot 90° clockwise per tap.
  async function rotatePhoto() {
    if (!photo) return
    const bitmap = await createImageBitmap(photo)
    const canvas = document.createElement('canvas')
    canvas.width = bitmap.height
    canvas.height = bitmap.width
    const context = canvas.getContext('2d')
    if (!context) return
    context.translate(canvas.width, 0)
    context.rotate(Math.PI / 2)
    context.drawImage(bitmap, 0, 0)
    canvas.toBlob(
      (blob) => {
        if (blob) setPhoto(blob)
      },
      'image/jpeg',
      JPEG_QUALITY,
    )
  }

  // Auto-capture: watch the guide area; fire when sharp + steady long enough.
  const [steady, setSteady] = useState(0) // 0..REQUIRED_STEADY_SAMPLES
  useEffect(() => {
    if (camera !== 'ready' || photo !== null) {
      setSteady(0)
      return
    }
    const canvas = document.createElement('canvas')
    canvas.width = SAMPLE_W
    canvas.height = SAMPLE_H
    const context = canvas.getContext('2d', { willReadFrequently: true })
    if (!context) return

    let prev: Float32Array | null = null
    let streak = 0
    const startedAt = performance.now()
    const timer = window.setInterval(() => {
      const video = videoRef.current
      const guide = guideRef.current
      if (!video || !guide || video.videoWidth === 0) return
      const { sx, sy, sw, sh } = computeSourceRect(video, guide)
      context.drawImage(video, sx, sy, sw, sh, 0, 0, SAMPLE_W, SAMPLE_H)
      const rgba = context.getImageData(0, 0, SAMPLE_W, SAMPLE_H).data
      const gray = new Float32Array(SAMPLE_W * SAMPLE_H)
      let brightnessSum = 0
      let brightCount = 0
      for (let i = 0; i < gray.length; i++) {
        const j = i * 4
        const g = rgba[j] * 0.299 + rgba[j + 1] * 0.587 + rgba[j + 2] * 0.114
        gray[i] = g
        brightnessSum += g
        if (g > 150) brightCount++
      }
      const meanBrightness = brightnessSum / gray.length
      const brightRatio = brightCount / gray.length
      const { sharpness, motion } = analyzeFrame(gray, prev)
      prev = gray

      // Never fire while the camera is settling or the phone is being
      // rotated/swung — big motion restarts the countdown outright.
      if (performance.now() - startedAt < STARTUP_GRACE_MS || motion > BIG_MOTION) {
        streak = 0
        setSteady(0)
        return
      }

      // Paper gate: a check fills the frame with bright paper. Desks,
      // floors, and rooms are darker/less uniform and must not trigger.
      const paperLike =
        meanBrightness >= MIN_BRIGHTNESS && brightRatio >= MIN_BRIGHT_RATIO

      // Leaky bucket: a good sample climbs, a bad one only dips — a single
      // wobble doesn't restart the countdown from zero.
      streak =
        paperLike && sharpness >= SHARPNESS_MIN && motion <= MOTION_MAX
          ? streak + 1
          : Math.max(0, streak - 1)
      setSteady(Math.min(streak, REQUIRED_STEADY_SAMPLES))
      if (streak >= REQUIRED_STEADY_SAMPLES) {
        window.clearInterval(timer)
        capture()
      }
    }, ANALYZE_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [camera, photo, capture])

  async function usePhoto() {
    if (!photo) return
    if (variant === 'check' && qbInvoice && qbInvoice.length !== 4) {
      setError('QB invoice # must be exactly 4 digits — or leave it empty.')
      return
    }
    setUploading(true)
    setError(null)
    setProgress(0)
    try {
      const item =
        variant === 'delivery'
          ? await uploadDelivery(photo, setProgress)
          : await uploadCheck(photo, setProgress, qbInvoice || undefined)
      // Release the camera BEFORE navigating — iOS is slow to free it and a
      // quick "Submit check" re-entry would find it still busy.
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
      // Land on the new card: the submitter watches CHECK-BOT work live
      // (5s poll) instead of waiting for a push notification.
      navigate(variant === 'delivery' ? `/deliveries/item/${item.id}` : `/payments/item/${item.id}`)
    } catch (err) {
      // Keep the photo in memory so the manager doesn't re-shoot.
      setError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setUploading(false)
    }
  }

  function onFilePicked(files: FileList | null) {
    const file = files?.[0]
    if (file) {
      setError(null)
      void toJpegSafe(file, 3000).then(setPhoto)
    }
  }

  return createPortal((
    <div className="fixed inset-0 z-50 overflow-hidden bg-black">
      <button
        type="button"
        onClick={exit}
        aria-label="Close"
        className="absolute left-4 top-4 z-30 flex h-11 w-11 items-center justify-center rounded-full bg-white/10 text-2xl leading-none text-white hover:bg-white/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
      >
        ×
      </button>

      {stage === 'choose' && !photo ? (
        /* ---------- Delivery chooser: camera or gallery ---------- */
        <div className="flex h-full flex-col items-center justify-center gap-4 p-6">
          <p className="text-lg font-semibold text-white">Add the delivery slip</p>
          <button
            type="button"
            onClick={() => setStage('camera')}
            className="min-h-[56px] w-full max-w-xs rounded-xl bg-blue-600 text-[16px] font-semibold text-white hover:bg-blue-700"
          >
            Open camera
          </button>
          <label className="flex min-h-[56px] w-full max-w-xs cursor-pointer items-center justify-center rounded-xl border-2 border-white/40 text-[16px] font-semibold text-white hover:border-white">
            Upload from gallery
            <input
              type="file"
              accept="image/*"
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void toJpegSafe(file, 3000).then(setPhoto)
                e.target.value = ''
              }}
            />
          </label>
          <button
            type="button"
            onClick={exit}
            className="mt-2 text-sm text-white/60 underline hover:text-white"
          >
            Cancel
          </button>
        </div>
      ) : photo && previewUrl ? (
        /* ---------- Preview: Retake / Use photo ---------- */
        <div className="flex h-full flex-col items-center justify-center gap-4 p-4">
          <img
            src={previewUrl}
            alt="Captured check preview"
            className="max-h-[70vh] max-w-full rounded-md object-contain"
          />

          {/* QB invoice fast path: 4 digits = auto-match, no question asked.
              Checks only — delivery slips assign jobs on the card. */}
          {!uploading && variant === 'check' && (
            <label className="flex flex-col items-center gap-1.5">
              <span className="text-sm font-medium text-white/80">
                QB invoice # <span className="font-normal text-white/50">(optional)</span>
              </span>
              <input
                type="text"
                inputMode="numeric"
                autoComplete="off"
                placeholder="••••"
                maxLength={4}
                value={qbInvoice}
                onChange={(e) => setQbInvoice(e.target.value.replace(/\D/g, '').slice(0, 4))}
                className="min-h-12 w-36 rounded-lg border border-white/30 bg-white/10 text-center text-xl font-semibold tracking-[0.4em] text-white placeholder:text-white/30 focus:border-blue-400 focus:outline-none"
              />
            </label>
          )}

          {error && (
            <div role="alert" className="max-w-md rounded-lg bg-red-50 p-4 text-center">
              <p className="text-sm font-medium text-red-800">{error}</p>
              <p className="mt-1 text-xs text-red-700">
                Your photo is safe — no need to re-shoot.
              </p>
            </div>
          )}

          {uploading ? (
            <div className="flex w-64 flex-col items-center gap-2" role="status">
              <div className="h-2 w-full overflow-hidden rounded-full bg-white/20">
                <div
                  className="h-full rounded-full bg-blue-500"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <p className="text-sm text-white">Uploading… {progress}%</p>
            </div>
          ) : (
            <div className="flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={() => {
                  setPhoto(null)
                  setError(null)
                }}
                className="min-h-11 rounded-md border border-white/40 px-6 font-medium text-white hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
              >
                Retake
              </button>
              <button
                type="button"
                onClick={() => void rotatePhoto()}
                className="min-h-11 rounded-md border border-white/40 px-6 font-medium text-white hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
              >
                Rotate ⟳
              </button>
              <button
                type="button"
                onClick={() => void usePhoto()}
                className="min-h-11 rounded-md bg-blue-600 px-6 font-medium text-white hover:bg-blue-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
              >
                {error ? 'Retry upload' : 'Use photo'}
              </button>
            </div>
          )}
        </div>
      ) : camera === 'ready' ? (
        /* ---------- Live viewfinder ---------- */
        <div className="relative h-full w-full">
          <video
            ref={attachVideo}
            autoPlay
            playsInline
            muted
            className="absolute inset-0 h-full w-full object-cover"
          />

          {/* Non-blocking tip — never a gate (see orientation comment above).
              Checks only: the A4 slip frame is upright, nothing to rotate. */}
          {isPortrait && variant === 'check' && (
            <p className="absolute left-1/2 top-5 z-10 w-11/12 max-w-sm -translate-x-1/2 rounded-md bg-black/60 px-3 py-2 text-center text-sm text-white">
              The frame is already sideways — just rotate the phone over the check
            </p>
          )}

          {/* Guide rectangle; the huge shadow dims everything outside it.
              While the screen reports portrait, the frame is drawn ALREADY
              ROTATED (tall on screen): turn the phone flat over the check
              and the frame lands landscape in the real world — no waiting
              for iOS to notice the rotation (it can't while pointing down).
              The border turns green as the auto-capture locks on. */}
          <div
            ref={guideRef}
            className={`absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 rounded-lg border-2 ${
              steady > 0 ? 'border-green-400' : 'border-white'
            }`}
            style={
              variant === 'delivery'
                ? // A4 slip: portrait document, no rotation trick — the frame
                  // fills most of the screen in the document's own orientation.
                  isPortrait
                  ? {
                      width: `min(92vw, calc(82vh * ${GUIDE_ASPECT}))`,
                      aspectRatio: `${GUIDE_ASPECT} / 1`,
                      boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.5)',
                    }
                  : {
                      width: `min(50vw, calc(85vh * ${GUIDE_ASPECT}))`,
                      aspectRatio: `${GUIDE_ASPECT} / 1`,
                      boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.5)',
                    }
                : isPortrait
                  ? {
                      width: `min(85vw, calc(75vh / ${GUIDE_ASPECT}))`,
                      aspectRatio: `1 / ${GUIDE_ASPECT}`,
                      boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.5)',
                    }
                  : {
                      width: `min(92vw, calc(70vh * ${GUIDE_ASPECT}))`,
                      aspectRatio: `${GUIDE_ASPECT} / 1`,
                      boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.5)',
                    }
            }
          />
          <div className="absolute bottom-16 left-1/2 z-10 flex w-64 -translate-x-1/2 flex-col items-center gap-2">
            <p className="text-sm font-medium text-white drop-shadow" role="status">
              {steady === 0
                ? `Fit the ${variant === 'delivery' ? 'slip' : 'check'} inside the frame`
                : 'Hold steady — capturing…'}
            </p>
            {/* Lock-on progress; full bar = photo taken automatically. */}
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/20">
              <div
                className="h-full rounded-full bg-green-400"
                style={{ width: `${(steady / REQUIRED_STEADY_SAMPLES) * 100}%` }}
              />
            </div>
          </div>
          <div className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-5">
            <button
              type="button"
              onClick={capture}
              className="text-sm text-white/70 underline hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
            >
              Capture manually
            </button>
            <label className="cursor-pointer text-sm text-white/70 underline hover:text-white">
              Upload from gallery
              <input
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) void toJpegSafe(file, 3000).then(setPhoto)
                  e.target.value = '' // allow re-picking the same file
                }}
              />
            </label>
          </div>
        </div>
      ) : camera === 'starting' ? (
        <div className="flex h-full items-center justify-center">
          <p className="text-white">Starting camera…</p>
        </div>
      ) : (
        /* ---------- Fallback: native camera app via file input ---------- */
        <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
          <p className="max-w-sm text-white">
            The in-app camera isn&apos;t available (no camera, or permission was
            denied). Use your phone&apos;s camera app instead:
          </p>
          <label className="inline-flex min-h-11 cursor-pointer items-center rounded-md bg-blue-600 px-6 font-medium text-white hover:bg-blue-700 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-white">
            Take photo
            <input
              type="file"
              accept="image/*"
              capture="environment"
              className="sr-only"
              onChange={(event) => onFilePicked(event.target.files)}
            />
          </label>
          <label className="cursor-pointer text-sm text-white/70 underline hover:text-white">
            Upload from gallery
            <input
              type="file"
              accept="image/*"
              className="sr-only"
              onChange={(event) => onFilePicked(event.target.files)}
            />
          </label>
          <button
            type="button"
            onClick={() => setCameraNonce((n) => n + 1)}
            className="mt-1 text-sm text-white/70 underline hover:text-white"
          >
            Try the in-app camera again
          </button>
          {cameraError && (
            <p className="text-xs text-white/40">reason: {cameraError}</p>
          )}
        </div>
      )}
    </div>
  ), document.body)
}
