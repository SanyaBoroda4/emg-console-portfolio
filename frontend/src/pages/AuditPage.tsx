import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { fetchAudit } from '../api'
import { formatDateTime } from '../lib/format'
import type { AuditEntry } from '../types'

const PAGE_SIZE = 50

/** The ledger, deliberately boring: When · Who · Action · Item · Field ·
 * Old → New, newest first. Admin-only (route + endpoint both enforce). */
export default function AuditPage() {
  const [searchParams] = useSearchParams()
  const reviewItemId = searchParams.get('review_item_id') ?? undefined

  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await fetchAudit({ reviewItemId, limit: PAGE_SIZE, offset: 0 })
      setEntries(list.entries)
      setTotal(list.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setLoading(false)
    }
  }, [reviewItemId])

  useEffect(() => {
    void load()
  }, [load])

  async function loadMore() {
    setLoadingMore(true)
    try {
      const list = await fetchAudit({
        reviewItemId,
        limit: PAGE_SIZE,
        offset: entries.length,
      })
      setEntries((prev) => [...prev, ...list.entries])
      setTotal(list.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-blue-950">
          Audit <span className="font-normal text-gray-400">· change history</span>
        </h2>
        <Link
          to="/payments"
          className="inline-flex min-h-11 items-center rounded-md border border-gray-300 bg-white px-4 text-sm font-medium text-gray-700 hover:border-gray-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
        >
          ← Back to payments
        </Link>
      </div>

      {reviewItemId && (
        <p className="rounded-md border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-900">
          Showing history for one payment.{' '}
          <Link to="/payments/audit" className="font-medium underline">
            Show everything
          </Link>
        </p>
      )}

      {error ? (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-6">
          <p className="text-sm text-red-800">{error}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-3 min-h-11 rounded-md bg-red-700 px-4 font-medium text-white hover:bg-red-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700"
          >
            Retry
          </button>
        </div>
      ) : loading ? (
        <div aria-hidden="true" className="rounded-lg border border-gray-200 bg-white p-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="mb-3 h-5 w-full rounded bg-gray-100" />
          ))}
        </div>
      ) : entries.length === 0 ? (
        <div className="rounded-lg border border-gray-200 bg-white p-8 text-center text-gray-600">
          No changes recorded yet.
        </div>
      ) : (
        <>
          <div className="overflow-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full text-sm">
              <thead>
                <tr>
                  {['When', 'Who', 'Action', 'Item', 'Field', 'Old → New'].map((label) => (
                    <th
                      key={label}
                      scope="col"
                      className="sticky top-0 z-10 whitespace-nowrap border-b border-gray-200 bg-gray-50 px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-500"
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.id} className="odd:bg-gray-50/60">
                    <td className="whitespace-nowrap px-3 py-1.5 text-gray-500">
                      {formatDateTime(entry.created_at)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5">{entry.actor_email}</td>
                    <td className="whitespace-nowrap px-3 py-1.5">
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                          entry.action === 'delete'
                            ? 'bg-red-100 text-red-800'
                            : 'bg-gray-100 text-gray-700'
                        }`}
                      >
                        {entry.action}
                      </span>
                    </td>
                    <td className="max-w-72 truncate whitespace-nowrap px-3 py-1.5 text-gray-600">
                      {entry.item_label}
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5 text-gray-600">
                      {entry.field ?? '—'}
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5 tabular-nums">
                      {entry.action === 'delete' ? (
                        '—'
                      ) : (
                        <>
                          <span className="text-gray-500">{entry.old_value ?? '(empty)'}</span>
                          {' → '}
                          <span className="font-medium">{entry.new_value ?? '(empty)'}</span>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {entries.length < total && (
            <button
              type="button"
              onClick={() => void loadMore()}
              disabled={loadingMore}
              className="min-h-11 rounded-md border border-gray-300 bg-white px-4 font-medium text-gray-700 hover:border-gray-400 disabled:text-gray-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
            >
              {loadingMore ? 'Loading…' : `Load more (showing ${entries.length} of ${total})`}
            </button>
          )}
        </>
      )}
    </div>
  )
}
