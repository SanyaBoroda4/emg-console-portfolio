import { Link } from 'react-router-dom'

/** Board chip for items CHECK-BOT is working on right now (status
 * 'processing') — a manager who missed the push can spot and open them.
 * The dot pulses; the text carries the meaning (never color alone). */
export default function ProcessingChip({ itemId, basePath = '/payments/item' }: {
  itemId: string
  basePath?: string
}) {
  return (
    <Link
      to={`${basePath}/${itemId}`}
      onClick={(e) => e.stopPropagation()}
      className="inline-flex min-h-7 items-center gap-1.5 rounded-full bg-purple-100 px-2.5 py-0.5 text-xs font-semibold text-purple-800 hover:bg-purple-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-purple-600"
    >
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full rounded-full bg-purple-500 opacity-75 motion-safe:animate-ping" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-purple-600" />
      </span>
      In progress
    </Link>
  )
}
