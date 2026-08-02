import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { ReviewItem } from '../types'
import { formatAmount, formatDateish, statusBadgeClass } from '../lib/format'
import CheckThumb from './CheckThumb'
import EditedChip from './EditedChip'
import NeedsDecisionChip from './NeedsDecisionChip'
import ProcessingChip from './ProcessingChip'
import { PencilIcon, TrashIcon } from './PaymentCard'

/** Click-to-edit table cell: input in place, Enter commits, Esc/blur
 * cancels — no accidental commits. Hover shows the pencil affordance. */
function EditableCell({
  canEdit,
  display,
  initial,
  onCommit,
}: {
  canEdit: boolean
  display: ReactNode
  initial: string
  onCommit: (value: string) => Promise<boolean>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  if (!canEdit) return <>{display}</>

  if (!editing) {
    return (
      <button
        type="button"
        title="Click to edit"
        onClick={() => {
          setDraft(initial)
          setEditing(true)
        }}
        className="group -mx-1 flex w-full items-center gap-1 rounded px-1 text-left hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-600"
      >
        <span className="min-w-0 truncate">{display}</span>
        <PencilIcon className="invisible shrink-0 text-gray-400 group-hover:visible" />
      </button>
    )
  }

  return (
    <input
      autoFocus
      value={draft}
      disabled={saving}
      onChange={(event) => setDraft(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' && !saving) {
          setSaving(true)
          void onCommit(draft).finally(() => {
            setSaving(false)
            setEditing(false)
          })
        }
        if (event.key === 'Escape') setEditing(false)
      }}
      onBlur={() => {
        if (!saving) setEditing(false) // blur cancels, never commits
      }}
      className="w-full min-w-24 rounded border border-blue-500 px-1 py-0.5 text-sm focus:outline-none"
    />
  )
}

type SortKey =
  | 'payment_date'
  | 'received'
  | 'amount'
  | 'payer'
  | 'method'
  | 'type'
  | 'invoice'
  | 'check'
  | 'job'
  | 'status'
type SortDir = 'asc' | 'desc'

// Sort-key fallback chain per the plan: txn_date → date_received → created_at.
const paymentMoment = (item: ReviewItem): string =>
  item.payment_details?.txn_date ?? item.payment_details?.date_received ?? item.created_at
const receivedMoment = (item: ReviewItem): string =>
  item.payment_details?.date_received ?? item.created_at
const payerDisplay = (item: ReviewItem): string =>
  item.payment_details?.payer_name ?? item.payment_details?.caption_name ?? ''

const ACCESSORS: Record<SortKey, (item: ReviewItem) => string | number | null> = {
  payment_date: (i) => paymentMoment(i),
  received: (i) => receivedMoment(i),
  amount: (i) => (i.payment_details?.amount != null ? Number(i.payment_details.amount) : null),
  payer: (i) => payerDisplay(i).toLowerCase() || null,
  method: (i) => i.payment_details?.payment_method?.toLowerCase() ?? null,
  type: (i) => i.payment_details?.payment_type?.toLowerCase() ?? null,
  invoice: (i) => i.payment_details?.invoice_number?.toLowerCase() ?? null,
  check: (i) => i.payment_details?.check_number?.toLowerCase() ?? null,
  job: (i) => i.matched_job_name?.toLowerCase() ?? null,
  status: (i) => i.status.toLowerCase(),
}

// One consistent hue per PaymentType; PIF (paid in full) gets the success one.
const TYPE_CHIPS: Record<string, string> = {
  deposit: 'bg-blue-100 text-blue-800',
  progress: 'bg-purple-100 text-purple-800',
  remainder: 'bg-amber-100 text-amber-800',
  pif: 'bg-green-100 text-green-800',
}

const COLUMNS: { key: SortKey | null; label: string; alignRight?: boolean }[] = [
  { key: 'payer', label: 'Payer' },
  { key: 'payment_date', label: 'Payment Date' },
  { key: 'received', label: 'Received' },
  { key: 'amount', label: 'Amount', alignRight: true },
  { key: 'method', label: 'Method' },
  { key: 'type', label: 'Type' },
  { key: 'invoice', label: 'Invoice' },
  { key: 'check', label: 'Check #' },
  { key: 'job', label: 'Job' },
  { key: 'status', label: 'Status' },
  { key: null, label: 'Photo' },
  { key: null, label: '' }, // delete column (admins only)
]

interface Props {
  items: ReviewItem[]
  statusFilter: string | null
  /** Absent for non-admin roles — the delete column disappears. */
  onDelete?: (item: ReviewItem) => void
  /** Admin: all whitelisted cells editable. Manager: amount only. */
  canEditAll?: boolean
  /** Opens the amount confirm flow (manager + admin). */
  onEditAmount?: (item: ReviewItem) => void
  /** Inline commit for non-amount fields; resolves false on failure. */
  onPatch?: (item: ReviewItem, field: string, value: string) => Promise<boolean>
  /** Items with an open bot question — rows wear the decision chip. */
  needsDecisionIds?: Set<string>
}

export default function PaymentsTable({
  items,
  statusFilter,
  onDelete,
  canEditAll = false,
  onEditAmount,
  onPatch,
  needsDecisionIds,
}: Props) {
  const columns = onDelete ? COLUMNS : COLUMNS.slice(0, -1)
  const canInline = canEditAll && onPatch !== undefined
  const commit = (item: ReviewItem, field: string) => (value: string) =>
    onPatch ? onPatch(item, field, value) : Promise.resolve(false)
  const [query, setQuery] = useState('')
  // Default: newest payment at the top (owner's choice, overriding the plan).
  const [sortKey, setSortKey] = useState<SortKey>('payment_date')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  function onSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    let filtered = statusFilter ? items.filter((i) => i.status === statusFilter) : items
    if (q) {
      filtered = filtered.filter((item) => {
        const d = item.payment_details
        const hay = [
          formatDateish(d?.txn_date),
          formatDateish(receivedMoment(item)),
          d?.amount,
          formatAmount(d?.amount),
          payerDisplay(item),
          d?.payment_method,
          d?.payment_type,
          d?.invoice_number,
          d?.check_number,
          item.matched_job_name,
          item.status,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
        return hay.includes(q)
      })
    }
    return [...filtered].sort((x, y) => {
      const a = ACCESSORS[sortKey](x)
      const b = ACCESSORS[sortKey](y)
      if (a == null && b == null) return 0
      if (a == null) return 1 // blanks sink to the bottom either direction
      if (b == null) return -1
      const cmp =
        typeof a === 'number' && typeof b === 'number'
          ? a - b
          : String(a) < String(b)
            ? -1
            : String(a) > String(b)
              ? 1
              : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [items, statusFilter, query, sortKey, sortDir])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="block w-full sm:max-w-xs">
          <span className="sr-only">Search payments</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search payer, invoice, amount, job…"
            className="min-h-11 w-full rounded-md border border-gray-300 bg-white px-3 text-sm focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600"
          />
        </label>
        <span className="text-sm text-gray-500">
          {rows.length} of {items.length} payments
        </span>
      </div>

      {/* max-h gives the box its own vertical scroll so the sticky header
          works; overflow also covers horizontal scroll on phones. */}
      <div className="max-h-[70vh] overflow-auto rounded-lg border border-gray-200 bg-white">
        <table className="min-w-full text-sm">
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.label}
                  scope="col"
                  aria-sort={
                    col.key === sortKey
                      ? sortDir === 'asc'
                        ? 'ascending'
                        : 'descending'
                      : undefined
                  }
                  className="sticky top-0 z-10 whitespace-nowrap border-b border-gray-200 bg-gray-50 px-3 py-2 text-left"
                >
                  {col.key ? (
                    <button
                      type="button"
                      onClick={() => onSort(col.key as SortKey)}
                      className={`flex items-center gap-1 text-xs font-semibold uppercase tracking-wide hover:text-gray-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 ${
                        col.key === sortKey ? 'text-gray-900' : 'text-gray-500'
                      } ${col.alignRight ? 'ml-auto' : ''}`}
                    >
                      {col.label}
                      <span aria-hidden="true" className="w-3">
                        {col.key === sortKey ? (sortDir === 'asc' ? '▲' : '▼') : ''}
                      </span>
                    </button>
                  ) : (
                    <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                      {col.label}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-3 py-8 text-center text-gray-500">
                  {query.trim()
                    ? `No payments match “${query.trim()}”.`
                    : statusFilter
                      ? `No payments with status “${statusFilter}”.`
                      : 'No payments yet.'}
                </td>
              </tr>
            ) : (
              rows.map((item) => {
                const d = item.payment_details
                const amount = formatAmount(d?.amount)
                const typeValue = d?.payment_type ?? null
                const fromCaption = !d?.payer_name && !!d?.caption_name
                return (
                  <tr key={item.id} className="bg-white hover:bg-blue-50">
                    {/* Cap sized so "Stephen M Rawson W Allen Woods" fits;
                        longer names truncate (full name in the tooltip). */}
                    <td
                      title={payerDisplay(item) || undefined}
                      className="max-w-60 whitespace-nowrap px-3 py-1.5 font-medium"
                    >
                      <span className="flex items-center gap-1.5">
                        <EditableCell
                          canEdit={canInline}
                          initial={d?.payer_name ?? ''}
                          onCommit={commit(item, 'payer_name')}
                          display={
                            <>
                              {payerDisplay(item) || '—'}
                              {fromCaption && (
                                <span className="ml-1 text-xs font-normal text-gray-400">
                                  (caption)
                                </span>
                              )}
                            </>
                          }
                        />
                        <EditedChip item={item} />
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5">
                      <EditableCell
                        canEdit={canInline}
                        initial={d?.txn_date ?? ''}
                        onCommit={commit(item, 'txn_date')}
                        display={formatDateish(d?.txn_date) ?? '—'}
                      />
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5 text-gray-500">
                      {formatDateish(receivedMoment(item)) ?? '—'}
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5 text-right font-medium tabular-nums">
                      {onEditAmount ? (
                        <button
                          type="button"
                          title="Edit amount"
                          onClick={() => onEditAmount(item)}
                          className="group -mx-1 inline-flex items-center gap-1 rounded px-1 hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-600"
                        >
                          {amount ?? (
                            <span className="font-normal italic text-gray-400">unreadable</span>
                          )}
                          <PencilIcon className="invisible text-gray-400 group-hover:visible" />
                        </button>
                      ) : (
                        (amount ?? (
                          <span className="font-normal italic text-gray-400">unreadable</span>
                        ))
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5 text-gray-600">
                      <EditableCell
                        canEdit={canInline}
                        initial={d?.payment_method ?? ''}
                        onCommit={commit(item, 'payment_method')}
                        display={d?.payment_method ?? '—'}
                      />
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5">
                      <EditableCell
                        canEdit={canInline}
                        initial={d?.payment_type ?? ''}
                        onCommit={commit(item, 'payment_type')}
                        display={
                          typeValue ? (
                            <span
                              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                                TYPE_CHIPS[typeValue.toLowerCase()] ?? 'bg-gray-100 text-gray-700'
                              }`}
                            >
                              {typeValue}
                            </span>
                          ) : (
                            '—'
                          )
                        }
                      />
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5 text-gray-600">
                      <EditableCell
                        canEdit={canInline}
                        initial={d?.invoice_number ?? ''}
                        onCommit={commit(item, 'invoice_number')}
                        display={d?.invoice_number ?? '—'}
                      />
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5 text-gray-600">
                      <EditableCell
                        canEdit={canInline}
                        initial={d?.check_number ?? ''}
                        onCommit={commit(item, 'check_number')}
                        display={d?.check_number ?? '—'}
                      />
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5">
                      {item.moraware_url ? (
                        <a
                          href={item.moraware_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-blue-700 underline hover:text-blue-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                        >
                          {item.matched_job_name ?? 'View job'}
                        </a>
                      ) : (
                        (item.matched_job_name ?? '—')
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5">
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}
                        >
                          {item.status}
                        </span>
                        {needsDecisionIds?.has(item.id) && (
                          <NeedsDecisionChip itemId={item.id} />
                        )}
                        {item.status === 'processing' && (
                          <ProcessingChip itemId={item.id} />
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-1.5">
                      <CheckThumb item={item} width={120} className="h-10 w-10" />
                    </td>
                    {onDelete && (
                      <td className="px-2 py-1.5">
                        <button
                          type="button"
                          onClick={() => onDelete(item)}
                          aria-label="Delete payment"
                          className="flex h-9 w-9 items-center justify-center rounded-md text-gray-300 hover:bg-red-50 hover:text-red-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600"
                        >
                          <TrashIcon />
                        </button>
                      </td>
                    )}
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
