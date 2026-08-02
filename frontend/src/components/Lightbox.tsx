import { useEffect, useRef, useState } from 'react'

interface Props {
  /** Full-size image URL, or null when nothing displayable exists. */
  imageUrl: string | null
  /** Original photo_drive_url escape hatch; null for console-captured photos. */
  driveUrl: string | null
  onClose: () => void
}

export default function Lightbox({ imageUrl, driveUrl, onClose }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(imageUrl === null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Check photo"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
    >
      <button
        ref={closeRef}
        type="button"
        onClick={onClose}
        aria-label="Close photo"
        className="absolute right-4 top-4 flex h-11 w-11 items-center justify-center rounded-full bg-white/10 text-2xl leading-none text-white hover:bg-white/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
      >
        ×
      </button>

      {/* Clicks inside the content shouldn't close the dialog. */}
      <div
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-full max-w-full flex-col items-center gap-3"
      >
        {failed ? (
          <div className="rounded-lg bg-white p-8 text-center">
            <p className="font-medium text-gray-700">Preview unavailable</p>
            {driveUrl && (
              <a
                href={driveUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-flex min-h-11 items-center text-blue-700 underline hover:text-blue-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              >
                Open in Google Drive ↗
              </a>
            )}
          </div>
        ) : (
          <>
            {!loaded && (
              <div
                aria-label="Loading photo"
                role="status"
                className="h-8 w-8 animate-spin rounded-full border-2 border-white/30 border-t-white"
              />
            )}
            <img
              src={imageUrl ?? undefined}
              alt="Check photo, full size"
              onLoad={() => setLoaded(true)}
              onError={() => setFailed(true)}
              className={`max-h-[85vh] max-w-full rounded-md object-contain ${loaded ? '' : 'hidden'}`}
            />
            {driveUrl && (
              <a
                href={driveUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-h-11 items-center text-sm text-white underline hover:text-gray-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
              >
                Open in Drive ↗
              </a>
            )}
          </>
        )}
      </div>
    </div>
  )
}
