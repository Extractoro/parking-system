// Retries of one user action. The caller keeps ONE Idempotency-Key for all attempts, so a retry
// after a lost answer gets the stored result instead of booking twice.
import { ApiError } from './api'

export const RETRY = {
  baseMs: 500,
  capMs: 8000,
  attempts: 5, // the first try and up to 4 retries
  budgetMs: 20000, // total waiting between attempts
}

/** Problem type slug: the last part of `type`. */
export function problemSlug(e: unknown): string {
  return e instanceof ApiError ? ((e.problem.type ?? '').split('/').pop() ?? '') : ''
}

/** Only these outcomes leave the result unknown or say "come back later":
 *  no answer (network, timeout), a proxy/availability error, the same key still running. */
export function isRetryable(e: unknown): boolean {
  if (!(e instanceof ApiError)) return false
  if (e.status === 0 || e.status === 502 || e.status === 503 || e.status === 504) return true
  return e.status === 409 && problemSlug(e) === 'idempotency-in-progress'
}

/** Full jitter: random in [0, min(cap, base * 2^attempt)), but never less than Retry-After. */
export function backoffMs(attempt: number, retryAfterS: number | null, random = Math.random): number {
  const ceiling = Math.min(RETRY.capMs, RETRY.baseMs * 2 ** attempt)
  return Math.max(Math.floor(random() * ceiling), (retryAfterS ?? 0) * 1000)
}

export type AttemptInfo = { attempt: number; of: number; waitMs: number; error: unknown }

/** Runs `fn` until it succeeds, fails with a final answer, or the attempts / time budget end.
 *  Throws the last error; `isRetryable(err)` then tells the caller the result is unknown. */
export async function withRetries<T>(fn: () => Promise<T>, onRetry?: (info: AttemptInfo) => void): Promise<T> {
  let waited = 0
  for (let attempt = 0; ; attempt++) {
    try {
      return await fn()
    } catch (err) {
      if (!isRetryable(err) || attempt + 1 >= RETRY.attempts) throw err
      const wait = backoffMs(attempt, err instanceof ApiError ? err.retryAfter : null)
      if (waited + wait > RETRY.budgetMs) throw err
      onRetry?.({ attempt: attempt + 2, of: RETRY.attempts, waitMs: wait, error: err })
      await new Promise((r) => setTimeout(r, wait))
      waited += wait
    }
  }
}
