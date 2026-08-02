import { Link } from 'react-router-dom'
import { deliveryStatus, formatAmount, relativeTime } from '../lib/format'
import type { ReviewItem } from '../types'
import { TrashIcon } from './PaymentCard'

/** Deliveries table mode (slab chapter): MAXIMUM detail — every material
 * with quantities, square footage, serials, finish/thickness. Cards are for
 * acting; this table is for auditing. */
export default function DeliveriesTable({
  items,
  onDelete,
}: {
  items: ReviewItem[]
  onDelete?: (item: ReviewItem) => void
}) {
  return (
    <div className="mt-5 overflow-x-auto rounded-xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <table className="w-full min-w-[900px] text-left text-sm text-gray-900 dark:text-gray-100">
        <thead>
          <tr className="border-b border-gray-200 text-[11px] uppercase tracking-wider text-gray-400 dark:border-gray-800">
            <th className="px-3 py-2.5">Supplier</th>
            <th className="px-3 py-2.5">Doc #</th>
            <th className="px-3 py-2.5">Slip date</th>
            <th className="px-3 py-2.5">Received</th>
            <th className="px-3 py-2.5">Materials</th>
            <th className="px-3 py-2.5 text-right">Slabs</th>
            <th className="px-3 py-2.5 text-right">Total</th>
            <th className="px-3 py-2.5">Job(s)</th>
            <th className="px-3 py-2.5">Status</th>
            {onDelete && <th className="px-3 py-2.5" aria-label="Actions" />}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const d = item.delivery_details
            const materials = d?.materials ?? []
            return (
              <tr
                key={item.id}
                className="border-b border-gray-100 align-top last:border-0 hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-800/40"
              >
                <td className="px-3 py-2.5 font-medium">
                  <Link to={`/deliveries/item/${item.id}`} className="hover:underline">
                    {d?.supplier ?? '—'}
                  </Link>
                </td>
                <td className="whitespace-nowrap px-3 py-2.5">{d?.document_number ?? '—'}</td>
                <td className="whitespace-nowrap px-3 py-2.5">{d?.order_date ?? '—'}</td>
                <td className="whitespace-nowrap px-3 py-2.5">
                  {relativeTime(item.created_at)}
                </td>
                <td className="px-3 py-2.5">
                  {materials.length === 0 ? (
                    <span>reading…</span>
                  ) : (
                    <ul className="space-y-1.5">
                      {materials.map((m, i) => (
                        <li key={i}>
                          <span className="font-medium">
                            {m.material}
                          </span>
                          {m.slab_count != null && <span> ×{m.slab_count}</span>}
                          {m.total_sf != null && (
                            <span> · {m.total_sf} sf</span>
                          )}
                          <span className="block text-xs">
                            {[m.thickness, m.finish, m.lot ? `lot ${m.lot}` : null]
                              .filter(Boolean).join(' · ')}
                          </span>
                          {m.serials && (
                            <span className="block break-all text-xs">
                              {m.serials}
                            </span>
                          )}
                          <span className="block text-xs font-medium">
                            {m.stock ? (
                              <span>→ Stock</span>
                            ) : m.job_name ? (
                              m.moraware_url ? (
                                <a href={m.moraware_url} target="_blank" rel="noreferrer"
                                   className="text-blue-700 underline dark:text-blue-400">
                                  → {m.job_name}
                                </a>
                              ) : (
                                <span>→ {m.job_name}</span>
                              )
                            ) : (
                              <span className="font-semibold text-amber-700 dark:text-amber-400">→ needs a job</span>
                            )}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">
                  {d?.slab_count ?? '—'}
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">
                  {d?.total ? formatAmount(d.total) : '—'}
                </td>
                <td className="px-3 py-2.5">
                  {item.moraware_url ? (
                    <a href={item.moraware_url} target="_blank" rel="noreferrer"
                       className="text-blue-700 underline dark:text-blue-400">
                      {item.matched_job_name ?? 'View job'}
                    </a>
                  ) : (
                    item.matched_job_name ?? '—'
                  )}
                </td>
                <td className="px-3 py-2.5">
                  <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${deliveryStatus(item.status).klass}`}>
                    {deliveryStatus(item.status).label}
                  </span>
                </td>
                {onDelete && (
                  <td className="px-3 py-2.5">
                    <button
                      type="button"
                      aria-label="Delete delivery"
                      onClick={() => onDelete(item)}
                      className="rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40"
                    >
                      <TrashIcon />
                    </button>
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
