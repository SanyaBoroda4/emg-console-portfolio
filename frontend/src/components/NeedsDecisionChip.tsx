import { Link } from 'react-router-dom'

/** Board chip for items whose bot question is unanswered — links straight to
 * the decision card (decision flow §7). Amber = attention semantics, matching
 * the card's question slip. */
export default function NeedsDecisionChip({ itemId }: { itemId: string }) {
  return (
    <Link
      to={`/payments/item/${itemId}`}
      onClick={(e) => e.stopPropagation()}
      className="inline-flex min-h-10 items-center gap-1.5 rounded-full bg-amber-100 px-4 py-1.5 text-sm font-semibold text-amber-800 shadow-sm ring-1 ring-amber-300 hover:bg-amber-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M9 9.5a3 3 0 1 1 4.2 2.8c-.9.4-1.2 1-1.2 1.9m0 3.3h.01" />
      </svg>
      Needs decision
    </Link>
  )
}
