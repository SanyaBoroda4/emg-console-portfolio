import { useRef, useState } from 'react'
import type { TouchEvent } from 'react'
import type { ReviewItem } from '../types'
import { formatAmount, formatDateish, relativeTime, statusBadgeClass } from '../lib/format'
import CheckThumb from './CheckThumb'
import EditedChip from './EditedChip'
import NeedsDecisionChip from './NeedsDecisionChip'
import ProcessingChip from './ProcessingChip'

// How far (px) the swipe-left gesture reveals the Delete button.
const SWIPE_OPEN_PX = 96

export function TrashIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  )
}

export function PencilIcon({ className }: { className?: string }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3Z" />
    </svg>
  )
}

// The payment's own date — never the mirror's ingestion time, which reads
// as "when the payment happened" and isn't.
function paymentDateLabel(item: ReviewItem): string {
  const details = item.payment_details
  const paid = formatDateish(details?.txn_date)
  if (paid) return `Paid ${paid}`
  const received = formatDateish(details?.date_received)
  if (received) return `Received ${received}`
  return `Added ${relativeTime(item.created_at)}`
}

export default function PaymentCard({
  item,
  onDelete,
  onEditAmount,
  needsDecision = false,
}: {
  item: ReviewItem
  /** Absent for non-admin roles — no swipe, no trash. */
  onDelete?: () => void
  /** The quick amount-fix pencil (manager + admin). */
  onEditAmount?: () => void
  /** Open bot question → the amber chip linking to the decision card. */
  needsDecision?: boolean
}) {
  // Swipe-left-to-delete (touch devices).
  const [dragX, setDragX] = useState(0)
  const [swipeOpen, setSwipeOpen] = useState(false)
  const touchStart = useRef<{ x: number; y: number } | null>(null)
  const dragging = useRef(false)

  function onTouchStart(event: TouchEvent) {
    if (!onDelete) return
    touchStart.current = { x: event.touches[0].clientX, y: event.touches[0].clientY }
    dragging.current = false
  }

  function onTouchMove(event: TouchEvent) {
    if (!touchStart.current) return
    const dx = event.touches[0].clientX - touchStart.current.x
    const dy = event.touches[0].clientY - touchStart.current.y
    if (!dragging.current) {
      // Vertical intent = the user is scrolling the list; leave it alone.
      if (Math.abs(dx) < 10 || Math.abs(dx) < Math.abs(dy)) return
      dragging.current = true
    }
    const base = swipeOpen ? -SWIPE_OPEN_PX : 0
    setDragX(Math.max(-SWIPE_OPEN_PX, Math.min(0, base + dx)))
  }

  function onTouchEnd() {
    if (dragging.current) {
      const open = dragX < -SWIPE_OPEN_PX / 2
      setSwipeOpen(open)
      setDragX(open ? -SWIPE_OPEN_PX : 0)
    }
    touchStart.current = null
    dragging.current = false
  }

  const details = item.payment_details
  const amount = formatAmount(details?.amount ?? null)

  // Prefer the OCR'd payer name; fall back to the WhatsApp caption name.
  const payerName = details?.payer_name ?? details?.caption_name ?? null
  const payerFromCaption = !details?.payer_name && !!details?.caption_name

  const infoParts: string[] = []
  if (details?.payment_method) infoParts.push(details.payment_method)
  if (details?.payment_type) infoParts.push(details.payment_type)
  if (details?.invoice_number) infoParts.push(`Invoice ${details.invoice_number}`)
  if (details?.check_number) infoParts.push(`Check #${details.check_number}`)

  return (
    <li className="relative overflow-hidden rounded-lg border border-gray-200 bg-white hover:border-blue-300">
      {/* Revealed by swiping the card left on touch devices (admins only). */}
      {onDelete && (
        <button
          type="button"
          onClick={onDelete}
          tabIndex={swipeOpen ? 0 : -1}
          aria-hidden={!swipeOpen}
          className="absolute inset-y-0 right-0 flex w-24 items-center justify-center bg-red-600 font-medium text-white"
        >
          Delete
        </button>
      )}

      <div
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          transform: `translateX(${dragX}px)`,
          transition: dragging.current ? 'none' : 'transform 150ms ease-out',
        }}
        className="relative bg-white p-4"
      >
        <div className="flex flex-col gap-4 sm:flex-row">
        <CheckThumb item={item} width={200} className="h-40 w-full shrink-0 sm:h-20 sm:w-20" />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {amount !== null ? (
              <p className="text-xl font-semibold text-gray-900">{amount}</p>
            ) : (
              <p className="text-xl font-medium italic text-gray-400">amount unreadable</p>
            )}
            {onEditAmount && (
              <button
                type="button"
                onClick={onEditAmount}
                aria-label="Edit amount"
                className="flex h-9 w-9 items-center justify-center rounded-md text-gray-300 hover:bg-blue-50 hover:text-blue-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              >
                <PencilIcon />
              </button>
            )}
          </div>

          <p className="mt-1 text-sm text-gray-700">
            {payerName ? (
              <>
                <span className="font-medium">{payerName}</span>
                {payerFromCaption && (
                  <span className="ml-1 text-xs text-gray-400">(from caption)</span>
                )}
              </>
            ) : (
              <span className="text-gray-400">Unknown payer</span>
            )}
            {infoParts.length > 0 && <span> · {infoParts.join(' · ')}</span>}
          </p>

          <p className="mt-1 text-sm">
            {item.moraware_url ? (
              <a
                href={item.moraware_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-h-11 items-center text-blue-700 underline hover:text-blue-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 sm:min-h-0"
              >
                {item.matched_job_name ?? 'View job in Moraware'}
              </a>
            ) : item.matched_job_name ? (
              <span className="text-gray-700">{item.matched_job_name}</span>
            ) : (
              <span className="text-gray-400">No job match yet</span>
            )}
          </p>
        </div>

        <div className="flex shrink-0 flex-row items-center gap-2 sm:flex-col sm:items-end">
          {needsDecision && <NeedsDecisionChip itemId={item.id} />}
          {item.status === 'processing' && <ProcessingChip itemId={item.id} />}
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
          >
            {item.status}
          </span>
          <EditedChip item={item} />
          <span className="text-xs text-gray-500">{paymentDateLabel(item)}</span>
          {/* Desktop delete; phones swipe left instead. Admins only. */}
          {onDelete && (
            <button
              type="button"
              onClick={onDelete}
              aria-label="Delete payment"
              className="hidden h-9 w-9 items-center justify-center rounded-md text-gray-300 hover:bg-red-50 hover:text-red-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 sm:flex"
            >
              <TrashIcon />
            </button>
          )}
        </div>
        </div>
      </div>
    </li>
  )
}
