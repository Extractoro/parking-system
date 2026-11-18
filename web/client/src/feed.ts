// Live notifications (Web lab 5): one WebSocket per tab through nginx to a gateway, and the feed over HTTP.
//
// One session:
//   1. POST /v1/ws-tickets -> ticket; new WebSocket(.../v1/ws?ticket=...). The browser sends Origin itself.
//      From now on live frames go into a buffer.
//   2. GET /v1/me/notifications/head -> head.
//   3. GET /v1/me/notifications?after=last_applied page by page until head; apply each.
//   4. Apply the buffer, then live frames as they come: position <= last_applied -> already applied, skip;
//      position > last_applied + 1 -> read the history up to it first (a gap), then it is applied from there.
// A close (gateway stopped, network, logout) -> state "reconnecting", a pause that grows 1, 2, 4 ... 30 s with
// jitter, and a new session with a NEW ticket from last_applied. After a logout the ticket request gets 401,
// api.ts forgets the session and App stops the feed.
// The same algorithm runs in the experiment client experiments/feed_client.py.
// last_applied and the last notifications are kept in sessionStorage per user (key notifications:<user id>),
// so a reload of the tab continues from where it was and shows nothing twice.

import { API_URL, api, type Notice } from './api'

export type FeedStatus = 'connecting' | 'online' | 'reconnecting'

export interface FeedState {
  status: FeedStatus
  items: Notice[] // newest first
  unread: number
  gateway: string | null
}

const KEEP = 30 // notifications kept for the list
const FIRST_LOAD = 20 // a tab with no saved state starts this many before head
const PAUSE_MIN_MS = 1000
const PAUSE_MAX_MS = 30000

interface Saved {
  last: number
  seen: number
  items: Notice[]
}

function load(key: string): Saved | null {
  try {
    return JSON.parse(sessionStorage.getItem(key) ?? 'null') as Saved | null
  } catch {
    return null
  }
}

export class NotificationFeed {
  private readonly key: string
  private saved: Saved | null
  private last: number
  private seen: number
  private items: Notice[]
  private status: FeedStatus = 'connecting'
  private gateway: string | null = null
  private ws: WebSocket | null = null
  private attempt = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private stopped = false
  private work: Promise<void> = Promise.resolve() // applying is strictly one step after another
  private readonly onChange: (s: FeedState) => void
  private readonly onNew: (n: Notice) => void

  constructor(userId: string, onChange: (s: FeedState) => void, onNew: (n: Notice) => void) {
    this.key = `notifications:${userId}`
    this.saved = load(this.key)
    this.last = this.saved?.last ?? 0
    this.seen = this.saved?.seen ?? 0
    this.items = this.saved?.items ?? []
    this.onChange = onChange
    this.onNew = onNew
  }

  start(): void {
    this.emit()
    void this.connect()
  }

  stop(): void {
    this.stopped = true
    if (this.timer) clearTimeout(this.timer)
    const ws = this.ws
    this.ws = null
    ws?.close(1000)
  }

  /** The list was opened: everything up to last_applied is read. */
  markSeen(): void {
    this.seen = this.last
    this.persist()
    this.emit()
  }

  private emit(): void {
    this.onChange({
      status: this.status,
      items: this.items,
      unread: this.items.filter((n) => n.position > this.seen).length,
      gateway: this.gateway,
    })
  }

  private persist(): void {
    try {
      sessionStorage.setItem(this.key, JSON.stringify({ last: this.last, seen: this.seen, items: this.items }))
    } catch {
      // storage full or blocked: the feed still works for this page
    }
  }

  private setStatus(status: FeedStatus): void {
    this.status = status
    this.emit()
  }

  private enqueue(step: () => Promise<void>): Promise<void> {
    this.work = this.work.then(step, step)
    return this.work
  }

  private async connect(): Promise<void> {
    if (this.stopped) return
    this.setStatus(this.attempt === 0 && this.status === 'connecting' ? 'connecting' : 'reconnecting')
    let ticket: string
    try {
      ticket = await api.wsTicket()
    } catch {
      this.retry() // 401 after a logout: App stops the feed; otherwise the server is unreachable
      return
    }
    if (this.stopped) return
    const url = new URL('/v1/ws', API_URL.replace(/^http/, 'ws'))
    url.searchParams.set('ticket', ticket)
    const ws = new WebSocket(url.href)
    this.ws = ws
    const buffer: Notice[] = []
    let synced = false

    ws.onmessage = (event) => {
      let frame: Partial<Notice> & { type?: string; gateway?: string }
      try {
        frame = JSON.parse(event.data as string)
      } catch {
        return
      }
      if (frame.type === 'hello') {
        this.gateway = frame.gateway ?? null
        void this.enqueue(() => this.sync(ws, buffer).then(() => void (synced = true)))
        return
      }
      if (typeof frame.position !== 'number') return
      if (synced) void this.enqueue(() => this.live(frame as Notice, true))
      else buffer.push(frame as Notice)
    }
    ws.onclose = () => {
      if (this.ws !== ws) return // stopped, or an old socket
      this.ws = null
      this.gateway = null
      this.retry()
    }
  }

  /** Steps 2-4 of a session: head, the history up to it, the buffer. */
  private async sync(ws: WebSocket, buffer: Notice[]): Promise<void> {
    const firstEver = this.saved === null && this.last === 0
    try {
      const head = await api.notificationsHead()
      if (firstEver) {
        // a new tab: the last few are shown as already read, older ones are not loaded
        this.last = Math.max(0, head - FIRST_LOAD)
        this.seen = head
        this.saved = { last: this.last, seen: this.seen, items: [] }
      }
      await this.catchUp(head, !firstEver)
      while (buffer.length) await this.live(buffer.shift()!, !firstEver)
    } catch {
      ws.close() // the history could not be read: start over with a new ticket
      return
    }
    this.attempt = 0
    this.setStatus('online')
  }

  private async live(n: Notice, toast: boolean): Promise<void> {
    if (n.position <= this.last) return // already applied (from the history)
    if (n.position > this.last + 1) await this.catchUp(n.position, toast) // a gap: it comes from the history
    if (n.position === this.last + 1) this.apply(n, toast)
  }

  private async catchUp(target: number, toast: boolean): Promise<void> {
    while (this.last < target) {
      const page = await api.notificationsAfter(this.last)
      if (page.items.length === 0) break
      for (const n of page.items) if (n.position === this.last + 1) this.apply(n, toast)
    }
  }

  private apply(n: Notice, toast: boolean): void {
    this.last = n.position
    this.items = [n, ...this.items.filter((x) => x.id !== n.id)].slice(0, KEEP)
    this.persist()
    this.emit()
    if (toast) this.onNew(n)
  }

  private retry(): void {
    if (this.stopped) return
    this.setStatus('reconnecting')
    const base = Math.min(PAUSE_MIN_MS * 2 ** this.attempt, PAUSE_MAX_MS)
    this.attempt += 1
    this.timer = setTimeout(() => void this.connect(), base * (0.75 + Math.random() / 2))
  }
}
