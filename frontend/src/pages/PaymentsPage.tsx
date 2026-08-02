import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ApiError, deleteReviewItem, fetchNeedsDecision, fetchReviewItems, fetchStats, patchReviewItem } from '../api'
import type { PaymentDetails, ReviewItem, Stats } from '../types'
import AmountEditor from '../components/AmountEditor'
import ConfirmDialog from '../components/ConfirmDialog'
import PaymentCard from '../components/PaymentCard'
import PaymentsTable from '../components/PaymentsTable'
import StatusFilter from '../components/StatusFilter'
import { useAuth } from '../lib/AuthContext'
import { formatAmount } from '../lib/format'

const PAGE_SIZE = 50
// Table mode wants the whole set (client-side search/sort). Fine at ~200
// rows; past ~1000 we move search server-side (plan §3 assumption).
const TABLE_LIMIT = 1000
const MIRROR_COMMAND =
  'docker compose exec backend python -m app.scripts.mirror_airtable'

type ViewMode = 'cards' | 'table'

// Static skeleton (no animation in Stage 1 — plan §9).
function SkeletonCards() {
  return (
    <ul aria-hidden="true" className="flex flex-col gap-3">
      {[0, 1, 2].map((i) => (
        <li key={i} className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="flex flex-col gap-4 sm:flex-row">
            <div className="h-40 w-full rounded-md bg-gray-100 sm:h-20 sm:w-20" />
            <div className="flex-1">
              <div className="h-6 w-28 rounded bg-gray-100" />
              <div className="mt-2 h-4 w-2/3 rounded bg-gray-100" />
              <div className="mt-2 h-4 w-1/3 rounded bg-gray-100" />
            </div>
          </div>
        </li>
      ))}
    </ul>
  )
}

function SkeletonTable() {
  return (
    <div aria-hidden="true" className="rounded-lg border border-gray-200 bg-white p-4">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="mb-3 h-5 w-full rounded bg-gray-100" />
      ))}
    </div>
  )
}

function ViewToggle({
  view,
  onChange,
}: {
  view: ViewMode
  onChange: (view: ViewMode) => void
}) {
  const segment = (value: ViewMode, label: string) => (
    <button
      type="button"
      aria-pressed={view === value}
      onClick={() => onChange(value)}
      className={`min-h-10 rounded-md px-4 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 ${
        view === value ? 'bg-blue-900 text-white' : 'text-gray-600 hover:bg-blue-50'
      }`}
    >
      {label}
    </button>
  )
  return (
    <div
      role="group"
      aria-label="View mode"
      className="inline-flex rounded-lg border border-gray-300 bg-white p-0.5"
    >
      {segment('cards', 'Cards')}
      {segment('table', 'Table')}
    </div>
  )
}

export default function PaymentsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const view: ViewMode = searchParams.get('view') === 'table' ? 'table' : 'cards'
  // Deleting anything, anywhere, is admins only (owner). Managers view/submit.
  const { user } = useAuth()
  const canDelete = user?.role === 'admin'

  const [stats, setStats] = useState<Stats | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Cards mode: paginated, status filtering done by the server.
  const [items, setItems] = useState<ReviewItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)

  // Table mode: the full set once; search/sort/status happen client-side.
  const [tableItems, setTableItems] = useState<ReviewItem[] | null>(null)
  const [tableLoading, setTableLoading] = useState(false)

  // Delete flow: which item awaits confirmation.
  const [pendingDelete, setPendingDelete] = useState<ReviewItem | null>(null)

  // Edit flow (Stage 3): the amount modal + a banner for inline-edit errors.
  const [amountEditItem, setAmountEditItem] = useState<ReviewItem | null>(null)
  const [editError, setEditError] = useState<string | null>(null)

  function setView(next: ViewMode) {
    setSearchParams(next === 'table' ? { view: 'table' } : {})
  }

  // Which items wear the "needs decision" chip (open bot question).
  const [needsDecisionIds, setNeedsDecisionIds] = useState<Set<string>>(new Set())
  const loadNeedsDecision = useCallback(async () => {
    try {
      const { ids } = await fetchNeedsDecision()
      setNeedsDecisionIds(new Set(ids))
    } catch {
      // The board still works without chips.
    }
  }, [])

  const loadCards = useCallback(async () => {
    setLoading(true)
    setError(null)
    void loadNeedsDecision()
    try {
      const [statsData, list] = await Promise.all([
        fetchStats(),
        fetchReviewItems({ status: status ?? undefined, limit: PAGE_SIZE, offset: 0 }),
      ])
      setStats(statsData)
      setItems(list.items)
      setTotal(list.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setLoading(false)
    }
  }, [status])

  const loadTable = useCallback(async () => {
    setTableLoading(true)
    setError(null)
    void loadNeedsDecision()
    try {
      const [statsData, list] = await Promise.all([
        fetchStats(),
        fetchReviewItems({ limit: TABLE_LIMIT }),
      ])
      setStats(statsData)
      setTableItems(list.items)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setTableLoading(false)
    }
  }, [])

  useEffect(() => {
    if (view === 'cards') void loadCards()
  }, [view, loadCards])

  useEffect(() => {
    if (view === 'table') void loadTable()
  }, [view, loadTable])

  async function loadMore() {
    setLoadingMore(true)
    try {
      const list = await fetchReviewItems({
        status: status ?? undefined,
        limit: PAGE_SIZE,
        offset: items.length,
      })
      setItems((prev) => [...prev, ...list.items])
      setTotal(list.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setLoadingMore(false)
    }
  }

  function replaceItem(updated: ReviewItem) {
    setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)))
    setTableItems((prev) =>
      prev ? prev.map((i) => (i.id === updated.id ? updated : i)) : prev,
    )
  }

  /** Inline (table) commits for non-amount fields. Returns false on failure
   * so the cell knows; errors surface in the banner. */
  async function patchField(item: ReviewItem, field: string, value: string): Promise<boolean> {
    const current =
      (item.payment_details?.[field as keyof PaymentDetails] as string | null | undefined) ??
      null
    try {
      const updated = await patchReviewItem(item.id, { [field]: value }, { [field]: current })
      replaceItem(updated)
      setEditError(null)
      return true
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const fresh = (err.body?.current as string | null) ?? '(empty)'
        setEditError(`Someone just changed ${field.replace('_', ' ')} to ${fresh} — review and retry.`)
        // Refresh the current view so the fresh value is on screen.
        void (view === 'table' ? loadTable() : loadCards())
      } else {
        setEditError(err instanceof Error ? err.message : 'Edit failed.')
      }
      return false
    }
  }

  async function confirmDelete() {
    const item = pendingDelete
    if (!item) return
    setPendingDelete(null)
    try {
      await deleteReviewItem(item.id)
      setItems((prev) => prev.filter((i) => i.id !== item.id))
      setTableItems((prev) => (prev ? prev.filter((i) => i.id !== item.id) : prev))
      setTotal((t) => Math.max(0, t - 1))
      setStats((prev) => {
        if (!prev) return prev
        const byStatus = { ...prev.by_status }
        const next = Math.max(0, (byStatus[item.status] ?? 1) - 1)
        if (next === 0) delete byStatus[item.status]
        else byStatus[item.status] = next
        return { total: Math.max(0, prev.total - 1), by_status: byStatus }
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed.')
    }
  }

  function deleteMessage(item: ReviewItem): string {
    const amount = formatAmount(item.payment_details?.amount)
    const payer = item.payment_details?.payer_name ?? item.payment_details?.caption_name
    if (amount && payer) return `Delete the ${amount} payment from ${payer}?`
    return 'Delete this payment?'
  }

  if (error) {
    return (
      <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-6">
        <p className="font-medium text-red-800">Couldn&apos;t load payments.</p>
        <p className="mt-1 text-sm text-red-700">
          {error} Check that the backend is running (docker compose ps), then retry.
        </p>
        <button
          type="button"
          onClick={() => void (view === 'table' ? loadTable() : loadCards())}
          className="mt-4 min-h-11 rounded-md bg-red-700 px-4 font-medium text-white hover:bg-red-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700"
        >
          Retry
        </button>
      </div>
    )
  }

  // The year the board is showing — taken from the newest loaded payment.
  const newest = view === 'table' ? tableItems?.[0] : items[0]
  const newestMoment =
    newest?.payment_details?.txn_date ??
    newest?.payment_details?.date_received ??
    newest?.created_at
  const boardYear = newestMoment
    ? newestMoment.slice(0, 4)
    : String(new Date().getFullYear())

  return (
    // Cards read best centered; the table wants the full screen width.
    <div className={`flex flex-col gap-4 ${view === 'cards' ? 'mx-auto w-full max-w-5xl' : ''}`}>
      <h2 className="text-lg font-semibold text-blue-950">
        Payments <span className="font-normal text-gray-400">· {boardYear}</span>
      </h2>

      {/* Toggle stays leftmost in both modes — it must never jump around.
          Upper-right corner: Submit check, with the total just beneath it. */}
      <div className="flex flex-wrap items-start gap-3">
        <ViewToggle view={view} onChange={setView} />
        {/* On phones the pills wrap to their own line so the submit button
            stays on the top row, level with the Cards/Table toggle. */}
        <div className="order-last w-full sm:order-none sm:w-auto">
          <StatusFilter active={status} onChange={setStatus} />
        </div>
        <div className="ml-auto flex flex-col items-end gap-1.5">
          <div className="flex items-center gap-2">
            {canDelete && (
              <Link
                to="/payments/audit"
                className="inline-flex min-h-11 items-center rounded-md border border-gray-300 bg-white px-4 font-medium text-gray-700 hover:border-gray-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              >
                Audit
              </Link>
            )}
            <Link
              to="/payments/submit"
              className="inline-flex min-h-11 items-center rounded-md bg-blue-600 px-4 font-medium text-white shadow-sm hover:bg-blue-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
            >
              + Submit check
            </Link>
          </div>
          {stats && (
            <p className="text-sm text-gray-500">
              <span className="text-xl font-semibold text-blue-950">{stats.total}</span>{' '}
              payments
            </p>
          )}
        </div>
      </div>

      {amountEditItem && (
        <AmountEditor
          item={amountEditItem}
          onDone={(updated) => {
            replaceItem(updated)
            setAmountEditItem(null)
          }}
          onCancel={() => setAmountEditItem(null)}
        />
      )}

      {pendingDelete && (
        <ConfirmDialog
          message={deleteMessage(pendingDelete)}
          note={
            pendingDelete.source === 'airtable_mirror'
              ? 'This row was mirrored from Airtable — the next mirror run will bring it back.'
              : "This can't be undone."
          }
          onConfirm={() => void confirmDelete()}
          onCancel={() => setPendingDelete(null)}
        />
      )}

      {editError && (
        <div
          role="alert"
          className="flex items-start justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800"
        >
          <span>{editError}</span>
          <button
            type="button"
            onClick={() => setEditError(null)}
            aria-label="Dismiss"
            className="min-h-6 font-bold hover:text-amber-950"
          >
            ×
          </button>
        </div>
      )}

      {view === 'table' ? (
        tableLoading || tableItems === null ? (
          <SkeletonTable />
        ) : (
          <PaymentsTable
            items={tableItems}
            statusFilter={status}
            onDelete={canDelete ? setPendingDelete : undefined}
            canEditAll={canDelete}
            onEditAmount={setAmountEditItem}
            onPatch={patchField}
            needsDecisionIds={needsDecisionIds}
          />
        )
      ) : loading ? (
        <SkeletonCards />
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-gray-200 bg-white p-8 text-center">
          <p className="font-medium text-gray-700">
            {status === null
              ? 'No payments yet — run the mirror script:'
              : `No payments with status “${status}”.`}
          </p>
          {status === null && (
            <code className="mt-3 inline-block rounded bg-gray-100 px-3 py-2 text-sm text-gray-800">
              {MIRROR_COMMAND}
            </code>
          )}
        </div>
      ) : (
        <>
          <ul className="flex flex-col gap-3">
            {items.map((item) => (
              <PaymentCard
                key={item.id}
                item={item}
                onDelete={canDelete ? () => setPendingDelete(item) : undefined}
                onEditAmount={() => setAmountEditItem(item)}
                needsDecision={needsDecisionIds.has(item.id)}
              />
            ))}
          </ul>
          {items.length < total && (
            <button
              type="button"
              onClick={() => void loadMore()}
              disabled={loadingMore}
              className="min-h-11 rounded-md border border-gray-300 bg-white px-4 font-medium text-gray-700 hover:border-gray-400 disabled:text-gray-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
            >
              {loadingMore ? 'Loading…' : `Load more (showing ${items.length} of ${total})`}
            </button>
          )}
        </>
      )}
    </div>
  )
}
