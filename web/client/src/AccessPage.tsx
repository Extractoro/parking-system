import { useEffect, useState } from 'react'
import { api, type Booking, type Garage, type LevelState, type Me } from './api'
import { ProblemAlert } from './components'
import { bookingCode } from './shared'
import { CheckCircle, Spinner } from './icons'

/** O3.4: an attempt to read a booking by id (someone else's -> 404 like a missing one) and, for the role
 *  staff only, the staff function "close a level". A driver does not see the staff function at all; the
 *  server still refuses it to the role user with 403 (the route checks the role, not this page). */
export default function AccessPage({ user }: { user: Me }) {
  const [bookingId, setBookingId] = useState('')
  const [reading, setReading] = useState(false)
  const [found, setFound] = useState<Booking | null>(null)
  const [readError, setReadError] = useState<unknown>(null)

  const [garages, setGarages] = useState<Garage[]>([])
  const [levelId, setLevelId] = useState('mall-2')
  const [acting, setActing] = useState(false)
  const [level, setLevel] = useState<LevelState | null>(null)
  const [levelError, setLevelError] = useState<unknown>(null)

  useEffect(() => {
    api.garages().then(setGarages, () => setGarages([]))
  }, [])

  async function read(e: React.FormEvent) {
    e.preventDefault()
    if (!bookingId.trim()) return
    setReading(true)
    setFound(null)
    setReadError(null)
    try {
      // the short id from a card may come with its '#'
      setFound(await api.booking(bookingId.trim().replace(/^#/, '')))
    } catch (err) {
      setReadError(err)
    } finally {
      setReading(false)
    }
  }

  async function act(close: boolean) {
    setActing(true)
    setLevel(null)
    setLevelError(null)
    try {
      setLevel(await (close ? api.closeLevel(levelId) : api.openLevel(levelId)))
      api.garages().then(setGarages, () => undefined)
    } catch (err) {
      setLevelError(err)
    } finally {
      setActing(false)
    }
  }

  const levels = garages.flatMap((g) => g.levels.map((l) => ({ ...l, garage: g.name })))
  return (
    <main id="content" className="page page-narrow">
      <div className="page-head">
        <div>
          <h1>Access check</h1>
          <p>
            Logged in as <strong>{user.login}</strong> with the role <strong>{user.role}</strong>. Every check below is
            made by the server, not by this page.
          </p>
        </div>
      </div>

      <div className="access-grid">
        <form className="card section" onSubmit={read} data-testid="access-booking">
          <h2 className="eyebrow">Read a booking by id</h2>
          <div className="access-row">
            <label className="field">
              <span>Booking id (full or short, as on the card)</span>
              <input
                className="input mono"
                value={bookingId}
                onChange={(e) => setBookingId(e.target.value)}
                placeholder="e.g. 0411A5B7 or a booking id of another user"
                spellCheck={false}
              />
            </label>
            <button type="submit" className="btn btn-dark" disabled={reading || !bookingId.trim()}>
              {reading && <Spinner size={16} color="#fff" />}
              Read
            </button>
          </div>
          {found && (
            <div className="alert alert-success" role="status">
              <CheckCircle color="var(--success)" />
              <span>
                Your booking {bookingCode(found.id)}: {found.level_id}, {found.hours} h, plate {found.plate}, {found.status}
              </span>
            </div>
          )}
          {readError != null && <ProblemAlert error={readError} />}
        </form>

        {user.role === 'staff' && (
          <section className="card section" data-testid="access-level">
            <h2 className="eyebrow">Staff function: close a level</h2>
            <div className="access-row">
              <label className="field">
                <span>Level</span>
                <select className="input" value={levelId} onChange={(e) => setLevelId(e.target.value)}>
                  {levels.length === 0 && <option value={levelId}>{levelId}</option>}
                  {levels.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.garage} · {l.name} ({l.id}){l.closed ? ' · closed' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <button type="button" className="btn btn-danger" disabled={acting} onClick={() => act(true)}>
                Close level
              </button>
              <button type="button" className="btn btn-ghost" disabled={acting} onClick={() => act(false)}>
                Open level
              </button>
            </div>
            {level && (
              <div className="alert alert-success" role="status">
                <CheckCircle color="var(--success)" />
                <span>
                  Level {level.name} ({level.id}) is now {level.closed ? 'closed: new bookings get 409, confirmed ones stay' : 'open'}.
                </span>
              </div>
            )}
            {levelError != null && <ProblemAlert error={levelError} />}
          </section>
        )}
      </div>
    </main>
  )
}
