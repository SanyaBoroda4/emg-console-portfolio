/** Re-encode any picked photo (HEIC included — Safari decodes it natively)
 * as a downscaled JPEG. Gallery inputs use accept="image/*" so iOS opens
 * the photo library directly instead of asking "Library / Camera / Files";
 * the price is that iPhones may hand over HEIC, which the backend (PIL)
 * and Claude can't read — so everything becomes JPEG right here. */
export async function toJpeg(file: Blob, maxSide = 1568): Promise<Blob> {
  const bitmap = await createImageBitmap(file)
  try {
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    const ctx = canvas.getContext('2d')
    if (!ctx) return file
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error('encode failed'))),
        'image/jpeg',
        0.85,
      ),
    )
  } finally {
    bitmap.close()
  }
}

/** toJpeg, but never throws — falls back to the original blob. */
export async function toJpegSafe(file: Blob, maxSide = 1568): Promise<Blob> {
  try {
    return await toJpeg(file, maxSide)
  } catch {
    return file
  }
}
