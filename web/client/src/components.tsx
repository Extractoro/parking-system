import type { MouseEvent, ReactNode } from 'react'
import { ApiError } from './api'
import { AlertIcon, LogoMark, Wifi } from './icons'
import { USERS, type View } from './shared'
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
    <a href={hrefWith({ view: to === 'bookings' ? 'bookings' : undefined })} aria-current={current ? 'page' : undefined} className={className} onClick={click}>
      {children}
    </a>
  )
}

export function Header({
  view,
  onNavigate,
  userId,
  onUser,
}: {
  view: View
  onNavigate: (v: View) => void
  userId: string
  onUser: (id: string) => void
}) {
  const user = USERS.find((u) => u.id === userId) ?? USERS[0]
  return (
    <header className="header">
      <div className="header-inner">
        <NavLink to="book" onNavigate={onNavigate} className="brand">
          <span className="brand-mark">
            <LogoMark />
          </span>
          Паркінги
        </NavLink>
        <nav className="nav" aria-label="Розділи">
          <NavLink to="book" current={view === 'book'} onNavigate={onNavigate}>
            Забронювати
          </NavLink>
          <NavLink to="bookings" current={view === 'bookings'} onNavigate={onNavigate}>
            Мої бронювання
          </NavLink>
        </nav>
        {/* Until lab 3 there is no login: the account is one of the pre-created users. */}
        <label className="account">
          <span className="avatar" aria-hidden>
            {user.login.slice(0, 1).toUpperCase() + user.login.slice(-1)}
          </span>
          <span className="sr-only">Обліковий запис</span>
          <select value={user.id} onChange={(e) => onUser(e.target.value)}>
            {USERS.map((u) => (
              <option key={u.id} value={u.id}>
                {u.login}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  )
}

/** Human texts for the problem types of web/openapi.yaml; the server's own title is the fallback. */
function explain(error: ApiError): { title: string; detail: string } {
  const slug = (error.problem.type ?? '').split('/').pop()
  switch (slug) {
    case 'no-free-spots':
      return { title: 'Немає вільних місць', detail: 'Хтось забронював останнє місце раніше. Жодну годину не списано — оберіть інший час або рівень.' }
    case 'level-closed':
      return { title: 'Рівень закрито', detail: 'Цей рівень зараз не приймає нових бронювань. Оберіть інший рівень.' }
    case 'level-not-found':
      return { title: 'Рівень не знайдено', detail: 'Оновіть сторінку й оберіть рівень ще раз.' }
    case 'unauthorized':
      return { title: 'Потрібен вхід', detail: 'Оберіть обліковий запис угорі сторінки.' }
    case 'invalid-request':
      return { title: 'Перевірте дані', detail: 'Деякі поля заповнено неправильно.' }
    default:
      return { title: error.problem.title ?? 'Щось пішло не так', detail: error.problem.detail ?? 'Спробуйте ще раз.' }
  }
}

/** An error answer (Problem Details) in words a driver understands; technical details are folded. */
export function ProblemAlert({ error, onRetry, children }: { error: unknown; onRetry?: () => void; children?: ReactNode }) {
  if (error instanceof ApiError && error.status === 0) {
    return (
      <div className="alert alert-warn" role="status">
        <Wifi color="var(--accent)" />
        <span style={{ flex: 1 }}>Сервер тимчасово недоступний. Дані не втрачено.</span>
        {onRetry && (
          <button type="button" className="btn btn-ghost" onClick={onRetry}>
            Повторити
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
            <summary>Деталі для підтримки</summary>
            <div>
              Код звернення{' '}
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
