import { useRef, useState } from 'react'
import type { ReviewItem } from '../types'
import { driveThumbUrl, extractDriveFileId } from '../lib/driveImage'
import Lightbox from './Lightbox'

// Shown when a payment has no check photo at all — those are the ones that
// arrived electronically, so the QuickBooks mark explains the absence.
// Drawn inline (green circle, white "qb") — no external assets allowed.
export function QuickBooksMark({ className }: { className?: string }) {
  return (
    <span
      title="No check photo — payment recorded via QuickBooks"
      className={`flex items-center justify-center bg-gray-50 ${className ?? 'h-full w-full'}`}
    >
      <svg role="img" aria-label="QuickBooks" viewBox="0 0 48 48" className="h-12 w-12">
        <circle cx="24" cy="24" r="22" fill="#2CA01C" />
        <text
          x="24"
          y="25"
          textAnchor="middle"
          dominantBaseline="central"
          fill="#ffffff"
          fontFamily="Arial, Helvetica, sans-serif"
          fontSize="19"
          fontWeight="700"
          letterSpacing="-0.5"
        >
          qb
        </text>
      </svg>
    </span>
  )
}

export function PhotoPlaceholder({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`flex items-center justify-center bg-gray-100 text-gray-400 ${className ?? 'h-full w-full'}`}
    >
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <circle cx="9" cy="10" r="2" />
        <path d="m3 17 5-5 4 4 3-3 6 6" />
      </svg>
    </span>
  )
}

interface Props {
  item: ReviewItem
  /** Drive thumbnail width hint: 200 for cards, 120 for table rows. */
  width?: number
  /** Size/shape classes for the thumbnail box. */
  className?: string
}

export default function CheckThumb({ item, width = 200, className }: Props) {
  const [thumbFailed, setThumbFailed] = useState(false)
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)

  // Console-captured photos win; Drive links keep working as before.
  const consoleSrc = item.photo_path ? `/api/photos/${item.id}` : null
  const driveId =
    !consoleSrc && item.photo_drive_url ? extractDriveFileId(item.photo_drive_url) : null
  const thumbSrc = consoleSrc ?? (driveId ? driveThumbUrl(driveId, width) : null)
  const fullSrc = consoleSrc ?? (driveId ? driveThumbUrl(driveId, 1600) : null)

  if (!consoleSrc && !item.photo_drive_url) {
    return <QuickBooksMark className={`rounded-md ${className ?? ''}`} />
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-label="View check photo"
        className={`block overflow-hidden rounded-md hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 ${className ?? ''}`}
      >
        {thumbSrc && !thumbFailed ? (
          <img
            src={thumbSrc}
            alt="check photo"
            loading="lazy"
            onError={() => setThumbFailed(true)}
            className="h-full w-full object-cover"
          />
        ) : (
          // Broken/unshared preview degrades to the icon but stays clickable.
          <PhotoPlaceholder />
        )}
      </button>
      {open && (
        <Lightbox
          imageUrl={fullSrc}
          driveUrl={item.photo_drive_url}
          onClose={() => {
            setOpen(false)
            triggerRef.current?.focus() // return focus to the trigger
          }}
        />
      )}
    </>
  )
}
