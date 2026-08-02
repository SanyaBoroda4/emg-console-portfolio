import { useCallback, useEffect, useRef, useState } from 'react'
import type { TouchEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { deleteReviewItem, fetchDeliveries } from '../api'
import ConfirmDialog from '../components/ConfirmDialog'
import DeliveriesTable from '../components/DeliveriesTable'
import ProcessingChip from '../components/ProcessingChip'
import { useAuth } from '../lib/AuthContext'
import { deliveryStatus, formatAmount, formatDateish } from '../lib/format'
import type { DeliveryMaterial, ReviewItem } from '../types'

/** The Deliveries board (slab chapter): one row per slip. Glance here, act
 * on the card. The materials chip opens a quick-peek popover without leaving
 * the board. */

function MaterialsPeek({ materials, onClose }: {
  materials: DeliveryMaterial[]
  onClose: () => void
}) {
  return (
    <div
      className="fixed inset-0 z-30 flex items-end justify-center bg-black/30 sm:items-center"
      onClick={onClose}
    >
      <div
        className="max-h-[70vh] w-full max-w-md overflow-auto rounded-t-2xl bg-white p-5 shadow-xl sm:rounded-2xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
          Materials ({materials.length})
        </h3>
        <ul className="mt-2 space-y-2.5">
          {materials.map((m, i) => (
            <li key={i} className="text-[15px]">
              <span className="font-semibold text-gray-900 dark:text-gray-100">
                {m.material}
              </span>
              {m.slab_count != null && (
                <span className="text-gray-500"> ×{m.slab_count}</span>
              )}
              <span className="block text-[13px] text-gray-500 dark:text-gray-400">
                {m.stock ? '→ Stock' : m.job_name ? `→ ${m.job_name}` : '→ needs a job'}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}


// How far (px) the swipe-left gesture reveals the Delete button.
const SWIPE_OPEN_PX = 96

/** One delivery row: swipe left to reveal Delete (touch, admins only);
 * desktop admins get a trash icon until the table view exists. */
function DeliveryRow({ item, onDelete, onPeek }: {
  item: ReviewItem
  onDelete?: () => void
  onPeek: (materials: DeliveryMaterial[]) => void
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

  const d = item.delivery_details
  const materials = d?.materials ?? []
  const needsWork = item.status === 'needs_job'
  const status = deliveryStatus(item.status)
  const dateStr = formatDateish(d?.order_date) ?? formatDateish(item.created_at)

  return (
    <li className="relative h-full overflow-hidden rounded-2xl">
      {/* Only mount the red panel mid-swipe — resting behind the card it
          bleeds through the rounded corners as a pink halo. */}
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
          to={`/deliveries/item/${item.id}`}
          className={`flex h-full w-full flex-col rounded-2xl border bg-white p-3 shadow-sm transition-colors hover:border-gray-300 hover:shadow ${
            needsWork ? 'border-amber-300' : 'border-gray-200'
          }`}
        >
          <div className="flex items-start justify-between gap-1">
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${status.klass}`}>
              {status.label}
            </span>
            {onDelete && (
              <button
                type="button"
                aria-label="Delete delivery"
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
            {d?.supplier ?? 'Reading slip…'}
          </p>
          {dateStr && (
            <p className="mt-1 text-[12px] text-gray-500">{dateStr}</p>
          )}
          <p className="text-[12px] text-gray-500">
            {[d?.document_number,
              d?.slab_count != null ? `${d.slab_count} slabs` : null,
              d?.total ? formatAmount(d.total) : null]
              .filter(Boolean).join(' · ')}
          </p>

          <div className="mt-2 min-w-0">
            <p className="truncate text-[13px] font-medium text-gray-700">
              {item.matched_job_name
                ? `→ ${item.matched_job_name}`
                : needsWork
                  ? '→ needs a job'
                  : ' '}
            </p>
            <div className="mt-1.5 flex items-center gap-1.5">
              {item.status === 'processing' && (
                <ProcessingChip itemId={item.id} basePath="/deliveries/item" />
              )}
              {materials.length > 0 && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.preventDefault()
                    e.stopPropagation()
                    onPeek(materials)
                  }}
                  className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-200"
                >
                  {materials.length} material{materials.length === 1 ? '' : 's'}
                </button>
              )}
            </div>
          </div>
        </Link>
      </div>
    </li>
  )
}

export default function DeliveriesPage() {
  const { user } = useAuth()
  const canDelete = user?.role === 'admin'
  const [searchParams, setSearchParams] = useSearchParams()
  const view = searchParams.get('view') === 'table' ? 'table' : 'cards'
  const [items, setItems] = useState<ReviewItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [peek, setPeek] = useState<DeliveryMaterial[] | null>(null)
  const [pendingDelete, setPendingDelete] = useState<ReviewItem | null>(null)

  async function confirmDelete() {
    if (!pendingDelete) return
    try {
      await deleteReviewItem(pendingDelete.id)
      setItems((prev) => prev.filter((i) => i.id !== pendingDelete.id))
    } catch {
      setError("Couldn't delete the delivery — try again.")
    } finally {
      setPendingDelete(null)
    }
  }

  const load = useCallback(async () => {
    try {
      const list = await fetchDeliveries()
      setItems(list.items)
      setError(null)
    } catch {
      setError("Couldn't load deliveries — check the connection and retry.")
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
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-50">Deliveries</h1>
        <Link
          to="/deliveries/submit"
          className="inline-flex min-h-11 items-center rounded-xl bg-blue-700 px-4 text-[15px] font-semibold text-white hover:bg-blue-800"
        >
          + Submit delivery
        </Link>
      </div>

      {/* Cards|Table lives at the LEFT edge (owner 2026-07-22). */}
      <div className="mt-3 flex">
        <div className="flex overflow-hidden rounded-xl border border-gray-300" role="tablist">
          {(['cards', 'table'] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              onClick={() => setSearchParams(v === 'table' ? { view: 'table' } : {})}
              className={`min-h-11 px-4 text-sm font-semibold capitalize ${
                view === v
                  ? 'bg-blue-900 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-300">
          {error}
        </p>
      )}

      {loading ? (
        <p role="status" className="mt-10 text-center text-gray-500">Loading…</p>
      ) : items.length === 0 ? (
        <p className="mt-10 text-center text-gray-500">
          No deliveries yet — photograph the first slip.
        </p>
      ) : (
        view === 'table' ? (
          <DeliveriesTable
            items={items}
            onDelete={canDelete ? setPendingDelete : undefined}
          />
        ) : (
        <ul className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {items.map((item) => (
            <DeliveryRow
              key={item.id}
              item={item}
              onPeek={setPeek}
              onDelete={canDelete ? () => setPendingDelete(item) : undefined}
            />
          ))}
        </ul>
        )
      )}

      {peek && <MaterialsPeek materials={peek} onClose={() => setPeek(null)} />}

      {pendingDelete && (
        <ConfirmDialog
          message={`Delete the delivery from ${
            pendingDelete.delivery_details?.supplier ?? 'this slip'
          }?`}
          note="This can't be undone — the photo and card are removed."
          confirmLabel="Delete"
          onConfirm={() => void confirmDelete()}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  )
}
