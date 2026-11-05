// HTTP client of the Web API (web/openapi.yaml). Every error answer is Problem Details (RFC 9457).
// Types list only the fields the client uses: unknown extra fields of a response are simply ignored.

export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8080'

export interface Problem {
  type?: string
  title?: string
  status?: number
  detail?: string
  errors?: { field: string; message: string }[]
  [extra: string]: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly problem: Problem
  readonly requestId: string | null
  readonly retryAfter: number | null // seconds, from Retry-After

  constructor(status: number, problem: Problem, requestId: string | null, retryAfter: number | null = null) {
    super(problem.title ?? `HTTP ${status}`)
    this.status = status
    this.problem = problem
    this.requestId = requestId
    this.retryAfter = retryAfter
  }
}

/** One attempt that got no answer in this time is treated as lost (status 0) and may be retried. */
const TIMEOUT_MS = 10000

export interface Level {
  id: string
  name: string
  spots: number
  closed: boolean
}

export interface Garage {
  id: string
  name: string
  tz: string
  tariff_minor_per_hour: number
  currency: string
  spots: number
  levels: Level[]
}

export interface HourSlot {
  hour: string
  free: number
  spots: number
}

export interface LevelHours {
  level_id: string
  garage_id: string
  day: string
  tz: string
  spots: number
  closed: boolean
  hours: HourSlot[]
}

export interface Booking {
  id: string
  level_id: string
  garage_id: string
  plate: string
  from_hour: string
  to_hour: string
  hours: number
  price_minor: number
  currency: string
  status: string
  version: number
  created_at: string
}

export interface BookingPage {
  items: Booking[]
  next_cursor: string | null
}

export interface BookingCreate {
  level_id: string
  from_hour: string
  hours: number
  plate: string
}

async function request<T>(path: string, init: RequestInit = {}, userId?: string): Promise<{ body: T; headers: Headers }> {
  const headers = new Headers(init.headers)
  if (userId) headers.set('X-User-Id', userId) // until lab 3: the pre-created user
  if (init.body) headers.set('Content-Type', 'application/json')

  let response: Response
  const timeout = new AbortController()
  const timer = setTimeout(() => timeout.abort(), TIMEOUT_MS)
  try {
    response = await fetch(API_URL + path, { ...init, headers, signal: timeout.signal })
  } catch {
    // no answer at all: the request may or may not have been done on the server
    throw new ApiError(0, { title: 'Server unavailable' }, null)
  } finally {
    clearTimeout(timer)
  }

  const text = await response.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (!response.ok) {
    const problem: Problem =
      body && typeof body === 'object' ? (body as Problem) : { title: `HTTP ${response.status}`, detail: text.slice(0, 200) }
    const retryAfter = Number(response.headers.get('Retry-After'))
    throw new ApiError(
      response.status,
      problem,
      response.headers.get('X-Request-ID'),
      Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
    )
  }
  return { body: body as T, headers: response.headers }
}

function bookingPath(id: string): string {
  return `/v1/bookings/${encodeURIComponent(id)}`
}

export const api = {
  garages: () => request<{ items: Garage[] }>('/v1/garages').then((r) => r.body.items),

  levelHours: (levelId: string, day: string) =>
    request<LevelHours>(`/v1/levels/${encodeURIComponent(levelId)}/hours?day=${encodeURIComponent(day)}`).then((r) => r.body),

  /** One attempt. The caller makes the key once per user action and sends it with every retry. */
  createBooking: (userId: string, data: BookingCreate, idempotencyKey: string) =>
    request<Booking>(
      '/v1/bookings',
      { method: 'POST', body: JSON.stringify(data), headers: { 'Idempotency-Key': idempotencyKey } },
      userId,
    ).then((r) => ({ booking: r.body, replayed: r.headers.get('Idempotent-Replayed') === 'true' })),

  /** Plate change with the version the user saw: another version -> 412. */
  updatePlate: (userId: string, id: string, plate: string, version: number) =>
    request<Booking>(
      bookingPath(id),
      { method: 'PATCH', body: JSON.stringify({ plate }), headers: { 'If-Match': `"${version}"` } },
      userId,
    ).then((r) => r.body),

  /** Safe to repeat: a cancelled booking answers 200 again and frees nothing twice. */
  cancelBooking: (userId: string, id: string) =>
    request<Booking>(`${bookingPath(id)}/cancel`, { method: 'POST' }, userId).then((r) => r.body),

  /** Reads EVERY page of the list by next_cursor (O1.5 + O1.7). */
  async allBookings(userId: string, limit = 5): Promise<{ items: Booking[]; pages: number }> {
    const items: Booking[] = []
    let cursor: string | null = null
    let pages = 0
    do {
      const query: string = `?limit=${limit}` + (cursor ? `&cursor=${encodeURIComponent(cursor)}` : '')
      const { body } = await request<BookingPage>('/v1/bookings' + query, {}, userId)
      items.push(...body.items)
      cursor = body.next_cursor
      pages += 1
    } while (cursor)
    return { items, pages }
  },
}
