// HTTP client of the DPC api (dpc/openapi.yaml): barrier entries, the garage occupancy and the
// parallel integral of lab 2 (/v1/compute).
// The DPC api is called directly on :8000 with the API token (Authorization: Bearer); CORS allows
// DPC_CLIENT_ORIGIN. Errors are Problem Details, the same ApiError as the Web client.

import { ApiError, type Problem } from './api'

export const DPC_URL: string = import.meta.env.VITE_DPC_URL ?? 'http://localhost:8000'
const DPC_TOKEN: string = import.meta.env.VITE_DPC_TOKEN ?? 'dev-token'
const TIMEOUT_MS = 10000

/** Garages of the DPC demo data (migration 903); dpc-race is the test garage of the race experiment. */
export const DPC_GARAGES = [
  { id: 'center', name: 'Центр' },
  { id: 'mall', name: 'ТЦ Південний' },
  { id: 'dpc-race', name: 'DPC race test' },
]

export type EntryState = 'queued' | 'processing' | 'done' | 'rejected' | 'failed'
export const FINAL_STATES: EntryState[] = ['done', 'rejected', 'failed']

export interface EntryRequest {
  id: string
  event_id: string
  state: EntryState
  reason: string | null
  detail: string | null
  garage_id: string
  plate: string
  direction: 'in' | 'out'
  event_time: string
  booking_id: string | null
  received_at: string
  finished_at: string | null
  total_ms?: number | null
}

export interface GarageState {
  garage_id: string
  spots: number
  occupied: number
  inside: number
  reserved: number
  free_walk_in: number
  invariant_ok: boolean
  mode: 'locked' | 'unsafe'
}

export interface EntryCreate {
  event_id: string
  garage_id: string
  plate: string
  direction: 'in' | 'out'
  event_time: string
  booking_id?: string
}

export type Method = 'midpoint' | 'trapezoid' | 'simpson'
export type SumMode = 'none' | 'lock' | 'local'

export interface ComputeParams {
  a: number
  b: number
  n: number
  workers: 1 | 2 | 4
  method: Method
  mode: SumMode
  order?: 'forward' | 'reverse'
  block?: number
}

export interface ComputeResult {
  value: number
  reference: number
  abs_error: number
  rel_error: number
  seconds: number
  pool_start_seconds: number
  workers: number
  method: Method
  n: number
  mode: SumMode
  block: number
  within_tolerance: boolean
  tolerance: number
  timing: { dispatch_s: number; compute_s: number; combine_s: number; processes: number }
}

export interface ComputeJob {
  id: string
  state: 'running' | 'done' | 'failed'
  active_parts: number
  params: ComputeParams
  result: ComputeResult | null
  error: string | null
  created_at: string
  finished_at: string | null
}

async function request<T>(path: string, init: RequestInit = {}): Promise<{ body: T; headers: Headers }> {
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${DPC_TOKEN}`)
  if (init.body) headers.set('Content-Type', 'application/json')
  const timeout = new AbortController()
  const timer = setTimeout(() => timeout.abort(), TIMEOUT_MS)
  let response: Response
  try {
    response = await fetch(DPC_URL + path, { ...init, headers, signal: timeout.signal })
  } catch {
    throw new ApiError(0, { title: 'DPC service unavailable' }, null)
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
    const problem: Problem = body && typeof body === 'object' ? (body as Problem) : { title: `HTTP ${response.status}` }
    const retryAfter = Number(response.headers.get('Retry-After'))
    throw new ApiError(response.status, problem, response.headers.get('X-Request-ID'), retryAfter > 0 ? retryAfter : null)
  }
  return { body: body as T, headers: response.headers }
}

/** ISO 8601 UTC with Z and milliseconds, the format fixed in the DPC contract. */
export function eventTimeNow(): string {
  return new Date().toISOString()
}

export const dpc = {
  /** One attempt; the caller keeps the event_id for every repeat, so a repeat is the same request. */
  createEntry: (data: EntryCreate) =>
    request<EntryRequest>('/v1/entries', { method: 'POST', body: JSON.stringify(data) }).then((r) => r.body),

  entry: (id: string) => request<EntryRequest>(`/v1/entries/${encodeURIComponent(id)}`).then((r) => r.body),

  recent: (garageId: string, limit = 12) =>
    request<{ items: EntryRequest[] }>(`/v1/entries?garage_id=${encodeURIComponent(garageId)}&limit=${limit}`).then(
      (r) => r.body.items,
    ),

  /** POST /v1/compute: 202 with the job; 429 compute-busy while another computation runs. */
  startCompute: (p: ComputeParams) =>
    request<ComputeJob>('/v1/compute', { method: 'POST', body: JSON.stringify(p) }).then((r) => r.body),

  compute: (id: string) => request<ComputeJob>(`/v1/compute/${encodeURIComponent(id)}`).then((r) => r.body),

  computeList: (limit = 12) =>
    request<{ items: ComputeJob[]; running: string | null }>(`/v1/compute?limit=${limit}`).then((r) => r.body),

  garage: (garageId: string) => request<GarageState>(`/v1/garage/${encodeURIComponent(garageId)}`).then((r) => r.body),
}
