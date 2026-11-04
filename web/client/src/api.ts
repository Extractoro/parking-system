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

  constructor(status: number, problem: Problem, requestId: string | null) {
    super(problem.title ?? `HTTP ${status}`)
    this.status = status
    this.problem = problem
    this.requestId = requestId
  }
}

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
  try {
    response = await fetch(API_URL + path, { ...init, headers })
  } catch {
    throw new ApiError(0, { title: 'Server unavailable' }, null)
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
    throw new ApiError(response.status, problem, response.headers.get('X-Request-ID'))
  }
  return { body: body as T, headers: response.headers }
}

export const api = {
  garages: () => request<{ items: Garage[] }>('/v1/garages').then((r) => r.body.items),

  levelHours: (levelId: string, day: string) =>
    request<LevelHours>(`/v1/levels/${encodeURIComponent(levelId)}/hours?day=${encodeURIComponent(day)}`).then((r) => r.body),

  createBooking: (userId: string, data: BookingCreate) =>
    request<Booking>(
      '/v1/bookings',
      // the idempotency key is required by the contract; the retry logic comes with lab 2 (O2.4)
      { method: 'POST', body: JSON.stringify(data), headers: { 'Idempotency-Key': crypto.randomUUID() } },
      userId,
    ).then((r) => ({ booking: r.body, location: r.headers.get('Location') })),

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
