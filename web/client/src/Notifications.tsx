import { useEffect, useRef, useState } from 'react'
import type { Notice } from './api'
import type { FeedState } from './feed'

const STATUS_TEXT: Record<FeedState['status'], string> = {
  connecting: 'Connecting…',
  online: 'Live',
  reconnecting: 'Reconnecting…',
}

function when(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString(undefined, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const BellIcon = () => (
  <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
       strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
    <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
  </svg>
)

/** Bell with the unread counter, the connection state and the list of the last notifications. */
export function NotificationBell({ state, onOpen }: { state: FeedState | null; onOpen: () => void }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  if (!state) return null
  const toggle = () => {
    if (!open) onOpen()
    setOpen(!open)
  }
  return (
    <div className="bell-box" ref={box} data-testid="notifications">
      <span className={`conn conn-${state.status}`} data-testid="connection" title={state.gateway ?? undefined}>
        <span className="conn-dot" aria-hidden />
        {STATUS_TEXT[state.status]}
      </span>
      <button type="button" className="bell" aria-expanded={open} aria-label={`Notifications, ${state.unread} unread`}
              onClick={toggle}>
        <BellIcon />
        {state.unread > 0 && <span className="bell-count">{state.unread > 99 ? '99+' : state.unread}</span>}
      </button>
      {open && (
        <div className="bell-panel" role="dialog" aria-label="Notifications" data-testid="notification-list">
          <div className="bell-head">
            <strong>Notifications</strong>
            <span className={`conn conn-${state.status}`}>
              <span className="conn-dot" aria-hidden />
              {STATUS_TEXT[state.status]}
              {state.gateway && state.status === 'online' ? ` · ${state.gateway}` : ''}
            </span>
          </div>
          {state.items.length === 0 ? (
            <p className="bell-empty">No notifications yet.</p>
          ) : (
            <ol className="bell-list">
              {state.items.map((n) => (
                <li key={n.id} className={`notice notice-${n.type.replace('.', '-')}`}>
                  <span className="notice-text">{n.text}</span>
                  <span className="notice-meta">
                    #{n.position} · {when(n.created_at)}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  )
}

export function Toasts({ toasts, onDismiss }: { toasts: Notice[]; onDismiss: (id: string) => void }) {
  if (toasts.length === 0) return null
  return (
    <div className="toasts" role="status" aria-live="polite" data-testid="toasts">
      {toasts.map((n) => (
        <div key={n.id} className={`toast notice-${n.type.replace('.', '-')}`}>
          <span className="toast-text">{n.text}</span>
          <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => onDismiss(n.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
