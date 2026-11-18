import { useCallback, useEffect, useRef, useState } from 'react'
import type { Me, Notice } from './api'
import { NotificationFeed, type FeedState } from './feed'

const TOAST_MS = 6000

/** The feed of the logged-in user: one WebSocket per tab while the user is logged in. */
export function useNotifications(user: Me | null) {
  const [state, setState] = useState<FeedState | null>(null)
  const [toasts, setToasts] = useState<Notice[]>([])
  const feed = useRef<NotificationFeed | null>(null)

  useEffect(() => {
    if (!user) return
    const f = new NotificationFeed(
      user.id,
      setState,
      (n) => {
        setToasts((t) => [...t.filter((x) => x.id !== n.id), n].slice(-4))
        setTimeout(() => setToasts((t) => t.filter((x) => x.id !== n.id)), TOAST_MS)
      },
    )
    feed.current = f
    f.start()
    return () => {
      f.stop()
      feed.current = null
      setState(null)
      setToasts([])
    }
  }, [user])

  const markSeen = useCallback(() => feed.current?.markSeen(), [])
  const dismiss = useCallback((id: string) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  return { state, toasts, markSeen, dismiss }
}

