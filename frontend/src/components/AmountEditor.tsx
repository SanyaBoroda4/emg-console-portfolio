import { useEffect, useState } from 'react'
import { ApiError, patchReviewItem } from '../api'
import { formatAmount } from '../lib/format'
import type { ReviewItem } from '../types'

interface Props {
  item: ReviewItem
  onDone: (updated: ReviewItem) => void
  onCancel: () => void
}

/** The quick OCR-fix path: input → explicit old→new confirm → save.
 * Enter advances/confirms, Esc cancels. */
export default function AmountEditor({ item, onDone, onCancel }: Props) {
  // Baseline = what we believe the current value is; refreshed on a 409.
  const [baseline, setBaseline] = useState<string | null>(
    item.payment_details?.amount ?? null,
  )
  const [draft, setDraft] = useState('')
  const [phase, setPhase] = useState<'input' | 'confirm'>('input')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mirrored = item.airtable_id !== null

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving) onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, saving])

  const draftNumber = Number(draft)
  const draftValid =
    draft.trim() !== '' && !Number.isNaN(draftNumber) && draftNumber > 0 && draftNumber <= 500000

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const updated = await patchReviewItem(
        item.id,
        { amount: draft.trim() },
        { amount: baseline },
      )
      onDone(updated)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const fresh = (err.body?.current as string | null) ?? null
        setBaseline(fresh)
        setPhase('input')
        setError(
          `Someone just changed this to ${formatAmount(fresh) ?? '(empty)'} — review and retry.`,
        )
      } else if (err instanceof ApiError && err.status === 502) {
        setError("Couldn't reach Airtable — nothing was changed. Try again.")
      } else {
        setError(err instanceof Error ? err.message : 'Edit failed.')
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Edit amount"
      onClick={() => !saving && onCancel()}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="w-full max-w-sm rounded-lg bg-white p-6 shadow-xl"
      >
        {phase === 'input' ? (
          <>
            <p className="font-medium text-gray-900">Edit amount</p>
            <p className="mt-2 text-sm text-gray-500">
              Current:{' '}
              <span className="line-through">{formatAmount(baseline) ?? '(no amount)'}</span>
            </p>
            <input
              autoFocus
              type="text"
              inputMode="decimal"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && draftValid) setPhase('confirm')
              }}
              placeholder="New amount, e.g. 4850.50"
              className="mt-3 min-h-11 w-full rounded-md border border-gray-300 px-3 text-lg tabular-nums focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600"
            />
            {error && (
              <p role="alert" className="mt-3 rounded-md bg-amber-50 p-2 text-sm text-amber-800">
                {error}
              </p>
            )}
            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={onCancel}
                className="min-h-11 rounded-md border border-gray-300 px-4 font-medium text-gray-700 hover:border-gray-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={!draftValid}
                onClick={() => setPhase('confirm')}
                className="min-h-11 rounded-md bg-blue-600 px-4 font-medium text-white hover:bg-blue-700 disabled:bg-gray-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              >
                Next
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="font-medium text-gray-900">
              Change amount {formatAmount(baseline) ?? '(no amount)'} →{' '}
              {formatAmount(draft) ?? draft}?
            </p>
            {mirrored && (
              <p className="mt-2 text-sm text-amber-700">This also updates Airtable.</p>
            )}
            {error && (
              <p role="alert" className="mt-3 rounded-md bg-red-50 p-2 text-sm text-red-800">
                {error}
              </p>
            )}
            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setPhase('input')}
                disabled={saving}
                className="min-h-11 rounded-md border border-gray-300 px-4 font-medium text-gray-700 hover:border-gray-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              >
                Back
              </button>
              <button
                type="button"
                autoFocus
                disabled={saving}
                onClick={() => void save()}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !saving) void save()
                }}
                className="min-h-11 rounded-md bg-blue-600 px-4 font-medium text-white hover:bg-blue-700 disabled:bg-gray-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              >
                {saving ? 'Saving…' : 'Confirm'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
