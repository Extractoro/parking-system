import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, type Booking, type Garage } from './api'
import { NavLink, ProblemAlert } from './components'
import { problemSlug, withRetries } from './retry'
import { bookingCode, type View } from './shared'
import { Calendar, CheckCircle, Plus, Spinner } from './icons'
import { dayNumber, dayOf, hoursLabel, money, monthShort, time } from './format'

const PLATE_RE = /^.{2,12}$/

/** Change the plate (If-Match with the version shown in the list) and cancel one booking. */
function BookingActions({
  booking,
  onChanged,
  onRefresh,
}: {
  booking: Booking
  onChanged: (b: Booking, what: 'plate' | 'cancel') => void
  onRefresh: () => void
}) {
  const [mode, setMode] = useState<'idle' | 'plate' | 'cancel'>('idle')
  const [plate, setPlate] = useState(booking.plate)
  const [plateError, setPlateError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const started = Date.parse(booking.from_hour) <= Date.now()

  function open(m: 'idle' | 'plate' | 'cancel') {
    setMode(m)
    setError(null)
    setPlateError(null)
    setPlate(booking.plate)
  }

  async function savePlate(e: React.FormEvent) {
    e.preventDefault()
    const p = plate.trim()
    if (!PLATE_RE.test(p)) {
      setPlateError('The plate must be 2 to 12 characters')
      return
    }
    setBusy(true)
    setError(null)
    try {
      // the version the user saw goes to If-Match: a change made meanwhile gives 412, not an overwrite
      onChanged(await api.updatePlate(booking.id, p, booking.version), 'plate')
      setMode('idle')
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    setBusy(true)
    setError(null)
    try {
      // cancelling twice is harmless on the server, so lost answers are simply retried
      onChanged(await withRetries(() => api.cancelBooking(booking.id)), 'cancel')
      setMode('idle')
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const stale = error instanceof ApiError && error.status === 412
  return (
    <div className="booking-actions">
      {mode === 'idle' && (
        <div className="row-actions">
          <button type="button" className="btn-link" onClick={() => open('plate')}>
            Change plate
          </button>
          {!started && (
            <button type="button" className="btn-link danger" onClick={() => open('cancel')}>
              Cancel booking
            </button>
          )}
        </div>
      )}

      {mode === 'plate' && (
        <form className="plate-edit" onSubmit={savePlate} noValidate>
          <label className="field">
            <span>New licence plate</span>
            <input
              className="input plate"
              autoFocus
              autoComplete="off"
              spellCheck={false}
              maxLength={12}
              value={plate}
              aria-invalid={plateError ? true : undefined}
              onChange={(e) => {
                setPlate(e.target.value.toUpperCase())
                setPlateError(null)
              }}
            />
            {plateError && <span className="field-error">{plateError}</span>}
          </label>
          <div className="row-actions">
            <button type="submit" className="btn btn-dark" disabled={busy || plate.trim() === booking.plate} aria-busy={busy || undefined}>
              {busy && <Spinner size={16} color="#fff" />}
              Save plate
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => open('idle')}>
              Close
            </button>
          </div>
        </form>
      )}

      {mode === 'cancel' && (
        <div className="cancel-confirm" role="group" aria-label="Cancel booking">
          <span>
            Cancel booking <span className="mono">{bookingCode(booking.id)}</span>? Its hours become free for others.
          </span>
          <div className="row-actions">
            <button type="button" className="btn btn-danger" onClick={() => void cancel()} disabled={busy} aria-busy={busy || undefined}>
              {busy && <Spinner size={16} color="#fff" />}
              Yes, cancel
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => open('idle')}>
              Keep it
            </button>
          </div>
        </div>
      )}

      {error != null && (
        <ProblemAlert error={error}>
          {(stale || problemSlug(error) === 'booking-not-modifiable') && (
            <button type="button" className="btn-soft" onClick={onRefresh}>
              Refresh the list
            </button>
          )}
        </ProblemAlert>
      )}
    </div>
  )
}

type Props = {
  params: URLSearchParams
  setParams: (patch: Record<string, string | undefined>, mode?: 'push' | 'replace') => void
  onNavigate: (v: View) => void
}

const STATUS: Record<string, string> = {
  confirmed: 'Confirmed',
  entered: 'Parked',
  cancelled: 'Cancelled',
  expired: 'Ended',
  held: 'Pending',
}

export default function BookingsPage({ params, setParams, onNavigate }: Props) {
  const [data, setData] = useState<{ items: Booking[]; pages: number } | null>(null)
  const [garages, setGarages] = useState<Garage[]>([])
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const tab = params.get('tab') === 'past' ? 'past' : 'upcoming'

  const load = useCallback(() => {
    setError(null)
    setNotice(null)
    setData(null)
    // every page by next_cursor (O1.5): the tabs split by date, so all bookings are needed
    api.allBookings().then(setData, setError)
    api.garages().then(setGarages, () => setGarages([]))
  }, [])
  useEffect(load, [load])

  const now = Date.now()
  const all = data?.items ?? []
  const isPast = (b: Booking) => Date.parse(b.to_hour) <= now || b.status === 'cancelled' || b.status === 'expired'
  const upcoming = all.filter((b) => !isPast(b)).sort((a, b) => Date.parse(a.from_hour) - Date.parse(b.from_hour))
  const past = all.filter(isPast).sort((a, b) => Date.parse(b.from_hour) - Date.parse(a.from_hour))
  const shown = tab === 'past' ? past : upcoming

  function changed(b: Booking, what: 'plate' | 'cancel') {
    setData((d) => (d ? { ...d, items: d.items.map((x) => (x.id === b.id ? b : x)) } : d))
    setNotice(
      what === 'cancel'
        ? `Booking ${bookingCode(b.id)} is cancelled. Its hours are free again; you can find it under Past.`
        : `Plate of booking ${bookingCode(b.id)} changed to ${b.plate}.`,
    )
  }

  const garageOf = (b: Booking) => garages.find((g) => g.id === b.garage_id)
  const levelName = (b: Booking) => garageOf(b)?.levels.find((l) => l.id === b.level_id)?.name ?? ''

  return (
    <main id="content" className="page page-narrow">
      <div className="page-head">
        <div>
          <h1>My bookings</h1>
          <p>{data ? `${all.length} ${all.length === 1 ? 'booking' : 'bookings'}` : error ? 'Could not load the list' : 'Loading…'}</p>
        </div>
        <NavLink to="book" onNavigate={onNavigate} className="btn btn-primary">
          <Plus size={16} />
          New booking
        </NavLink>
      </div>

      <div role="tablist" aria-label="Filter" className="tabs">
        {(
          [
            ['upcoming', 'Upcoming', upcoming.length],
            ['past', 'Past', past.length],
          ] as const
        ).map(([id, label, n]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`tab-${id}`}
            className="tab"
            aria-controls="panel-bookings"
            aria-selected={tab === id}
            tabIndex={tab === id ? 0 : -1}
            onClick={() => setParams({ tab: id === 'past' ? 'past' : undefined }, 'push')}
          >
            {label} {data && <span className="mono">{n}</span>}
          </button>
        ))}
      </div>

      {error != null && <ProblemAlert error={error} onRetry={load} />}

      {notice && (
        <div className="alert alert-success" role="status">
          <CheckCircle color="var(--success)" />
          <span>{notice}</span>
        </div>
      )}

      <ul id="panel-bookings" role="tabpanel" aria-labelledby={`tab-${tab}`} aria-busy={!data} className="list">
        {!data && !error && [0, 1, 2].map((i) => <li key={i} className="skeleton" style={{ height: 96 }} />)}
        {shown.map((b) => {
          const g = garageOf(b)
          const tz = g?.tz ?? 'Europe/Kyiv'
          const day = dayOf(b.from_hour, tz)
          return (
            <li key={b.id} className="card booking">
              <div className="date-tile" aria-hidden>
                <span>{monthShort(day)}</span>
                <span className="mono">{dayNumber(day)}</span>
              </div>
              <div className="booking-main">
                <div className="booking-title">
                  <strong title={`${g?.name ?? ''} · ${levelName(b)}`}>
                    {g?.name ?? 'Parking'} · {levelName(b)}
                  </strong>
                  <span className={`badge ${b.status}`}>{STATUS[b.status] ?? b.status}</span>
                </div>
                <div className="booking-meta">
                  <span className="mono" style={{ color: 'var(--ink)' }}>
                    {time(b.from_hour, tz)} – {time(b.to_hour, tz)}
                  </span>
                  <span>{hoursLabel(b.hours)}</span>
                  <span className="mono" style={{ letterSpacing: '0.04em' }}>
                    {b.plate}
                  </span>
                </div>
              </div>
              <div className="booking-side">
                <strong className="mono" style={{ fontSize: 16 }}>
                  {money(b.price_minor, b.currency)}
                </strong>
                <span className="muted small">
                  Booking{' '}
                  <span className="mono" translate="no">
                    {bookingCode(b.id)}
                  </span>
                </span>
              </div>
              {b.status === 'confirmed' && Date.parse(b.to_hour) > now && (
                <BookingActions key={`${b.id}/${b.version}`} booking={b} onChanged={changed} onRefresh={load} />
              )}
            </li>
          )
        })}
      </ul>

      {data && shown.length === 0 && (
        <div className="card empty">
          <span className="empty-icon">
            <Calendar color="var(--muted)" size={22} />
          </span>
          <strong style={{ fontSize: 15 }}>{tab === 'past' ? 'No past bookings' : 'No bookings yet'}</strong>
          <span className="muted">Pick a garage and the hours — the spot is held for you.</span>
          <NavLink to="book" onNavigate={onNavigate} className="btn btn-primary">
            Book a spot
          </NavLink>
        </div>
      )}

      {data && data.pages > 1 && (
        <p className="muted small" style={{ margin: 0, textAlign: 'center' }}>
          All {all.length} bookings loaded ({data.pages} pages by cursor)
        </p>
      )}
    </main>
  )
}
