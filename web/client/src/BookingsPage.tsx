import { useCallback, useEffect, useState } from 'react'
import { api, type Booking, type Garage } from './api'
import { NavLink, ProblemAlert } from './components'
import { bookingCode, type View } from './shared'
import { Calendar, Plus } from './icons'
import { dayNumber, dayOf, hoursLabel, money, monthShort, time } from './format'

type Props = {
  userId: string
  params: URLSearchParams
  setParams: (patch: Record<string, string | undefined>, mode?: 'push' | 'replace') => void
  onNavigate: (v: View) => void
}

const STATUS: Record<string, string> = {
  confirmed: 'Підтверджено',
  entered: 'На паркінгу',
  cancelled: 'Скасовано',
  expired: 'Завершено',
  held: 'Очікує',
}

export default function BookingsPage({ userId, params, setParams, onNavigate }: Props) {
  const [data, setData] = useState<{ items: Booking[]; pages: number } | null>(null)
  const [garages, setGarages] = useState<Garage[]>([])
  const [error, setError] = useState<unknown>(null)
  const tab = params.get('tab') === 'past' ? 'past' : 'upcoming'

  const load = useCallback(() => {
    setError(null)
    setData(null)
    // every page by next_cursor (O1.5): the tabs split by date, so all bookings are needed
    api.allBookings(userId).then(setData, setError)
    api.garages().then(setGarages, () => setGarages([]))
  }, [userId])
  useEffect(load, [load])

  const now = Date.now()
  const all = data?.items ?? []
  const isPast = (b: Booking) => Date.parse(b.to_hour) <= now || b.status === 'cancelled' || b.status === 'expired'
  const upcoming = all.filter((b) => !isPast(b)).sort((a, b) => Date.parse(a.from_hour) - Date.parse(b.from_hour))
  const past = all.filter(isPast).sort((a, b) => Date.parse(b.from_hour) - Date.parse(a.from_hour))
  const shown = tab === 'past' ? past : upcoming

  const garageOf = (b: Booking) => garages.find((g) => g.id === b.garage_id)
  const levelName = (b: Booking) => garageOf(b)?.levels.find((l) => l.id === b.level_id)?.name ?? ''

  return (
    <main id="content" className="page page-narrow">
      <div className="page-head">
        <div>
          <h1>Мої бронювання</h1>
          <p>{data ? `${all.length} бронювань` : 'Завантаження…'}</p>
        </div>
        <NavLink to="book" onNavigate={onNavigate} className="btn btn-primary">
          <Plus size={16} />
          Нове бронювання
        </NavLink>
      </div>

      <div role="tablist" aria-label="Фільтр" className="tabs">
        {(
          [
            ['upcoming', 'Майбутні', upcoming.length],
            ['past', 'Минулі', past.length],
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
                    {g?.name ?? 'Паркінг'} · {levelName(b)}
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
                  Бронювання{' '}
                  <span className="mono" translate="no">
                    {bookingCode(b.id)}
                  </span>
                </span>
              </div>
            </li>
          )
        })}
      </ul>

      {data && shown.length === 0 && (
        <div className="card empty">
          <span className="empty-icon">
            <Calendar color="var(--muted)" size={22} />
          </span>
          <strong style={{ fontSize: 15 }}>{tab === 'past' ? 'Минулих бронювань немає' : 'Бронювань ще немає'}</strong>
          <span className="muted">Оберіть паркінг і години — місце закріпиться за вами.</span>
          <NavLink to="book" onNavigate={onNavigate} className="btn btn-primary">
            Забронювати місце
          </NavLink>
        </div>
      )}

      {data && data.pages > 1 && (
        <p className="muted small" style={{ margin: 0, textAlign: 'center' }}>
          Завантажено всі {all.length} бронювань ({data.pages} сторінки за курсором)
        </p>
      )}
    </main>
  )
}
