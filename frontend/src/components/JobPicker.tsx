import { useEffect, useRef, useState } from 'react'
import { searchJobs } from '../api'
import type { JobHit } from '../types'

/** The typeahead job picker (slab chapter). The manager MUST type — no
 * empty-state suggestions (3,500+ jobs make them meaningless) — and [Stock]
 * is always tappable from the very start. Matches arrive from the console's
 * local directory in ~10ms, so suggestions track every keystroke. */
export default function JobPicker({
  onPick,
  onStock,
  busy = false,
  autoFocus = false,
}: {
  onPick: (job: JobHit) => void
  /** Absent = this picker has no Stock option (not used today). */
  onStock?: () => void
  busy?: boolean
  autoFocus?: boolean
}) {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<JobHit[]>([])
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const seq = useRef(0)

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setHits([])
      setOpen(false)
      return
    }
    const mySeq = ++seq.current
    const timer = window.setTimeout(() => {
      void searchJobs(q)
        .then(({ jobs }) => {
          if (seq.current !== mySeq) return // a newer keystroke superseded us
          setHits(jobs)
          setHighlight(0)
          setOpen(true)
        })
        .catch(() => {
          if (seq.current === mySeq) setHits([])
        })
    }, 120)
    return () => window.clearTimeout(timer)
  }, [query])

  function pick(job: JobHit) {
    setOpen(false)
    setQuery('')
    onPick(job)
  }

  return (
    <div className="relative">
      <div className="flex gap-2">
        <input
          type="text"
          value={query}
          autoFocus={autoFocus}
          disabled={busy}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (!open || hits.length === 0) return
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              setHighlight((h) => Math.min(h + 1, hits.length - 1))
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              setHighlight((h) => Math.max(h - 1, 0))
            } else if (e.key === 'Enter') {
              e.preventDefault()
              pick(hits[highlight])
            } else if (e.key === 'Escape') {
              setOpen(false)
            }
          }}
          placeholder="Type the job name…"
          className="min-h-[52px] w-full rounded-xl border-2 border-gray-200 bg-white px-3.5 text-[16px] text-gray-900 placeholder:text-gray-400 focus:border-blue-600 focus:outline-none disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
        />
        {onStock && (
          <button
            type="button"
            disabled={busy}
            onClick={onStock}
            className="min-h-[52px] shrink-0 rounded-xl border-2 border-gray-300 px-4 text-[15px] font-semibold text-gray-700 hover:border-gray-400 hover:bg-gray-50 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            Stock
          </button>
        )}
      </div>

      {open && hits.length > 0 && (
        <ul
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-900"
        >
          {hits.map((job, index) => (
            <li key={job.job_id}>
              <button
                type="button"
                role="option"
                aria-selected={index === highlight}
                onMouseEnter={() => setHighlight(index)}
                onClick={() => pick(job)}
                className={`block min-h-11 w-full px-3.5 py-2 text-left ${
                  index === highlight
                    ? 'bg-blue-50 dark:bg-blue-950/40'
                    : ''
                }`}
              >
                <span className="block text-[15px] font-medium text-gray-900 dark:text-gray-100">
                  {job.customer_name}
                </span>
                <span className="block text-xs text-gray-400 dark:text-gray-500">
                  Job #{job.job_id}
                  {job.creation_date ? ` · ${job.creation_date}` : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && hits.length === 0 && query.trim().length >= 2 && (
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          No jobs match "{query.trim()}" — check the spelling.
        </p>
      )}
    </div>
  )
}
