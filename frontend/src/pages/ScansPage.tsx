import { useCallback, useEffect, useRef, useState } from 'react'
import type { TouchEvent } from 'react'
import { Link } from 'react-router-dom'
import { deleteReviewItem, fetchScans } from '../api'
import ConfirmDialog from '../components/ConfirmDialog'
import { useAuth } from '../lib/AuthContext'
import { deliveryStatus, formatDateish } from '../lib/format'
import type { ReviewItem } from '../types'

/** Slab scans board (slab scans chapter): cards only — one card = one
 * scanning session = one job. The real action happens on the card. */

const SWIPE_OPEN_PX = 96

function ScanRow({ item, onDelete }: {
  item: ReviewItem
  onDelete?: () => void
}) {
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

  const slabs = item.scan_details?.slab_ids ?? []
  const status = deliveryStatus(item.status)
  const dateStr = formatDateish(item.scan_details?.scanned_date) ??
    formatDateish(item.created_at)

  return (
    <li className="relative h-full overflow-hidden rounded-2xl">
      {onDelete && (swipeOpen || dragX !== 0) && (
        <button
          type="button"
          onClick={onDelete}
          tabIndex={swipeOpen ? 0 : -1}
          aria-hidden={!swipeOpen}
          className="absolute inset-y-0 right-0 flex w-24 items-center justify-center rounded-r-2xl bg-red-600 font-medium text-white"
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
        className="relative h-full"
      >
        <Link
          to={`/scans/item/${item.id}`}
          className={`flex h-full w-full flex-col rounded-2xl border bg-white p-3 shadow-sm transition-colors hover:border-gray-300 hover:shadow ${
            item.status === 'confirmed' ? 'border-gray-200' : 'border-amber-300'
          }`}
        >
          <div className="flex items-start justify-between gap-1">
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${status.klass}`}>
              {status.label}
            </span>
            {onDelete && (
              <button
                type="button"
                aria-label="Delete scan"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  onDelete()
                }}
                className="hidden rounded-full p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 sm:flex"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M10 11v6M14 11v6" />
                </svg>
              </button>
            )}
          </div>

          <p className="mt-2.5 text-[15px] font-semibold leading-tight text-gray-900">
            {item.matched_job_name ?? 'Needs a job'}
          </p>
          {dateStr && <p className="mt-1 text-[12px] text-gray-500">{dateStr}</p>}
          <div className="mt-2">
            <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700">
              {slabs.length} slab{slabs.length === 1 ? '' : 's'}
            </span>
          </div>
        </Link>
      </div>
    </li>
  )
}

export default function ScansPage() {
  const { user } = useAuth()
  const canDelete = user?.role === 'admin'
  const [items, setItems] = useState<ReviewItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<ReviewItem | null>(null)

  async function confirmDelete() {
    if (!pendingDelete) return
    try {
      await deleteReviewItem(pendingDelete.id)
      setItems((prev) => prev.filter((i) => i.id !== pendingDelete.id))
    } catch {
      setError("Couldn't delete the scan — try again.")
    } finally {
      setPendingDelete(null)
    }
  }

  const load = useCallback(async () => {
    try {
      const list = await fetchScans()
      setItems(list.items)
      setError(null)
    } catch {
      setError("Couldn't load scans — check the connection and retry.")
    }
  }, [])

  useEffect(() => {
    void load().finally(() => setLoading(false))
    const timer = window.setInterval(() => void load(), 15000)
    return () => window.clearInterval(timer)
  }, [load])

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Slab scans</h1>
        <Link
          to="/scans/submit"
          className="inline-flex min-h-11 items-center rounded-xl bg-blue-700 px-4 text-[15px] font-semibold text-white hover:bg-blue-800"
        >
          + Upload slabs
        </Link>
      </div>

      {error && (
        <p role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </p>
      )}

      {loading ? (
        <p role="status" className="mt-10 text-center text-gray-500">Loading…</p>
      ) : items.length === 0 ? (
        <p className="mt-10 text-center text-gray-500">
          No scans yet — tap “Upload slabs” after scanning labels.
        </p>
      ) : (
        <ul className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {items.map((item) => (
            <ScanRow
              key={item.id}
              item={item}
              onDelete={canDelete ? () => setPendingDelete(item) : undefined}
            />
          ))}
        </ul>
      )}

      {pendingDelete && (
        <ConfirmDialog
          message="Delete this slab scan?"
          note="This can't be undone."
          confirmLabel="Delete"
          onConfirm={() => void confirmDelete()}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  )
}
