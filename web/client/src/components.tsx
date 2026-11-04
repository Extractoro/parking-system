import { ApiError, type Booking, type Garage, type HourSlot } from './api'
import { dateTimeIn, money, timeIn } from './format'

/** An error answer as Problem Details: title, detail, invalid fields, request id. */
export function ProblemView({ error }: { error: unknown }) {
  if (!(error instanceof ApiError)) {
    return <div className="problem">Помилка: {String(error)}</div>
  }
  const p = error.problem
  return (
    <div className="problem" role="alert">
      <div className="problem-head">
        <strong>{p.title ?? 'Помилка'}</strong>
        {error.status > 0 && <span className="status">{error.status}</span>}
      </div>
      {p.detail && <div>{p.detail}</div>}
      {p.errors && p.errors.length > 0 && (
        <ul>
          {p.errors.map((e, i) => (
            <li key={i}>
              <code>{e.field}</code>: {e.message}
            </li>
          ))}
        </ul>
      )}
      {p.type && <div className="muted small">type: {p.type}</div>}
      {error.requestId && <div className="muted small">X-Request-ID: {error.requestId}</div>}
    </div>
  )
}

export function GarageList({
  garages,
  levelId,
  onSelect,
}: {
  garages: Garage[]
  levelId: string | null
  onSelect: (garage: Garage, levelId: string) => void
}) {
  return (
    <div className="garages">
      {garages.map((g) => (
        <section key={g.id} className="card">
          <h3>{g.name}</h3>
          <div className="muted small">
            {g.spots} місць · тариф {money(g.tariff_minor_per_hour, g.currency)} / год · {g.tz}
          </div>
          <ul className="levels">
            {g.levels.map((l) => (
              <li key={l.id}>
                <button
                  className={l.id === levelId ? 'level selected' : 'level'}
                  disabled={l.closed}
                  onClick={() => onSelect(g, l.id)}
                >
                  {l.name} <span className="muted">· {l.spots} місць{l.closed ? ' · закрито' : ''}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

export function HoursGrid({
  hours,
  tz,
  selected,
  span,
  onPick,
}: {
  hours: HourSlot[]
  tz: string
  selected: string | null
  span: number
  onPick: (hour: string) => void
}) {
  const start = selected ? hours.findIndex((h) => h.hour === selected) : -1
  return (
    <div className="hours">
      {hours.map((h, i) => {
        const inSpan = start >= 0 && i >= start && i < start + span
        const cls = ['hour', h.free === 0 ? 'full' : h.free < h.spots ? 'partial' : '', inSpan ? 'picked' : '']
        return (
          <button key={h.hour} className={cls.join(' ')} onClick={() => onPick(h.hour)} title={h.hour}>
            <span className="time">{timeIn(h.hour, tz)}</span>
            <span className="free">
              {h.free}/{h.spots}
            </span>
          </button>
        )
      })}
    </div>
  )
}

export function BookingCard({ b, tz }: { b: Booking; tz: string }) {
  return (
    <div className="booking">
      <div>
        <strong>{b.plate}</strong> · {b.level_id} · <span className={`badge ${b.status}`}>{b.status}</span>
      </div>
      <div>
        {dateTimeIn(b.from_hour, tz)} – {timeIn(b.to_hour, tz)} ({b.hours} год) · {money(b.price_minor, b.currency)}
      </div>
      <div className="muted small">
        id {b.id} · створено {dateTimeIn(b.created_at, tz)}
      </div>
    </div>
  )
}
