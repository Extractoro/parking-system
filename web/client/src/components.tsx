import type { MouseEvent, ReactNode } from 'react'
import { ApiError } from './api'
import { AlertIcon, LogoMark, Wifi } from './icons'
import type { Me } from './api'
import type { View } from './shared'
import { hrefWith } from './useUrlState'

/** A link that also works with Cmd/Ctrl+click and middle click; a plain click is handled in place. */
export function NavLink({
  to,
  current,
  onNavigate,
  className,
  children,
}: {
  to: View
  current?: boolean
  onNavigate: (v: View) => void
  className?: string
  children: ReactNode
}) {
  function click(e: MouseEvent<HTMLAnchorElement>) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    e.preventDefault()
    onNavigate(to)
  }
  return (
    <a href={hrefWith({ view: to === 'book' ? undefined : to })} aria-current={current ? 'page' : undefined} className={className} onClick={click}>
      {children}
    </a>
  )
}

export function Header({
  view,
  onNavigate,
  user,
  onLogout,
}: {
  view: View
  onNavigate: (v: View) => void
  user: Me | null
  onLogout: () => void
}) {
  return (
    <header className="header">
      <div className="header-inner">
        <NavLink to="book" onNavigate={onNavigate} className="brand">
          <span className="brand-mark">
            <LogoMark />
          </span>
          Parking
        </NavLink>
        <nav className="nav" aria-label="Sections">
          <NavLink to="book" current={view === 'book'} onNavigate={onNavigate}>
            Book a spot
          </NavLink>
          <NavLink to="bookings" current={view === 'bookings'} onNavigate={onNavigate}>
            My bookings
          </NavLink>
          <NavLink to="access" current={view === 'access'} onNavigate={onNavigate}>
            Access check
          </NavLink>
          <NavLink to="barrier" current={view === 'barrier'} onNavigate={onNavigate}>
            Barrier
          </NavLink>
          <NavLink to="compute" current={view === 'compute'} onNavigate={onNavigate}>
            Compute
          </NavLink>
        </nav>
        {user && (
          <div className="account" data-testid="account">
            <span className="avatar" aria-hidden>
              {user.login.slice(0, 1).toUpperCase() + user.login.slice(-1)}
            </span>
            <span className="account-login">{user.login}</span>
            <span className={`badge role-${user.role}`}>{user.role}</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={onLogout}>
              Log out
            </button>
          </div>
        )}
      </div>
    </header>
  )
}

/** Human texts for the problem types of web/openapi.yaml; the server's own title is the fallback. */
function explain(error: ApiError): { title: string; detail: string } {
  const slug = (error.problem.type ?? '').split('/').pop()
  switch (slug) {
    case 'no-free-spots':
      return { title: 'No free spots', detail: 'Someone booked the last spot first. No hours were taken — pick another time or level.' }
    case 'level-closed':
      return { title: 'Level closed', detail: 'This level takes no new bookings right now. Pick another level.' }
    case 'level-not-found':
      return { title: 'Level not found', detail: 'Reload the page and pick the level again.' }
    case 'idempotency-in-progress':
      return { title: 'Still booking', detail: 'Your booking is still being processed. Wait a moment, then check your bookings.' }
    case 'idempotency-key-reused':
      return { title: 'Something changed', detail: 'Reload the page and book again.' }
    case 'version-mismatch':
      return { title: 'The booking has changed', detail: 'It was changed after you opened this page. Refresh the list and try again.' }
    case 'booking-not-modifiable':
    case 'booking-not-cancellable':
      return { title: 'Cannot change this booking', detail: 'It is no longer active. Refresh the list to see its current state.' }
    case 'booking-started':
      return { title: 'The booking has started', detail: 'A booking can be cancelled only before its first hour starts.' }
    case 'booking-not-found':
      return { title: 'Booking not found', detail: 'There is no booking with this id among your bookings.' }
    case 'unauthorized':
      return { title: 'Sign-in required', detail: 'Your session has ended. Log in again.' }
    case 'invalid-credentials':
      return { title: 'Wrong login or password', detail: 'Check both and try again.' }
    case 'forbidden':
      return { title: 'Not allowed', detail: 'Only parking staff can do this. Your account has the role user.' }
    case 'invalid-request':
      if (error.problem.errors?.some((e) => e.field.endsWith('from_hour')))
        return { title: 'That time has passed', detail: 'Pick the current hour or a later one.' }
      return { title: 'Check the form', detail: 'Some fields are filled in incorrectly.' }
    default:
      return { title: error.problem.title ?? 'Something went wrong', detail: error.problem.detail ?? 'Try again.' }
  }
}

/** An error answer (Problem Details) in words a driver understands; technical details are folded. */
export function ProblemAlert({ error, onRetry, children }: { error: unknown; onRetry?: () => void; children?: ReactNode }) {
  if (error instanceof ApiError && error.status === 0) {
    return (
      <div className="alert alert-warn" role="status">
        <Wifi color="var(--accent)" />
        <span style={{ flex: 1 }}>The server is temporarily unavailable. Nothing was lost.</span>
        {onRetry && (
          <button type="button" className="btn btn-ghost" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    )
  }
  const e = error instanceof ApiError ? error : new ApiError(500, { title: String(error) }, null)
  const { title, detail } = explain(e)
  return (
    <div className="alert alert-danger" role="alert">
      <AlertIcon color="var(--danger)" size={20} />
      <div className="alert-body">
        <strong>{title}</strong>
        <span>{detail}</span>
        {children && <div className="alert-actions">{children}</div>}
        {e.requestId && (
          <details className="support">
            <summary>Details for support</summary>
            <div>
              Reference{' '}
              <span className="mono" translate="no">
                {e.requestId.slice(0, 8).toUpperCase()}
              </span>{' '}
              · HTTP {e.status}
            </div>
          </details>
        )}
      </div>
    </div>
  )
}
