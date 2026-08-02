// QR decoding for slab labels (slab scans chapter). Two engines:
//  1. The platform's native BarcodeDetector when available (iOS Safari
//     exposes the same Vision engine the Camera app uses — best for live
//     frames by far).
//  2. zxing-wasm as the universal fallback, bundled by Vite (no CDN; the
//     PWA must not depend on one).

import { prepareZXingModule, readBarcodes } from 'zxing-wasm/reader'
import wasmUrl from 'zxing-wasm/reader/zxing_reader.wasm?url'

prepareZXingModule({
  overrides: {
    locateFile: (path: string, prefix: string) =>
      path.endsWith('.wasm') ? wasmUrl : prefix + path,
  },
})

const ZXING_OPTS = {
  formats: ['QRCode' as const],
  tryHarder: true,
  maxNumberOfSymbols: 8,
}

type NativeDetector = {
  detect: (source: CanvasImageSource) => Promise<{ rawValue: string }[]>
}

let nativeDetector: NativeDetector | null = null
try {
  const BD = (window as unknown as {
    BarcodeDetector?: new (opts: { formats: string[] }) => NativeDetector
  }).BarcodeDetector
  if (BD) nativeDetector = new BD({ formats: ['qr_code'] })
} catch {
  nativeDetector = null
}

/** Slab IDs are plain digit runs (e.g. "2287478"). */
function onlyIds(texts: string[]): string[] {
  return texts.filter((t) => /^\d{5,9}$/.test(t.trim())).map((t) => t.trim())
}

async function decodeCanvas(canvas: HTMLCanvasElement): Promise<string[]> {
  // Native first — on iPhone it reads codes zxing misses on live frames.
  if (nativeDetector) {
    try {
      const hits = await nativeDetector.detect(canvas)
      const ids = onlyIds(hits.map((h) => h.rawValue))
      if (ids.length > 0) return ids
    } catch {
      // fall through to zxing
    }
  }
  const ctx = canvas.getContext('2d')
  if (!ctx) return []
  const results = await readBarcodes(
    ctx.getImageData(0, 0, canvas.width, canvas.height),
    ZXING_OPTS,
  )
  return onlyIds(results.map((r) => r.text))
}

function draw(
  source: CanvasImageSource,
  sx: number,
  sy: number,
  sw: number,
  sh: number,
  maxSide: number,
): HTMLCanvasElement {
  const scale = Math.min(1, maxSide / Math.max(sw, sh))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(sw * scale))
  canvas.height = Math.max(1, Math.round(sh * scale))
  canvas
    .getContext('2d')
    ?.drawImage(source, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height)
  return canvas
}

/** Decode every QR in a picked photo. Multi-scale passes — on the real
 * test labels the rescaled passes rescued shots the first one missed.
 * Returns [] when nothing decodes (caller falls back to OCR). */
export async function decodeLabelPhoto(file: Blob): Promise<string[]> {
  const bitmap = await createImageBitmap(file)
  try {
    const found = new Set<string>()
    for (const maxSide of [2600, 1600, 1000]) {
      const canvas = draw(bitmap, 0, 0, bitmap.width, bitmap.height, maxSide)
      const ids = await decodeCanvas(canvas)
      ids.forEach((id) => found.add(id))
      if (found.size > 0) break
    }
    return [...found]
  } finally {
    bitmap.close()
  }
}

/** Decode a live camera frame: full frame at high detail, then a center
 * crop at native resolution — catches labels held further away, where the
 * QR is only a small part of the frame. */
export async function decodeVideoFrame(video: HTMLVideoElement): Promise<string[]> {
  const vw = video.videoWidth
  const vh = video.videoHeight
  if (!vw || !vh) return []

  // Pass 1: whole frame, generous detail budget.
  const full = draw(video, 0, 0, vw, vh, 1440)
  const ids = await decodeCanvas(full)
  if (ids.length > 0) return ids

  // Pass 2: center crop (the guide square area) at native resolution.
  const side = Math.round(Math.min(vw, vh) * 0.7)
  const crop = draw(
    video,
    Math.round((vw - side) / 2),
    Math.round((vh - side) / 2),
    side,
    side,
    1200,
  )
  return decodeCanvas(crop)
}

/** True when the platform's native detector is active (diagnostics). */
export function usingNativeDetector(): boolean {
  return nativeDetector !== null
}
