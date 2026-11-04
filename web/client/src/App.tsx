import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_URL, api, type Booking, type Garage, type LevelHours } from './api'
import { BookingCard, GarageList, HoursGrid, ProblemView } from './components'
import { money, todayIn } from './format'

// Until lab 3 there is no login: the client sends the id of a pre-created user (X-User-Id).
const USERS = [
  { id: 'u-1', label: 'user1 (водій)' },
  { id: 'u-2', label: 'user2 (водій)' },
]

export default function App() {
  const [userId, setUserId] = useState(() => sessionStorage.getItem('userId') ?? USERS[0].id)
  const [garages, setGarages] = useState<Garage[]>([])
  const [garage, setGarage] = useState<Garage | null>(null)
  const [levelId, setLevelId] = useState<string | null>(null)
  const [day, setDay] = useState('')
  const [hours, setHours] = useState<LevelHours | null>(null)
  const [fromHour, setFromHour] = useState<string | null>(null)
  const [count, setCount] = useState(2)
  const [plate, setPlate] = useState('AA1234BB')
  const [sending, setSending] = useState(false)
  const [result, setResult] = useState<{ booking: Booking; location: string | null } | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [mine, setMine] = useState<{ items: Booking[]; pages: number } | null>(null)
  const [mineError, setMineError] = useState<unknown>(null)

  const tz = garage?.tz ?? 'Europe/Kyiv'

  useEffect(() => {
    api.garages().then(setGarages, setError)
  }, [])

  const loadHours = useCallback(() => {
    if (!levelId || !day) return
    api.levelHours(levelId, day).then(setHours, setError)
  }, [levelId, day])
  useEffect(loadHours, [loadHours])

  const loadMine = useCallback(() => {
    setMineError(null)
    api.allBookings(userId).then(setMine, (e) => {
      setMine(null)
      setMineError(e)
    })
  }, [userId])
  useEffect(loadMine, [loadMine])

  function pickLevel(g: Garage, id: string) {
    setGarage(g)
    setLevelId(id)
    setDay((d) => d || todayIn(g.tz))
    setFromHour(null)
    setResult(null)
    setError(null)
  }

  function changeUser(id: string) {
    sessionStorage.setItem('userId', id)
    setUserId(id)
    setResult(null)
  }

  // price shown before sending: tariff x hours (a partial hour counts as a full one); the server
  // computes it again with the tariff at booking time and returns its own value
  const price = useMemo(() => (garage ? garage.tariff_minor_per_hour * count : 0), [garage, count])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!levelId || !fromHour) return
    setSending(true)
    setResult(null)
    setError(null)
    try {
      setResult(await api.createBooking(userId, { level_id: levelId, from_hour: fromHour, hours: count, plate }))
    } catch (err) {
      setError(err)
    } finally {
      setSending(false)
      loadHours()
      loadMine()
    }
  }

  return (
    <div className="page">
      <header>
        <h1>Паркінги — бронювання місць</h1>
        <label>
          Користувач:{' '}
          <select value={userId} onChange={(e) => changeUser(e.target.value)}>
            {USERS.map((u) => (
              <option key={u.id} value={u.id}>
                {u.label}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small">API: {API_URL}</span>
      </header>

      <main>
        <section>
          <h2>1. Паркінги й рівні</h2>
          <GarageList garages={garages} levelId={levelId} onSelect={pickLevel} />
        </section>

        {levelId && garage && (
          <section>
            <h2>
              2. Вільні місця · {levelId}{' '}
              <input type="date" value={day} onChange={(e) => (setDay(e.target.value), setFromHour(null))} />
            </h2>
            <p className="muted small">
              Години в зоні паркінгу {tz}. Натисніть годину, щоб почати бронювання з неї.
            </p>
            {hours && hours.level_id === levelId && (
              <HoursGrid hours={hours.hours} tz={tz} selected={fromHour} span={count} onPick={setFromHour} />
            )}
          </section>
        )}

        {levelId && garage && (
          <section>
            <h2>3. Бронювання</h2>
            <form className="card form" onSubmit={submit}>
              <label>
                Рівень <input value={levelId} readOnly />
              </label>
              <label>
                Початок <input value={fromHour ?? '— оберіть годину вище —'} readOnly />
              </label>
              <label>
                Годин{' '}
                <input type="number" min={1} max={24} value={count} onChange={(e) => setCount(Number(e.target.value))} />
              </label>
              <label>
                Номер авто <input value={plate} onChange={(e) => setPlate(e.target.value)} />
              </label>
              <div className="price">
                До сплати: <strong>{money(price, garage.currency)}</strong>{' '}
                <span className="muted small">
                  ({money(garage.tariff_minor_per_hour, garage.currency)} × {count} год)
                </span>
              </div>
              <button type="submit" disabled={!fromHour || sending}>
                {sending ? 'Надсилаю…' : 'Забронювати'}
              </button>
            </form>

            {result && (
              <div className="ok card">
                <strong>Бронювання створено</strong> <span className="muted small">Location: {result.location}</span>
                <BookingCard b={result.booking} tz={tz} />
              </div>
            )}
            {error != null && <ProblemView error={error} />}
          </section>
        )}

        <section>
          <h2>
            4. Мої бронювання{' '}
            <button className="link" onClick={loadMine}>
              оновити
            </button>
          </h2>
          {mineError != null && <ProblemView error={mineError} />}
          {mine && (
            <>
              <p className="muted small">
                Усього {mine.items.length}, прочитано сторінок: {mine.pages} (limit 5, next_cursor)
              </p>
              {mine.items.length === 0 && <p className="muted">Бронювань ще немає.</p>}
              {mine.items.map((b) => (
                <BookingCard key={b.id} b={b} tz={garages.find((g) => g.id === b.garage_id)?.tz ?? tz} />
              ))}
            </>
          )}
        </section>
      </main>
    </div>
  )
}
