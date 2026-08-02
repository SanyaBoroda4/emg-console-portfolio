import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  assignMaterial,
  confirmDelivery,
  fetchItemCard,
  resendDelivery,
  sendComment,
  setDeliveryMode,
} from '../api'
import Lightbox from '../components/Lightbox'
import JobPicker from '../components/JobPicker'
import { deliveryStatus, formatAmount, relativeTime } from '../lib/format'
import LinkifiedText from '../components/LinkifiedText'
import type { DeliveryMaterial, ItemCard, JobHit } from '../types'

/** The delivery card (slab chapter): the manager ACTS here — poll
 * (one/split), typeahead per material, Stock always a tap away. One slip =
 * one card = one push; the workflow only reads, files, and notes. */

const POLL_MS = 5000

function shortName(email: string | null): string {
  if (!email) return 'SLABBOT'
  const local = email.split('@')[0]
  return local.charAt(0).toUpperCase() + local.slice(1)
}

export default function DeliveryCardPage() {
  const { id } = useParams<{ id: string }>()
  const [card, setCard] = useState<ItemCard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [comment, setComment] = useState('')
  const [lightbox, setLightbox] = useState(false)

  const load = useCallback(async () => {
    if (!id) return
    try {
      setCard(await fetchItemCard(id))
    } catch {
      setError("This delivery couldn't be loaded — it may have been deleted.")
    }
  }, [id])

  useEffect(() => {
    void load().finally(() => setLoading(false))
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [load])

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message
        : err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  const item = card?.item ?? null
  const events = card?.events ?? []
  const details = item?.delivery_details ?? null
  const materials: DeliveryMaterial[] = details?.materials ?? []
  const mode = details?.assignment_mode ?? null
  const unassigned = materials.filter((m) => !m.stock && !m.job_id)
  const distinctMaterials = new Set(materials.map((m) => m.material)).size
  const resolved = ['confirmed', 'stock', 'complete'].includes(item?.status ?? '')
  const filing = item?.status === 'filing'
  // Upload -> outbound send takes a couple of seconds; don't alarm the user
  // (or offer Resend) unless the item is genuinely stuck.
  const ageSeconds = item ? (Date.now() - new Date(item.created_at).getTime()) / 1000 : 0
  const stuckSubmitted = item?.status === 'submitted' && ageSeconds > 30
  const updatedAgo = item ? (Date.now() - new Date(item.updated_at).getTime()) / 1000 : 0
  const stuckFiling = filing && updatedAgo > 90
  const needsPoll = materials.length > 1 && distinctMaterials > 1 && mode === null && !filing
  const processing = !resolved && materials.length === 0 && item !== null
  const allAssigned = materials.length > 0 && unassigned.length === 0
  // In split mode the card walks material by material.
  const activeIndex = materials.findIndex((m) => !m.stock && !m.job_id)

  function assign(index: number | null, job: JobHit | null, stock = false) {
    void act(() =>
      assignMaterial(id!, {
        material_index: index,
        stock,
        job_id: job ? String(job.job_id) : undefined,
        job_name: job?.customer_name,
        moraware_url: job?.lead_url ?? null,
      }),
    )
  }

  return (
    <div className="-mx-4 -my-6 min-h-screen bg-gray-50 px-4 py-6 dark:bg-gray-950 sm:-mx-6 sm:px-6">
      <div className="mx-auto max-w-xl">
        <Link
          to="/deliveries"
          className="inline-flex min-h-11 items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M15 18l-6-6 6-6" />
          </svg>
          Deliveries
        </Link>

        {loading ? (
          <p role="status" className="mt-10 text-center text-gray-500">Loading…</p>
        ) : !item ? (
          <div role="alert" className="mt-10 rounded-xl bg-red-50 p-6 text-center dark:bg-red-950/40">
            <p className="font-medium text-red-800 dark:text-red-300">{error ?? 'Not found.'}</p>
          </div>
        ) : (
          <>
            {/* ---- header ---- */}
            <header className="mt-3 flex items-start justify-between gap-3">
              <div>
                <p className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-50">
                  {details?.supplier ?? 'Reading slip…'}
                </p>
                <p className="mt-1 text-[15px] font-medium text-gray-600 dark:text-gray-300">
                  {[details?.document_number,
                    details?.slab_count != null ? `${details.slab_count} slabs` : null,
                    details?.total ? formatAmount(details.total) : null]
                    .filter(Boolean).join(' · ') || '—'}
                </p>
              </div>
              <span className={`mt-1.5 shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${deliveryStatus(item.status).klass}`}>
                {deliveryStatus(item.status).label}
              </span>
            </header>

            {/* ---- photo + validation ---- */}
            <section className="mt-5 flex gap-4 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
              {item.photo_path && (
                <button
                  type="button"
                  onClick={() => setLightbox(true)}
                  aria-label="Open slip photo"
                  className="h-28 w-20 shrink-0 overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700"
                >
                  <img src={`/api/photos/${item.id}`} alt="Delivery slip" className="h-full w-full object-cover" />
                </button>
              )}
              <div className="min-w-0 text-[14px] text-gray-700 dark:text-gray-300">
                {details?.validation_note && <p>{details.validation_note}</p>}
                {details?.hand_notes && (
                  <p className="mt-1"><span className="font-semibold">Handwritten:</span> {details.hand_notes}</p>
                )}
                {details?.order_date && (
                  <p className="mt-1 text-gray-500 dark:text-gray-400">Slip date {details.order_date}</p>
                )}
              </div>
            </section>

            {processing && (
              <div className="mt-4 rounded-xl border-l-4 border-purple-400 bg-purple-50/70 p-4 dark:border-purple-500 dark:bg-purple-950/30">
                <div className="flex items-center gap-3">
                  <span className="relative flex h-2.5 w-2.5 shrink-0">
                    <span className="absolute inline-flex h-full w-full rounded-full bg-purple-500 opacity-75 motion-safe:animate-ping" />
                    <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-purple-600" />
                  </span>
                  <p className="text-[15px] font-medium text-purple-900 dark:text-purple-200">
                    {stuckSubmitted
                      ? "Couldn't reach SLABBOT — resend below."
                      : item.status === 'submitted'
                        ? 'Sending to SLABBOT…'
                        : 'SLABBOT is reading the slip — materials appear here automatically.'}
                  </p>
                </div>
                {stuckSubmitted && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void act(() => resendDelivery(id!))}
                    className="mt-3 min-h-11 w-full rounded-lg bg-purple-700 text-[14px] font-semibold text-white hover:bg-purple-800 disabled:opacity-40"
                  >
                    Resend to SLABBOT
                  </button>
                )}
              </div>
            )}

            {/* ---- the poll: one job or split? ---- */}
            {needsPoll && !resolved && (
              <section className="mt-4 rounded-xl border-l-4 border-amber-400 bg-amber-50/70 p-4 dark:border-amber-500 dark:bg-amber-950/30">
                <p className="text-[16px] font-medium text-gray-900 dark:text-gray-100">
                  {distinctMaterials} different materials on this slip — all for ONE
                  job, or different jobs?
                </p>
                <div className="mt-3 grid grid-cols-2 gap-2.5">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void act(() => setDeliveryMode(id!, 'one'))}
                    className="min-h-[56px] rounded-xl border-2 border-gray-200 bg-white text-[15px] font-semibold text-gray-900 hover:border-blue-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
                  >
                    One job
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void act(() => setDeliveryMode(id!, 'split'))}
                    className="min-h-[56px] rounded-xl border-2 border-gray-200 bg-white text-[15px] font-semibold text-gray-900 hover:border-blue-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
                  >
                    Different jobs
                  </button>
                </div>
              </section>
            )}

            {/* ---- materials panel ---- */}
            {materials.length > 0 && (
              <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
                <h2 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
                  Materials ({materials.length})
                </h2>
                <ul className="mt-2 space-y-3">
                  {materials.map((m, index) => {
                    const done = m.stock || m.job_id
                    const isActive =
                      !resolved && !filing && !needsPoll &&
                      ((mode === 'split' && index === activeIndex) ||
                        (mode !== 'split' && materials.length === 1 && !done))
                    return (
                      <li
                        key={index}
                        className={`rounded-lg border p-3 ${
                          isActive
                            ? 'border-amber-400 bg-amber-50/60 dark:border-amber-500 dark:bg-amber-950/30'
                            : 'border-gray-100 dark:border-gray-800'
                        }`}
                      >
                        <div className="flex items-baseline justify-between gap-2">
                          <p className="text-[15px] font-semibold text-gray-900 dark:text-gray-100">
                            {m.material}
                            {m.slab_count != null && (
                              <span className="font-normal text-gray-500"> ×{m.slab_count}</span>
                            )}
                          </p>
                          {m.total_sf != null && (
                            <span className="text-xs text-gray-400">{m.total_sf} sf</span>
                          )}
                        </div>
                        {(m.finish || m.thickness) && (
                          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                            {[m.thickness, m.finish].filter(Boolean).join(' · ')}
                          </p>
                        )}
                        <div className="mt-1.5 text-[14px]">
                          {m.stock ? (
                            <span className="font-medium text-gray-700 dark:text-gray-300">→ Stock</span>
                          ) : m.job_id ? (
                            <span className="font-medium text-green-700 dark:text-green-400">
                              → {m.moraware_url ? (
                                <a href={m.moraware_url} target="_blank" rel="noreferrer" className="underline decoration-green-300 underline-offset-2">
                                  {m.job_name}
                                </a>
                              ) : m.job_name}
                            </span>
                          ) : isActive ? (
                            <div className="mt-1">
                              <JobPicker
                                busy={busy}
                                onPick={(job) => assign(index, job)}
                                onStock={() => assign(index, null, true)}
                              />
                            </div>
                          ) : (
                            <span className="text-gray-400 dark:text-gray-600">→ waiting</span>
                          )}
                        </div>
                      </li>
                    )
                  })}
                </ul>

                {/* one-job mode: a single picker assigns everything. Also the
                    default when every line is the SAME material (no poll). */}
                {!resolved && !filing &&
                  (mode === 'one' ||
                    (mode === null && materials.length > 1 && distinctMaterials === 1)) &&
                  unassigned.length > 0 && (
                  <div className="mt-3 border-t border-gray-100 pt-3 dark:border-gray-800">
                    <p className="mb-1.5 text-[13px] font-medium text-gray-600 dark:text-gray-400">
                      One job for all {materials.length} materials:
                    </p>
                    <JobPicker
                      autoFocus
                      busy={busy}
                      onPick={(job) => assign(null, job)}
                      onStock={() => assign(null, null, true)}
                    />
                  </div>
                )}

                {!resolved && !filing && allAssigned && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void act(() => confirmDelivery(id!))}
                    className="mt-4 min-h-[52px] w-full rounded-xl bg-blue-700 text-[15px] font-semibold text-white hover:bg-blue-800 disabled:opacity-40"
                  >
                    {busy ? 'Registering…' : 'Register'}
                  </button>
                )}
              </section>
            )}

            {filing && (
              <div className="mt-4 flex items-center gap-3 rounded-xl border-l-4 border-purple-400 bg-purple-50/70 p-4 dark:border-purple-500 dark:bg-purple-950/30">
                <span className="relative flex h-2.5 w-2.5 shrink-0">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-purple-500 opacity-75 motion-safe:animate-ping" />
                  <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-purple-600" />
                </span>
                <p className="text-[15px] font-medium text-purple-900 dark:text-purple-200">
                  Registered — filing to Moraware. This card updates automatically.
                </p>
                {stuckFiling && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void act(() => confirmDelivery(id!))}
                    className="mt-3 min-h-11 w-full rounded-lg bg-purple-700 text-[14px] font-semibold text-white hover:bg-purple-800 disabled:opacity-40"
                  >
                    Taking long — Register again
                  </button>
                )}
              </div>
            )}

            {resolved && (
              <div className="mt-4 rounded-xl border-l-4 border-green-500 bg-green-50 p-4 dark:border-green-400 dark:bg-green-950/40">
                <p className="text-[16px] font-semibold text-green-900 dark:text-green-200">
                  ✓ Filed — {item.matched_job_name ?? 'done'}
                </p>
              </div>
            )}

            {error && (
              <div role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2.5 text-sm font-medium text-red-800 dark:bg-red-950/40 dark:text-red-300">
                {error}
              </div>
            )}

            {/* ---- activity ---- */}
            <section className="mt-6">
              <h2 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
                Activity
              </h2>
              <ol className="mt-3 space-y-3">
                {events.map((event) => (
                  <li key={event.id}>
                    <p className="whitespace-pre-line text-[14px] leading-snug text-gray-800 dark:text-gray-200">
                      <LinkifiedText text={event.body} />
                    </p>
                    <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">
                      {shortName(event.actor_email)} · {relativeTime(event.created_at)}
                    </p>
                  </li>
                ))}
              </ol>
            </section>

            {/* ---- comment ---- */}
            <section className="mt-6 flex gap-2 pb-8">
              <input
                type="text"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && comment.trim()) {
                    void act(async () => {
                      await sendComment(id!, comment.trim())
                      setComment('')
                    })
                  }
                }}
                placeholder="Add a comment…"
                className="min-h-[52px] w-full rounded-xl border border-gray-300 bg-white px-3.5 text-[15px] text-gray-900 placeholder:text-gray-400 focus:border-blue-600 focus:outline-none dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
              />
              <button
                type="button"
                disabled={busy || !comment.trim()}
                onClick={() =>
                  void act(async () => {
                    await sendComment(id!, comment.trim())
                    setComment('')
                  })
                }
                className="min-h-[52px] shrink-0 rounded-xl border border-gray-300 px-5 text-[15px] font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-40 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
              >
                Post
              </button>
            </section>
          </>
        )}

        {lightbox && item && (
          <Lightbox
            imageUrl={item.photo_path ? `/api/photos/${item.id}` : null}
            driveUrl={item.delivery_details?.drive_url ?? null}
            onClose={() => setLightbox(false)}
          />
        )}
      </div>
    </div>
  )
}
