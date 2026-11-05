import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'
import { DPC_GARAGES, FINAL_STATES, dpc, eventTimeNow, type EntryRequest, type GarageState } from './dpc'
import { AlertIcon, CheckCircle, Spinner, Wifi } from './icons'
import { time } from './format'

const TZ = 'Europe/Kyiv'
const POLL_MS = 1000
const MAX_ATTEMPTS = 5

/** Why the DPC service refused an entry or an exit (reason of a rejected/failed request). */
const REASONS: Record<string, string> = {
  full: 'No free spot for a car without a booking',
  'booking-invalid': 'The booking does not allow this entry',
  'already-entered': 'This booking was already used for an entry',
  'already-inside': 'This car is already inside',
  'not-inside': 'This car is not inside, so it cannot exit',
  'unknown-garage': 'Unknown garage',
  error: 'Processing error',
  'lost-on-restart': 'Lost when the service restarted',
}

function reasonText(r: EntryRequest): string | null {
  if (!r.reason) return null
  return REASONS[r.reason] ?? r.reason
}

/** Problem Details of the DPC api in plain words. */
function DpcProblem({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 0) {
    return (
      <div className="alert alert-warn" role="status">
        <Wifi color="var(--accent)" />
        <span>The DPC service is unavailable. Start it with docker compose up -d api.</span>
      </div>
    )
  }
  const e = error instanceof ApiError ? error : null
  const slug = (e?.problem.type ?? '').split('/').pop()
  const text: Record<string, [string, string]> = {
    'queue-full': ['The queue is full', 'The event was not accepted. Try again in a second.'],
    'shutting-down': ['The service is shutting down', 'Accepted events are being finished. Send this one again later.'],
    'garage-not-found': ['Unknown garage', 'Pick a garage from the list.'],
    'event-id-reused': ['Event id already used', 'This event id belongs to another event.'],
    'invalid-request': ['Check the form', 'Some fields are filled in incorrectly.'],
    unauthorized: ['Wrong API token', 'Set VITE_DPC_TOKEN in web/client/.env.local.'],
  }
  const [title, detail] = text[slug ?? ''] ?? [e?.problem.title ?? 'Something went wrong', e?.problem.detail ?? String(error)]
  return (
    <div className="alert alert-danger" role="alert">
      <AlertIcon color="var(--danger)" size={20} />
      <div className="alert-body">
        <strong>{title}</strong>
        <span>{detail}</span>
      </div>
    </div>
  )
}

function GarageCard({ garage, error }: { garage: GarageState | null; error: unknown }) {
  return (
    <section className="card section garage-state" aria-labelledby="garage-state-title">
      <div className="row-between">
        <h2 id="garage-state-title" className="eyebrow">
          Garage state
        </h2>
        {garage && (
          <span className={`badge ${garage.invariant_ok ? 'confirmed' : 'danger'}`}>
            {garage.invariant_ok ? 'Invariant holds' : 'Invariant broken'}
          </span>
        )}
      </div>
      {error != null && !garage && <DpcProblem error={error} />}
      {garage && (
        <>
          <div className="occupancy" aria-hidden>
            <div
              className="occupancy-bar"
              style={{ width: `${Math.min(100, (garage.occupied / garage.spots) * 100)}%` }}
            />
            <div
              className="occupancy-reserved"
              style={{ width: `${Math.min(100, (garage.reserved / garage.spots) * 100)}%` }}
            />
          </div>
          <dl className="kv">
            <div>
              <dt>Spots</dt>
              <dd className="mono">{garage.spots}</dd>
            </div>
            <div>
              <dt>Occupied</dt>
              <dd className="mono">{garage.occupied}</dd>
            </div>
            <div>
              <dt>Cars inside (entries − exits)</dt>
              <dd className="mono">{garage.inside}</dd>
            </div>
            <div>
              <dt>Reserved by bookings now</dt>
              <dd className="mono">{garage.reserved}</dd>
            </div>
            <div>
              <dt>Free for cars without a booking</dt>
              <dd className="mono">{garage.free_walk_in}</dd>
            </div>
            {garage.mode === 'unsafe' && (
              <div>
                <dt>Mode</dt>
                <dd>without locking (test)</dd>
              </div>
            )}
          </dl>
          <p className="small muted">Updated every second.</p>
        </>
      )}
    </section>
  )
}

function RequestRow({ r }: { r: EntryRequest }) {
  const reason = reasonText(r)
  return (
    <li className={`card entry-row state-${r.state}`}>
      <div className="entry-main">
        <span className="entry-title">
          <span className="mono plate-tag">{r.plate}</span>
          <span>{r.direction === 'in' ? 'Entry' : 'Exit'}</span>
          {r.booking_id && <span className="muted small">with booking</span>}
        </span>
        <span className="muted small">
          {time(r.received_at, TZ)} · event {r.event_id.slice(0, 8)}
          {r.total_ms != null && ` · ${Math.round(r.total_ms)} ms`}
        </span>
        {reason && (
          <span className="entry-reason">
            {reason}
            {r.detail && <span className="muted"> ({r.detail})</span>}
          </span>
        )}
      </div>
      <span className={`badge state ${r.state}`}>{r.state}</span>
    </li>
  )
}

export default function BarrierPage({
  params,
  setParams,
}: {
  params: URLSearchParams
  setParams: (patch: Record<string, string | undefined>, mode?: 'push' | 'replace') => void
}) {
  const garageId = DPC_GARAGES.some((g) => g.id === params.get('garage')) ? (params.get('garage') as string) : 'center'
  const [plate, setPlate] = useState('')
  const [direction, setDirection] = useState<'in' | 'out'>('in')
  const [bookingId, setBookingId] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [last, setLast] = useState<EntryRequest | null>(null)
  const [garage, setGarage] = useState<GarageState | null>(null)
  const [garageError, setGarageError] = useState<unknown>(null)
  const [recent, setRecent] = useState<EntryRequest[]>([])
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const [g, items] = await Promise.all([dpc.garage(garageId), dpc.recent(garageId)])
      if (!alive.current) return
      setGarage(g)
      setRecent(items)
      setGarageError(null)
    } catch (err) {
      if (alive.current) setGarageError(err)
    }
  }, [garageId])

  useEffect(() => {
    alive.current = true
    setGarage(null)
    void refresh()
    const timer = setInterval(() => void refresh(), POLL_MS)
    return () => {
      alive.current = false
      clearInterval(timer)
    }
  }, [refresh])

  async function send(e: React.FormEvent) {
    e.preventDefault()
    const p = plate.replace(/[\s-]+/g, '').toUpperCase()
    if (p.length < 2 || p.length > 12) {
      setError(new ApiError(400, { type: 'invalid-request', title: 'Check the form' }, null))
      return
    }
    setBusy(true)
    setError(null)
    setNote(null)
    setLast(null)
    // one event of the barrier = one event_id, kept in every repeat after 429
    const body = {
      event_id: crypto.randomUUID(),
      garage_id: garageId,
      plate: p,
      direction,
      event_time: eventTimeNow(),
      ...(direction === 'in' && bookingId.trim() ? { booking_id: bookingId.trim() } : {}),
    }
    try {
      let accepted: EntryRequest | null = null
      for (let attempt = 1; !accepted; attempt++) {
        try {
          accepted = await dpc.createEntry(body)
        } catch (err) {
          if (!(err instanceof ApiError) || err.status !== 429 || attempt >= MAX_ATTEMPTS) throw err
          setNote(`The queue is full, trying again (${attempt}/${MAX_ATTEMPTS - 1})…`)
          await new Promise((r) => setTimeout(r, (err.retryAfter ?? 1) * 1000))
        }
      }
      setNote(null)
      setLast(accepted)
      // follow the request until its final state (the first such read is the delivery mark)
      let current = accepted
      while (!FINAL_STATES.includes(current.state)) {
        await new Promise((r) => setTimeout(r, 300))
        current = await dpc.entry(current.id)
        if (alive.current) setLast(current)
      }
      if (current.state === 'done') setPlate('')
      void refresh()
    } catch (err) {
      setNote(null)
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const lastReason = last ? reasonText(last) : null
  return (
    <main id="content" className="page">
      <div className="page-head">
        <div>
          <h1>Barrier</h1>
          <p>Entries and exits go to the queue of the DPC service and are processed by its worker pool.</p>
        </div>
      </div>

      <div className="columns">
        <div className="col-main">
          <form className="card section barrier-form" onSubmit={send} noValidate>
            <div className="barrier-grid">
              <label className="field">
                <span>Garage</span>
                <select
                  className="input"
                  value={garageId}
                  onChange={(e) => {
                    setParams({ garage: e.target.value === 'center' ? undefined : e.target.value })
                    setLast(null)
                  }}
                >
                  {DPC_GARAGES.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Licence plate</span>
                <input
                  className="input plate"
                  autoComplete="off"
                  spellCheck={false}
                  maxLength={16}
                  placeholder="e.g. AA 1234 BB…"
                  value={plate}
                  onChange={(e) => setPlate(e.target.value.toUpperCase())}
                />
              </label>
              <fieldset className="field direction">
                <span>Direction</span>
                <div className="segmented" role="radiogroup">
                  {(['in', 'out'] as const).map((d) => (
                    <button
                      key={d}
                      type="button"
                      role="radio"
                      aria-checked={direction === d}
                      className={direction === d ? 'on' : ''}
                      onClick={() => setDirection(d)}
                    >
                      {d === 'in' ? 'Entry' : 'Exit'}
                    </button>
                  ))}
                </div>
              </fieldset>
              <label className="field">
                <span>
                  Booking id <span className="muted">(optional, entry only)</span>
                </span>
                <input
                  className="input mono"
                  autoComplete="off"
                  spellCheck={false}
                  disabled={direction === 'out'}
                  value={direction === 'out' ? '' : bookingId}
                  onChange={(e) => setBookingId(e.target.value)}
                />
              </label>
            </div>
            <div className="row-actions">
              <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
                {busy && <Spinner size={16} color="#fff" />}
                Send event
              </button>
              {note && <span className="muted">{note}</span>}
            </div>
            {error != null && <DpcProblem error={error} />}
            {last && (
              <div
                className={`alert ${last.state === 'done' ? 'alert-success' : last.state === 'rejected' || last.state === 'failed' ? 'alert-danger' : 'alert-warn'}`}
                role="status"
              >
                {last.state === 'done' ? (
                  <CheckCircle color="var(--success)" size={20} />
                ) : FINAL_STATES.includes(last.state) ? (
                  <AlertIcon color="var(--danger)" size={20} />
                ) : (
                  <Spinner size={18} />
                )}
                <div className="alert-body">
                  <strong>
                    {last.plate} · {last.direction === 'in' ? 'entry' : 'exit'}: {last.state}
                  </strong>
                  {lastReason && (
                    <span>
                      {lastReason}
                      {last.detail ? ` (${last.detail})` : ''}
                    </span>
                  )}
                  {!FINAL_STATES.includes(last.state) && <span>Accepted (202), waiting for a worker…</span>}
                </div>
              </div>
            )}
          </form>

          <section aria-labelledby="recent-title" className="recent">
            <h2 id="recent-title" className="eyebrow">
              Last requests of this garage
            </h2>
            {recent.length === 0 ? (
              <p className="muted">No requests yet.</p>
            ) : (
              <ul className="list">
                {recent.map((r) => (
                  <RequestRow key={r.id} r={r} />
                ))}
              </ul>
            )}
          </section>
        </div>

        <aside className="col-side">
          <GarageCard garage={garage} error={garageError} />
        </aside>
      </div>
    </main>
  )
}
