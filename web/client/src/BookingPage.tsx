import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, type Booking, type Garage, type HourSlot } from './api'
import { NavLink, ProblemAlert } from './components'
import type { View } from './shared'
import { AlertIcon, Check, CheckCircle, Spinner } from './icons'
import {
  HOUR_MS,
  addDays,
  dateFull,
  dateLong,
  dayNumber,
  dayOf,
  hoursLabel,
  money,
  plusHours,
  time,
  todayIn,
  weekdayShort,
} from './format'

const DAYS_AHEAD = 14
const MAX_HOURS = 24
const PLATE_RE = /^.{2,12}$/

type Props = {
  userId: string
  params: URLSearchParams
  setParams: (patch: Record<string, string | undefined>, mode?: 'push' | 'replace') => void
  onNavigate: (v: View) => void
}

type FieldErrors = { plate?: string; hours?: string }

export default function BookingPage({ userId, params, setParams, onNavigate }: Props) {
  const [garages, setGarages] = useState<Garage[] | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [slots, setSlots] = useState<Record<string, HourSlot[]>>({})
  const [plate, setPlate] = useState(() => sessionStorage.getItem('plate') ?? '')
  const [sending, setSending] = useState(false)
  const [result, setResult] = useState<Booking | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const plateRef = useRef<HTMLInputElement>(null)
  const gridRef = useRef<HTMLDivElement>(null)

  const loadGarages = useCallback(() => {
    setLoadError(null)
    api.garages().then(setGarages, setLoadError)
  }, [])
  useEffect(loadGarages, [loadGarages])

  // ---- selection from the URL ----
  const garage = garages?.find((g) => g.id === params.get('garage')) ?? garages?.[0] ?? null
  const tz = garage?.tz ?? 'Europe/Kyiv'
  const openLevels = garage?.levels ?? []
  const level = openLevels.find((l) => l.id === params.get('level')) ?? openLevels.find((l) => !l.closed) ?? null
  const today = todayIn(tz)
  const dayParam = params.get('day')
  const day = dayParam && dayParam >= today ? dayParam : today
  const from = params.get('from')
  const hours = Math.min(MAX_HOURS, Math.max(1, Number(params.get('hours')) || 2))

  // free spots of the chosen day and the next one (a booking may cross midnight)
  const loadSlots = useCallback(() => {
    if (!level) return
    for (const d of [day, addDays(day, 1)]) {
      api.levelHours(level.id, d).then(
        (r) => setSlots((s) => ({ ...s, [`${level.id}/${d}`]: r.hours })),
        setLoadError,
      )
    }
  }, [level, day])
  useEffect(loadSlots, [loadSlots])

  const daySlots = level ? slots[`${level.id}/${day}`] : undefined
  const freeAt = useMemo(() => {
    const m = new Map<number, HourSlot>()
    if (level) for (const d of [day, addDays(day, 1)]) for (const h of slots[`${level.id}/${d}`] ?? []) m.set(Date.parse(h.hour), h)
    return m
  }, [slots, level, day])

  const start = from ? Date.parse(from) : NaN
  const picked = new Set<number>()
  let conflictAt: number | null = null
  if (!Number.isNaN(start)) {
    for (let i = 0; i < hours; i++) {
      const t = start + i * HOUR_MS
      picked.add(t)
      if (conflictAt === null && freeAt.get(t)?.free === 0) conflictAt = t
    }
  }

  // the strip starts today; a day from a shared link further ahead starts the strip itself
  const stripStart = day > addDays(today, DAYS_AHEAD - 1) ? day : today
  const days = Array.from({ length: DAYS_AHEAD }, (_, i) => addDays(stripStart, i))
  const price = garage ? garage.tariff_minor_per_hour * hours : 0
  const end = from ? plusHours(from, hours) : null
  const endsNextDay = end !== null && dayOf(end - 1, tz) !== day

  function select(patch: Record<string, string | undefined>) {
    setResult(null)
    setError(null)
    setParams(patch)
  }

  function pickHour(iso: string) {
    select({ from: iso })
  }

  function validate(): FieldErrors {
    const errs: FieldErrors = {}
    if (!PLATE_RE.test(plate.trim())) errs.plate = 'The plate must be 2 to 12 characters'
    if (hours < 1 || hours > MAX_HOURS) errs.hours = `From 1 to ${MAX_HOURS} hours`
    return errs
  }

  function submit(e: React.FormEvent) {
    e.preventDefault()
    void send()
  }

  async function send() {
    if (!level || !from || sending) return
    const errs = validate()
    setFieldErrors(errs)
    if (errs.plate) {
      plateRef.current?.focus()
      return
    }
    setSending(true)
    setError(null)
    setResult(null)
    try {
      const { booking } = await api.createBooking(userId, { level_id: level.id, from_hour: from, hours, plate: plate.trim() })
      setResult(booking)
      setParams({ from: undefined }) // the hours are now taken: drop the selection, keep the result
      sessionStorage.setItem('plate', plate.trim())
    } catch (err) {
      // 422 with fields -> show next to the fields and focus the first one
      if (err instanceof ApiError && err.status === 422 && err.problem.errors?.length) {
        const fe: FieldErrors = {}
        for (const x of err.problem.errors) {
          if (x.field.endsWith('plate')) fe.plate = 'The plate must be 2 to 12 characters'
          if (x.field.endsWith('hours')) fe.hours = `From 1 to ${MAX_HOURS} hours`
        }
        setFieldErrors(fe)
        if (fe.plate) plateRef.current?.focus()
      }
      setError(err)
    } finally {
      setSending(false)
      loadSlots()
    }
  }

  if (loadError && !garages) {
    return (
      <main id="content" className="page">
        <ProblemAlert error={loadError} onRetry={loadGarages} />
      </main>
    )
  }

  return (
    <main id="content" className="page">
      <div className="page-head">
        <div>
          <h1>Book a parking spot for a few hours</h1>
          <p>Pick a garage, a level and the hours. The spot is held for you for the whole interval.</p>
        </div>
      </div>

      <div className="columns">
        <div className="col-main">
          <section aria-labelledby="s-garage" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <h2 id="s-garage" className="eyebrow">
              1 · Garage
            </h2>
            <div className="garages">
              {!garages &&
                [0, 1].map((i) => <div key={i} className="skeleton" style={{ height: 96 }} />)}
              {garages?.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  className="garage"
                  aria-pressed={g.id === garage?.id}
                  onClick={() => select({ garage: g.id, level: undefined, from: undefined })}
                >
                  <span className="garage-name">
                    {g.name}
                    {g.id === garage?.id && <CheckCircle color="var(--accent)" />}
                  </span>
                  <span className="garage-meta">
                    <span>
                      <strong className="mono">{money(g.tariff_minor_per_hour, g.currency)}</strong> / hour
                    </span>
                    <span>
                      <strong className="mono">{g.spots}</strong> spots · {g.levels.length}{' '}
                      {g.levels.length === 1 ? 'level' : 'levels'}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </section>

          <section aria-labelledby="s-time" className="card section">
            <div className="row-between">
              <h2 id="s-time" className="eyebrow">
                2 · Level and time
              </h2>
              <span className="muted small">Garage time ({tz})</span>
            </div>

            <div role="radiogroup" aria-label="Level" className="chips">
              {openLevels.map((l) => (
                <button
                  key={l.id}
                  type="button"
                  role="radio"
                  className="level"
                  aria-checked={l.id === level?.id}
                  disabled={l.closed}
                  onClick={() => select({ level: l.id, from: undefined })}
                >
                  <span style={{ fontWeight: 500 }}>{l.name}</span>
                  <span className="muted small">{l.closed ? 'closed' : `${l.spots} ${l.spots === 1 ? 'spot' : 'spots'}`}</span>
                </button>
              ))}
            </div>

            <div className="days" aria-label="Day">
              {days.map((d) => (
                <button
                  key={d}
                  type="button"
                  className="day"
                  aria-pressed={d === day}
                  aria-label={dateFull(d)}
                  onClick={() => select({ day: d, from: undefined })}
                >
                  <span className="day-week">{weekdayShort(d)}</span>
                  <span className="day-num mono">{dayNumber(d)}</span>
                </button>
              ))}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="hours" ref={gridRef} aria-label="Hours">
                {!daySlots && Array.from({ length: 24 }, (_, i) => <span key={i} className="skeleton" style={{ height: 52 }} />)}
                {daySlots?.map((h) => {
                  const t = Date.parse(h.hour)
                  const inRange = picked.has(t)
                  const full = h.free === 0
                  const few = !full && h.free < h.spots && h.free <= Math.max(3, Math.round(h.spots * 0.15))
                  const cls = ['slot', full && 'full', few && 'few', inRange && (full ? 'conflict' : 'picked')].filter(Boolean).join(' ')
                  return (
                    <button
                      key={h.hour}
                      type="button"
                      className={cls}
                      disabled={full && !inRange}
                      aria-pressed={inRange}
                      aria-label={`${time(h.hour, tz)}, ${full ? 'no free spots' : `${h.free} free`}`}
                      onClick={() => pickHour(h.hour)}
                    >
                      <span className="t mono">{time(h.hour, tz)}</span>
                      <span className="f">{full ? 'full' : `${h.free} free`}</span>
                    </button>
                  )
                })}
              </div>
              <div className="legend" aria-hidden>
                <span>
                  <i />
                  Free
                </span>
                <span>
                  <i className="few" />
                  Few left
                </span>
                <span>
                  <i className="full" />
                  Full
                </span>
                <span>
                  <i className="picked" />
                  Your pick
                </span>
              </div>
            </div>
          </section>
        </div>

        <aside className="col-side card" aria-labelledby="s-summary">
          {result ? (
            <div className="success" role="status">
              <span className="success-icon">
                <Check color="var(--success)" size={22} />
              </span>
              <div>
                <h2>Spot booked</h2>
                <p className="muted" style={{ margin: '4px 0 0' }}>
                  {garage?.name} · {level?.name} · {dateLong(dayOf(result.from_hour, tz))},{' '}
                  <span className="mono">
                    {time(result.from_hour, tz)} – {time(result.to_hour, tz)}
                  </span>
                </p>
              </div>
              <div className="paid">
                <span className="muted">Total</span>
                <strong className="mono">{money(result.price_minor, result.currency)}</strong>
              </div>
              <div className="actions">
                <NavLink to="bookings" onNavigate={onNavigate} className="btn btn-dark">
                  My bookings
                </NavLink>
                <button type="button" className="btn btn-ghost" onClick={() => select({ from: undefined })}>
                  Book another
                </button>
              </div>
            </div>
          ) : (
            <form onSubmit={submit} noValidate>
              <div className="summary-body">
                <h2 id="s-summary" className="eyebrow">
                  3 · Your booking
                </h2>
                <dl className="kv" style={{ margin: 0 }}>
                  <div>
                    <dt>Garage</dt>
                    <dd>{garage?.name ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>Level</dt>
                    <dd>{level?.name ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>Date</dt>
                    <dd>{dateLong(day)}</dd>
                  </div>
                  <div>
                    <dt>Time</dt>
                    <dd className="mono">
                      {from && end !== null ? (
                        <>
                          {time(from, tz)} – {time(end, tz)}
                          {endsNextDay && <span className="muted"> (+1 day)</span>}
                        </>
                      ) : (
                        <span className="muted" style={{ fontFamily: 'inherit' }}>
                          pick an hour
                        </span>
                      )}
                    </dd>
                  </div>
                </dl>

                <div className="row-between" style={{ paddingTop: 12, borderTop: '1px solid var(--line)' }}>
                  <span id="hours-label" style={{ color: 'var(--ink-2)' }}>
                    Duration
                  </span>
                  <div
                    role="group"
                    aria-labelledby="hours-label"
                    className={fieldErrors.hours ? 'stepper invalid' : 'stepper'}
                  >
                    <button type="button" aria-label="Fewer hours" disabled={hours <= 1} onClick={() => select({ hours: String(hours - 1) })}>
                      −
                    </button>
                    <output className="mono" aria-live="polite">
                      {hoursLabel(hours)}
                    </output>
                    <button type="button" aria-label="More hours" disabled={hours >= MAX_HOURS} onClick={() => select({ hours: String(hours + 1) })}>
                      +
                    </button>
                  </div>
                </div>
                {fieldErrors.hours && <span className="field-error">{fieldErrors.hours}</span>}

                <label className="field">
                  <span>Licence plate</span>
                  <input
                    ref={plateRef}
                    className="input plate"
                    name="plate"
                    autoComplete="off"
                    spellCheck={false}
                    autoCapitalize="characters"
                    placeholder="e.g. AA 1234 BB…"
                    value={plate}
                    maxLength={12}
                    aria-invalid={fieldErrors.plate ? true : undefined}
                    aria-describedby={fieldErrors.plate ? 'plate-err' : undefined}
                    onChange={(e) => {
                      setPlate(e.target.value.toUpperCase())
                      if (fieldErrors.plate) setFieldErrors((f) => ({ ...f, plate: undefined }))
                    }}
                  />
                  {fieldErrors.plate && (
                    <span id="plate-err" className="field-error">
                      {fieldErrors.plate}
                    </span>
                  )}
                </label>

                {conflictAt !== null && !error && (
                  <div className="alert alert-danger" role="alert">
                    <AlertIcon color="var(--danger)" />
                    <span>
                      No free spots at <strong>{time(conflictAt, tz)}</strong>. Change the start or the duration.
                    </span>
                  </div>
                )}

                {error != null && (
                  <ProblemAlert error={error} onRetry={() => void send()}>
                    {error instanceof ApiError && error.status === 409 && (
                      <button
                        type="button"
                        className="btn-soft"
                        onClick={() => {
                          setError(null)
                          gridRef.current?.querySelector<HTMLButtonElement>('.slot:not(:disabled)')?.focus()
                        }}
                      >
                        Change time
                      </button>
                    )}
                  </ProblemAlert>
                )}
              </div>

              <div className="summary-foot">
                <div className="total">
                  <span className="muted">Total</span>
                  <strong className="mono">{garage ? money(price, garage.currency) : '—'}</strong>
                </div>
                <span className="muted small">
                  {garage ? `${money(garage.tariff_minor_per_hour, garage.currency)} × ${hoursLabel(hours)}` : ''}. A started hour counts as a
                  full hour.
                </span>
                <button
                  type="submit"
                  className="btn btn-primary btn-lg"
                  disabled={!from || !level || conflictAt !== null || sending}
                  aria-busy={sending || undefined}
                >
                  {sending ? (
                    <>
                      <Spinner size={16} color="#fff" /> Booking…
                    </>
                  ) : from ? (
                    `Book · ${garage ? money(price, garage.currency) : ''}`
                  ) : (
                    'Pick an hour'
                  )}
                </button>
              </div>
            </form>
          )}
        </aside>
      </div>
    </main>
  )
}
