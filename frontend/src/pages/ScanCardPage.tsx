import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  assignScanJob,
  confirmScan,
  fetchScanCard,
  updateScanSlabs,
} from '../api'
import JobPicker from '../components/JobPicker'
import MaterialPicker from '../components/MaterialPicker'
import LinkifiedText from '../components/LinkifiedText'
import Celebration from '../components/Celebration'
import { deliveryStatus, formatDateish, relativeTime } from '../lib/format'
import type { ItemCard, ScanSlab } from '../types'

function shortName(email: string | null): string {
  if (!email) return 'SCANBOT'
  const local = email.split('@')[0]
  return local.charAt(0).toUpperCase() + local.slice(1)
}

/** One scan card: the slab numbers as an editable list, the job typeahead,
 * and Register → the appended note in the Moraware Job Details form. */
export default function ScanCardPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [card, setCard] = useState<ItemCard | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [pickingJob, setPickingJob] = useState(false)
  const [ticked, setTicked] = useState<Set<string>>(new Set())
  const [celebrate, setCelebrate] = useState(false)
  const wasConfirmed = useRef<boolean | null>(null)
  const ticksInit = useRef(false)

  const load = useCallback(async () => {
    if (!id) return
    try {
      setCard(await fetchScanCard(id))
    } catch {
      setError("Couldn't load the card.")
    }
  }, [id])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 7000)
    return () => window.clearInterval(timer)
  }, [load])

  const item = card?.item
  const events = card?.events ?? []
  const slabs: ScanSlab[] = item?.scan_details?.slab_ids ?? []
  const confirmed = item?.status === 'confirmed'
  const needsMaterial = slabs.some((s) => !s.material)

  // Pre-tick every slab that still needs a material, ONCE, when the card
  // first loads — a gallery batch is usually one material, so Wade can
  // skip ticking and go straight to picking (owner 2026-07-25). He can
  // still untick any that differ.
  useEffect(() => {
    if (!ticksInit.current && !confirmed && slabs.length > 0) {
      ticksInit.current = true
      setTicked(new Set(slabs.filter((s) => !s.material).map((s) => s.id)))
    }
  }, [slabs, confirmed])

  // Pop a "good job!" the moment the card flips to confirmed while it's open —
  // but never when re-opening a card that was already confirmed. The baseline
  // is captured from the card's REAL loaded state: until `item` exists,
  // `confirmed` is a meaningless false (nothing fetched yet), and treating that
  // load as a not-confirmed -> confirmed transition would replay the animation
  // on every open. Guarding on `item` makes the first loaded state the baseline.
  useEffect(() => {
    if (!item) return
    if (wasConfirmed.current === null) {
      wasConfirmed.current = confirmed
      return
    }
    if (confirmed && !wasConfirmed.current) {
      wasConfirmed.current = confirmed
      const t = window.setTimeout(() => setCelebrate(true), 600)
      return () => window.clearTimeout(t)
    }
    wasConfirmed.current = confirmed
  }, [confirmed, item])
  const status = item ? deliveryStatus(item.status) : null

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  function setSlabList(next: ScanSlab[]) {
    if (!id) return
    void act(() => updateScanSlabs(id, next))
  }

  if (!item) {
    return (
      <p role="status" className="mt-10 text-center text-gray-500">
        {error ?? 'Loading…'}
      </p>
    )
  }

  const dateStr = formatDateish(item.scan_details?.scanned_date) ??
    formatDateish(item.created_at)

  return (
    <div className="mx-auto max-w-xl pb-10">
      {celebrate && <Celebration onDone={() => setCelebrate(false)} />}
      <button
        type="button"
        onClick={() => navigate('/scans')}
        className="mt-1 text-sm font-medium text-blue-700 hover:underline"
      >
        ← All scans
      </button>

      <div className="mt-3 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-900">
            Slab scan{dateStr ? ` — ${dateStr}` : ''}
          </h1>
          <p className="mt-0.5 text-sm text-gray-500">
            {slabs.length} slab{slabs.length === 1 ? '' : 's'}
          </p>
        </div>
        {status && (
          <span className={`mt-1.5 shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${status.klass}`}>
            {status.label}
          </span>
        )}
      </div>

      {/* ---- confirmed: the receipt ---- */}
      {confirmed && (
        <div className="mt-4 rounded-xl border border-green-200 bg-green-50 p-4">
          <p className="text-[16px] font-semibold text-green-900">
            ✓ Posted to Moraware
            {item.matched_job_name && (
              <>
                {' — '}
                {item.moraware_url ? (
                  <a
                    href={item.moraware_url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline"
                  >
                    {item.matched_job_name}
                  </a>
                ) : (
                  item.matched_job_name
                )}
              </>
            )}
          </p>
          <ul className="mt-2 space-y-0.5">
            {slabs.map((s) => (
              <li key={s.id} className="text-[15px] text-green-900">
                {s.material ? `${s.material} — ` : ''}
                <span className="font-mono">{s.id}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ---- pending: edit slabs + pick job + register ---- */}
      {!confirmed && (
        <>
          <section className="mt-4">
            <h2 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
              Slabs &amp; materials
            </h2>
            {needsMaterial && (
              <p className="mt-1 text-[13px] text-gray-500">
                Tick the slabs that still need a material, then pick it below.
              </p>
            )}
            <ul className="mt-2 divide-y divide-gray-100 rounded-xl border border-gray-200">
              {slabs.map((s) => (
                <li key={s.id} className="flex items-center gap-3 px-3 py-2.5">
                  {needsMaterial && (
                    s.material ? (
                      // already assigned — no checkbox (can't be re-assigned in
                      // bulk); a spacer keeps the rows aligned.
                      <span className="h-5 w-5 shrink-0" aria-hidden />
                    ) : (
                      <input
                        type="checkbox"
                        checked={ticked.has(s.id)}
                        onChange={(e) => {
                          const next = new Set(ticked)
                          if (e.target.checked) next.add(s.id)
                          else next.delete(s.id)
                          setTicked(next)
                        }}
                        disabled={busy}
                        className="h-5 w-5 accent-blue-600"
                        aria-label={`Select ${s.id}`}
                      />
                    )
                  )}
                  <div className="min-w-0 flex-1">
                    <span className="font-mono text-[15px] font-semibold text-gray-900">
                      {s.id}
                    </span>
                    {s.material ? (
                      <button
                        type="button"
                        onClick={() =>
                          setSlabList(slabs.map((x) =>
                            x.id === s.id ? { ...x, material: null } : x))
                        }
                        disabled={busy}
                        className="ml-2 text-[13px] text-gray-600 underline decoration-dotted underline-offset-2 hover:text-blue-700"
                        title="Change material"
                      >
                        {s.material}
                      </button>
                    ) : (
                      <span className="ml-2 text-[13px] font-medium text-amber-600">
                        needs material
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    aria-label={`Remove ${s.id}`}
                    onClick={() => setSlabList(slabs.filter((x) => x.id !== s.id))}
                    className="px-1 text-gray-400 hover:text-red-600"
                    disabled={busy}
                  >
                    ×
                  </button>
                </li>
              ))}
              {slabs.length === 0 && (
                <li className="px-3 py-2.5 text-sm text-gray-500">
                  No slab numbers on this card.
                </li>
              )}
            </ul>
            {needsMaterial && (
              <div className="mt-3">
                <div className="mb-1.5 flex items-center justify-between">
                  <p className="text-[13px] font-medium text-gray-600">
                    {ticked.size > 0
                      ? `Material for ${ticked.size} ticked slab${ticked.size === 1 ? '' : 's'}:`
                      : 'Tick slab(s) above, then choose the material:'}
                  </p>
                  {slabs.filter((s) => !s.material).length > 1 && (
                    <button
                      type="button"
                      onClick={() => {
                        const open = slabs.filter((s) => !s.material).map((s) => s.id)
                        setTicked(
                          ticked.size === open.length ? new Set() : new Set(open),
                        )
                      }}
                      className="text-[13px] text-blue-700 underline"
                    >
                      {ticked.size === slabs.filter((s) => !s.material).length
                        ? 'untick all'
                        : 'tick all'}
                    </button>
                  )}
                </div>
                <MaterialPicker
                  busy={busy || ticked.size === 0}
                  onPick={(name) => {
                    // Only ticked, still-unassigned slabs get the material —
                    // an already-named slab can never be overwritten in bulk.
                    const next = slabs.map((s) =>
                      ticked.has(s.id) && !s.material ? { ...s, material: name } : s,
                    )
                    setTicked(new Set())
                    setSlabList(next)
                  }}
                />
              </div>
            )}
          </section>

          <section className="mt-6">
            <h2 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
              Job
            </h2>
            {item.matched_job_name && !pickingJob ? (
              <div className="mt-2 flex items-center gap-3">
                <p className="text-[16px] font-semibold text-gray-900">
                  {item.matched_job_name}
                </p>
                <button
                  type="button"
                  onClick={() => setPickingJob(true)}
                  className="text-sm text-blue-700 underline"
                >
                  change
                </button>
              </div>
            ) : (
              <div className="mt-2">
                <JobPicker
                  autoFocus={pickingJob}
                  busy={busy}
                  onPick={(job) => {
                    setPickingJob(false)
                    void act(() =>
                      assignScanJob(item.id, {
                        job_id: job.job_id,
                        job_name: job.customer_name,
                        moraware_url: job.lead_url,
                      }),
                    )
                  }}
                />
              </div>
            )}
          </section>

          {error && (
            <div role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2.5 text-sm font-medium text-red-800">
              {error}
            </div>
          )}

          <button
            type="button"
            onClick={() => void act(() => confirmScan(item.id))}
            disabled={busy || slabs.length === 0 || !item.matched_job_id || slabs.some((s) => !s.material)}
            className="mt-6 min-h-[52px] w-full rounded-xl bg-blue-700 text-[16px] font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
          >
            {busy ? 'Working…' : 'Register'}
          </button>
        </>
      )}

      {confirmed && error && (
        <div role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2.5 text-sm font-medium text-red-800">
          {error}
        </div>
      )}

      {/* ---- activity ---- */}
      <section className="mt-8">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
          Activity
        </h2>
        <ol className="mt-3 space-y-3">
          {events.map((event) => (
            <li key={event.id}>
              <p className="whitespace-pre-line text-[14px] leading-snug text-gray-800">
                <LinkifiedText text={event.body} />
              </p>
              <p className="mt-0.5 text-xs text-gray-400">
                {shortName(event.actor_email)} · {relativeTime(event.created_at)}
              </p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
