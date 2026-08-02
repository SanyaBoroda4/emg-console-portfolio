import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, fetchItemCard, sendComment, sendDecision } from '../api'
import Lightbox from '../components/Lightbox'
import { formatAmount, relativeTime, statusBadgeClass } from '../lib/format'
import type { Candidate, ItemCard, ItemEvent } from '../types'

/** The decision card (decision flow §7) — a manager's whole interaction with
 * one payment, reached from a push notification on a phone. The signature
 * element is the DECISION SLIP: the bot's question and its eventual answer
 * occupy one continuous slot whose accent rail turns amber → green, so the
 * question visibly *becomes* its resolution. Everything else stays quiet. */

const POLL_MS = 5000
const ARM_TIMEOUT_MS = 4000

function shortName(email: string | null): string {
  if (!email) return 'CHECK-BOT'
  const local = email.split('@')[0]
  return local.charAt(0).toUpperCase() + local.slice(1)
}

/** Bot text can carry URLs (the finished duplicate's card, Moraware jobs) —
 * render them tappable. Same-origin card links stay in the app (no new tab). */
function LinkifiedText({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/[^\s]+)/g)
  return (
    <>
      {parts.map((part, i) => {
        if (!/^https?:\/\//.test(part)) return <span key={i}>{part}</span>
        const sameOrigin = part.startsWith(window.location.origin)
        const href = sameOrigin ? part.slice(window.location.origin.length) : part
        return sameOrigin ? (
          <Link
            key={i}
            to={href}
            className="break-all font-medium text-blue-700 underline decoration-blue-300 underline-offset-2 dark:text-blue-400 dark:decoration-blue-700"
          >
            {part}
          </Link>
        ) : (
          <a
            key={i}
            href={part}
            target="_blank"
            rel="noreferrer"
            className="break-all font-medium text-blue-700 underline decoration-blue-300 underline-offset-2 dark:text-blue-400 dark:decoration-blue-700"
          >
            {part}
          </a>
        )
      })}
    </>
  )
}

// --- feed iconography: each kind gets a fixed glyph + hue, so the eye can
// walk the rail without reading (icon-led lines, §7) -------------------------

const KIND_STYLE: Record<ItemEvent['kind'], { circle: string; glyph: JSX.Element }> = {
  system: {
    circle: 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
    glyph: <circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none" />,
  },
  bot_update: {
    circle: 'bg-sky-100 text-sky-700 dark:bg-sky-900 dark:text-sky-300',
    // document-scan lines: the bot reporting what it read
    glyph: <path d="M6 7h12M6 12h12M6 17h7" />,
  },
  bot_question: {
    circle: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300',
    glyph: <path d="M9 9.5a3 3 0 1 1 4.2 2.8c-.9.4-1.2 1-1.2 1.9m0 3.3h.01" />,
  },
  decision: {
    circle: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300',
    glyph: <path d="m6 12.5 4 4L18 8" />,
  },
  comment: {
    circle: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300',
    glyph: <path d="M20 12a8 8 0 1 0-3 6.2L20 19l-.6-2.6A7.9 7.9 0 0 0 20 12Z" />,
  },
}

function FeedIcon({ kind }: { kind: ItemEvent['kind'] }) {
  const style = KIND_STYLE[kind]
  return (
    <span
      className={`absolute -left-[39px] top-0.5 flex h-7 w-7 items-center justify-center rounded-full ring-4 ring-gray-50 dark:ring-gray-950 ${style.circle}`}
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {style.glyph}
      </svg>
    </span>
  )
}

/** Mounts with a one-time rise-and-settle — the §7 "motion limited to state
 * transitions": this is the only animated thing on the page. */
function ResolvedBanner({ decision }: { decision: ItemEvent }) {
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    const raf = requestAnimationFrame(() => setSettled(true))
    return () => cancelAnimationFrame(raf)
  }, [])
  return (
    <div
      className={`rounded-xl border-l-4 border-green-500 bg-green-50 p-4 motion-safe:transition-all motion-safe:duration-300 dark:border-green-400 dark:bg-green-950/40 ${
        settled ? 'translate-y-0 opacity-100' : 'motion-safe:translate-y-2 motion-safe:opacity-0'
      }`}
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-green-600 text-white dark:bg-green-500">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m6 12.5 4 4L18 8" />
          </svg>
        </span>
        <div>
          <p className="whitespace-pre-line text-[17px] font-semibold text-green-900 dark:text-green-200">
            <LinkifiedText text={decision.body} />
          </p>
          <p className="mt-0.5 text-sm text-green-700 dark:text-green-400">
            {shortName(decision.actor_email)}, {relativeTime(decision.created_at)}
          </p>
        </div>
      </div>
    </div>
  )
}

export default function ItemCardPage() {
  const { id } = useParams<{ id: string }>()
  const [card, setCard] = useState<ItemCard | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  // Decision state
  const [armed, setArmed] = useState<number | 'freeform' | null>(null)
  const armTimer = useRef<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [decideError, setDecideError] = useState<string | null>(null)
  const [race, setRace] = useState<{ by: string; at: string } | null>(null)

  const [freeform, setFreeform] = useState('')
  const [comment, setComment] = useState('')
  const [commentBusy, setCommentBusy] = useState(false)
  const [lightbox, setLightbox] = useState(false)
  const [showAllActivity, setShowAllActivity] = useState(false)

  const load = useCallback(async () => {
    if (!id) return
    try {
      setCard(await fetchItemCard(id))
      setLoadError(null)
    } catch {
      setLoadError("This payment couldn't be loaded — it may have been deleted.")
    }
  }, [id])

  // Initial load + the 5s liveness poll (feed grows, resolution appears).
  useEffect(() => {
    void load().finally(() => setLoading(false))
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [load])

  function disarm() {
    if (armTimer.current) window.clearTimeout(armTimer.current)
    setArmed(null)
  }

  function arm(key: number | 'freeform') {
    if (armTimer.current) window.clearTimeout(armTimer.current)
    setArmed(key)
    armTimer.current = window.setTimeout(() => setArmed(null), ARM_TIMEOUT_MS)
  }

  async function decide(payload: Parameters<typeof sendDecision>[1]) {
    if (!id) return
    setSubmitting(true)
    setDecideError(null)
    try {
      await sendDecision(id, payload)
      setFreeform('') // don't leak this round's text into a follow-up question
      await load() // the decision (and soon the workflow's outcome) appears
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.body) {
        // Race loser: name the winner calmly; the refetch renders resolved.
        setRace({
          by: String(err.body.decided_by ?? 'someone'),
          at: String(err.body.decided_at ?? ''),
        })
        await load()
      } else if (err instanceof ApiError && err.status === 502) {
        setDecideError(`${err.message} Your options are still open.`)
      } else {
        setDecideError(err instanceof Error ? err.message : 'Something went wrong.')
      }
    } finally {
      setSubmitting(false)
      disarm()
    }
  }

  async function submitComment() {
    if (!id || !comment.trim()) return
    setCommentBusy(true)
    try {
      await sendComment(id, comment.trim())
      setComment('')
      await load()
    } catch (err) {
      setDecideError(err instanceof Error ? err.message : 'Could not add the comment.')
    } finally {
      setCommentBusy(false)
    }
  }

  // ---- derived card state ----
  const item = card?.item ?? null
  const events = card?.events ?? []
  // Multi-round Q&A: the slip shows the LATEST question and the decision
  // paired to it — an answered round 1 must not mask an open round 2.
  const question = [...events].reverse().find((e) => e.kind === 'bot_question') ?? null
  const decision =
    (question &&
      events.find(
        (e) => e.kind === 'decision' && e.answers_event_id === question.id,
      )) ??
    null
  const open = question !== null && decision === null
  const candidates: Candidate[] = question?.payload?.candidates ?? []
  const allowFreeform = question?.payload?.allowed_freeform ?? true
  const submittedBy =
    events.find((e) => e.kind === 'system' && e.actor_email)?.actor_email ?? null
  const details = item?.payment_details ?? null
  const amount = formatAmount(details?.amount ?? null)
  const payer = details?.payer_name || details?.caption_name || null
  const processing = item?.status === 'processing' && !open

  // The feed's spine, not its every breath: questions, answers, comments,
  // plus the first and latest lines. The rest folds behind "Show all".
  const IMPORTANT = ['bot_question', 'decision', 'comment']
  const visibleEvents = showAllActivity
    ? events
    : events.filter(
        (e, i) =>
          i === 0 || i === events.length - 1 || IMPORTANT.includes(e.kind),
      )
  const hiddenCount = events.length - visibleEvents.length

  return (
    // The page paints its own canvas edge-to-edge (incl. dark mode) without
    // touching the shared Layout: negative margins cancel <main>'s padding.
    <div className="-mx-4 -my-6 min-h-screen bg-gray-50 px-4 py-6 dark:bg-gray-950 sm:-mx-6 sm:px-6">
      <div className="mx-auto max-w-xl">
        <Link
          to="/payments"
          className="inline-flex min-h-11 items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-gray-400 dark:hover:text-gray-200"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M15 18l-6-6 6-6" />
          </svg>
          Payments
        </Link>

        {loading ? (
          <p role="status" className="mt-10 text-center text-gray-500">
            Loading…
          </p>
        ) : loadError || !item ? (
          <div role="alert" className="mt-10 rounded-xl bg-red-50 p-6 text-center dark:bg-red-950/40">
            <p className="font-medium text-red-800 dark:text-red-300">
              {loadError ?? 'Not found.'}
            </p>
          </div>
        ) : (
          <>
            {/* ---- header: the amount is the hero ---- */}
            <header className="mt-3 flex items-start justify-between gap-3">
              <div>
                <p className="text-4xl font-bold tracking-tight text-gray-900 tabular-nums dark:text-gray-50">
                  {amount ?? <span className="text-2xl font-medium italic text-gray-400">amount unreadable</span>}
                </p>
                {payer && (
                  <p className="mt-1 text-[15px] font-medium text-gray-600 dark:text-gray-300">
                    {payer}
                  </p>
                )}
              </div>
              <span
                className={`mt-1.5 shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${statusBadgeClass(item.status)}`}
              >
                {item.status}
              </span>
            </header>

            {/* ---- photo + facts ---- */}
            <section className="mt-5 flex gap-4 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
              {item.photo_path && (
                <button
                  type="button"
                  onClick={() => setLightbox(true)}
                  aria-label="Open check photo"
                  className="h-24 w-20 shrink-0 overflow-hidden rounded-lg border border-gray-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-gray-700"
                >
                  <img
                    src={`/api/photos/${item.id}`}
                    alt="Check"
                    className="h-full w-full object-cover"
                  />
                </button>
              )}
              <dl className="grid flex-1 grid-cols-2 gap-x-4 gap-y-3">
                {(
                  [
                    ['Payer', payer],
                    ['Check #', details?.check_number],
                    ['Submitted by', submittedBy ? shortName(submittedBy) : null],
                    // The bot's matched invoice wins; the manager-typed
                    // qb_invoice is the fallback until the workflow reports.
                    ['Invoice #', details?.invoice_number ?? details?.qb_invoice ?? 'None entered'],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
                      {label}
                    </dt>
                    <dd
                      className={`mt-0.5 text-[15px] font-medium ${
                        value && value !== 'None entered'
                          ? 'text-gray-900 dark:text-gray-100'
                          : 'text-gray-400 dark:text-gray-600'
                      }`}
                    >
                      {value ?? '—'}
                    </dd>
                  </div>
                ))}
                {/* The matched job spans the row — it's the payment's answer. */}
                <div className="col-span-2">
                  <dt className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
                    Job
                  </dt>
                  <dd className="mt-0.5 text-[15px] font-medium">
                    {item.matched_job_name ? (
                      item.moraware_url ? (
                        <a
                          href={item.moraware_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-blue-700 underline decoration-blue-300 underline-offset-2 hover:text-blue-900 dark:text-blue-400 dark:decoration-blue-700 dark:hover:text-blue-300"
                        >
                          {item.matched_job_name}
                        </a>
                      ) : (
                        <span className="text-gray-900 dark:text-gray-100">
                          {item.matched_job_name}
                        </span>
                      )
                    ) : (
                      <span className="text-gray-400 dark:text-gray-600">
                        No job matched yet
                      </span>
                    )}
                  </dd>
                </div>
              </dl>
            </section>

            {/* ---- THE DECISION SLIP: question → answer, one slot ---- */}
            <section className="mt-4" aria-live="polite">
              {race && decision && (
                <p className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                  {shortName(race.by)} got there first — here's what they chose.
                </p>
              )}

              {processing && (
                <div className="mb-2 flex items-center gap-3 rounded-xl border-l-4 border-purple-400 bg-purple-50/70 p-4 dark:border-purple-500 dark:bg-purple-950/30">
                  <span className="relative flex h-2.5 w-2.5 shrink-0">
                    <span className="absolute inline-flex h-full w-full rounded-full bg-purple-500 opacity-75 motion-safe:animate-ping" />
                    <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-purple-600" />
                  </span>
                  <p className="text-[15px] font-medium text-purple-900 dark:text-purple-200">
                    CHECK-BOT is working on this check — updates appear here
                    automatically.
                  </p>
                </div>
              )}

              {decision ? (
                <ResolvedBanner decision={decision} />
              ) : open && question ? (
                <div className="rounded-xl border-l-4 border-amber-400 bg-amber-50/70 p-4 dark:border-amber-500 dark:bg-amber-950/30">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
                    Needs a decision
                  </p>
                  <p className="mt-1.5 text-[17px] font-medium leading-snug text-gray-900 dark:text-gray-100">
                    <LinkifiedText text={question.body} />
                  </p>

                  <div className="mt-4 space-y-2.5">
                    {candidates.map((candidate, index) => {
                      const isArmed = armed === index
                      return (
                        <div key={index}>
                        <button
                          type="button"
                          disabled={submitting}
                          onClick={() =>
                            isArmed
                              ? void decide({
                                  choice: { label: candidate.label, job_id: candidate.job_id },
                                })
                              : arm(index)
                          }
                          className={`block min-h-[56px] w-full rounded-xl border-2 bg-white px-4 py-3 text-left shadow-sm transition-colors disabled:opacity-50 dark:bg-gray-900 ${
                            isArmed
                              ? 'border-blue-600 dark:border-blue-500'
                              : 'border-gray-200 hover:border-gray-300 dark:border-gray-700 dark:hover:border-gray-600'
                          } focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600`}
                        >
                          <span className="block text-[16px] font-semibold text-gray-900 dark:text-gray-100">
                            {candidate.label}
                          </span>
                          <span
                            className={`mt-0.5 block text-[13px] ${
                              isArmed
                                ? 'font-semibold text-blue-700 dark:text-blue-400'
                                : 'text-gray-500 dark:text-gray-400'
                            }`}
                          >
                            {isArmed
                              ? 'Tap again to confirm'
                              : candidate.sublabel ?? ' '}
                          </span>
                        </button>
                        {candidate.moraware_url && (
                          <a
                            href={candidate.moraware_url}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-1 inline-flex min-h-8 items-center gap-1 px-1 text-[13px] font-medium text-blue-700 underline decoration-blue-300 underline-offset-2 hover:text-blue-900 dark:text-blue-400 dark:decoration-blue-700 dark:hover:text-blue-300"
                          >
                            Double-check in Moraware
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                              <path d="M7 17 17 7M9 7h8v8" />
                            </svg>
                          </a>
                        )}
                        </div>
                      )
                    })}
                  </div>

                  {allowFreeform && (
                    <div className="mt-4 border-t border-amber-200/70 pt-3 dark:border-amber-900">
                      <p className="text-[13px] font-medium text-gray-600 dark:text-gray-400">
                        None of these?
                      </p>
                      <div className="mt-2 flex gap-2">
                        <input
                          type="text"
                          value={freeform}
                          onChange={(e) => setFreeform(e.target.value)}
                          placeholder={question.payload?.format_hint || 'Type your answer'}
                          className="min-h-[52px] w-full rounded-xl border-2 border-gray-200 bg-white px-3.5 text-[15px] text-gray-900 placeholder:text-gray-400 focus:border-blue-600 focus:outline-none dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
                        />
                        <button
                          type="button"
                          disabled={submitting || !freeform.trim()}
                          onClick={() => void decide({ text: freeform.trim() })}
                          className="min-h-[52px] shrink-0 rounded-xl bg-blue-700 px-5 text-[15px] font-semibold text-white hover:bg-blue-800 disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                        >
                          Send
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ) : null}

              {decideError && (
                <div
                  role="alert"
                  className="mt-2 rounded-lg bg-red-50 px-3 py-2.5 text-sm font-medium text-red-800 dark:bg-red-950/40 dark:text-red-300"
                >
                  {decideError}
                </div>
              )}
            </section>

            {/* ---- activity: the ledger spine ---- */}
            <section className="mt-6">
              <h2 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
                Activity
              </h2>
              <ol className="relative ml-3.5 mt-3 space-y-5 border-l-2 border-gray-200 pl-6 dark:border-gray-800">
                {visibleEvents.map((event) => (
                  <li key={event.id} className="relative">
                    <FeedIcon kind={event.kind} />
                    <p className="whitespace-pre-line text-[15px] leading-snug text-gray-800 dark:text-gray-200">
                      <LinkifiedText text={event.body} />
                    </p>
                    <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">
                      {shortName(event.actor_email)} · {relativeTime(event.created_at)}
                    </p>
                  </li>
                ))}
              </ol>
              {(hiddenCount > 0 || showAllActivity) && (
                <button
                  type="button"
                  onClick={() => setShowAllActivity((v) => !v)}
                  className="ml-9 mt-3 min-h-8 text-[13px] font-medium text-gray-500 underline decoration-gray-300 underline-offset-2 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
                >
                  {showAllActivity
                    ? 'Show less'
                    : `Show all activity (${hiddenCount} more)`}
                </button>
              )}
            </section>

            {/* ---- comment box ---- */}
            <section className="mt-6 flex gap-2 pb-8">
              <input
                type="text"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void submitComment()
                }}
                placeholder="Add a comment…"
                className="min-h-[52px] w-full rounded-xl border border-gray-300 bg-white px-3.5 text-[15px] text-gray-900 placeholder:text-gray-400 focus:border-blue-600 focus:outline-none dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
              />
              <button
                type="button"
                disabled={commentBusy || !comment.trim()}
                onClick={() => void submitComment()}
                className="min-h-[52px] shrink-0 rounded-xl border border-gray-300 px-5 text-[15px] font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
              >
                Post
              </button>
            </section>
          </>
        )}

        {lightbox && item && (
          <Lightbox
            imageUrl={item.photo_path ? `/api/photos/${item.id}` : null}
            driveUrl={item.photo_drive_url}
            onClose={() => setLightbox(false)}
          />
        )}
      </div>
    </div>
  )
}
