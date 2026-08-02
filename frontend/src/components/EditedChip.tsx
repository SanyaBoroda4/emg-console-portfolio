import { Link } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext'
import { relativeTime } from '../lib/format'
import type { ReviewItem } from '../types'

/** Neutral "edited" marker — corrected values stay visually distinct from
 * pristine OCR output. Admins can click through to the filtered ledger. */
export default function EditedChip({ item }: { item: ReviewItem }) {
  const { user } = useAuth()
  if (!item.last_edited_at) return null

  const title = `by ${item.last_edited_by ?? 'unknown'}, ${relativeTime(item.last_edited_at)}`
  const base = 'rounded-full bg-gray-200 px-2 py-0.5 text-xs font-medium text-gray-600'

  if (user?.role === 'admin') {
    return (
      <Link
        to={`/payments/audit?review_item_id=${item.id}`}
        title={title}
        className={`${base} underline decoration-dotted hover:bg-gray-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600`}
      >
        edited
      </Link>
    )
  }
  return (
    <span title={title} className={base}>
      edited
    </span>
  )
}
