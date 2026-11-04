// Money is integer kopecks + currency; moments are shown in the zone of the garage (garage.tz),
// never the browser's. Everything goes through Intl, no hand-built date or number strings.

const HOUR_MS = 3_600_000
const LOCALE = 'en-GB' // English, 24-hour clock, day before month

const cache = new Map<string, Intl.DateTimeFormat>()
function dtf(tz: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = tz + JSON.stringify(opts)
  let f = cache.get(key)
  if (!f) {
    f = new Intl.DateTimeFormat(LOCALE, { timeZone: tz, ...opts })
    cache.set(key, f)
  }
  return f
}

export function money(minor: number, currency: string): string {
  return new Intl.NumberFormat(LOCALE, { style: 'currency', currency, currencyDisplay: 'narrowSymbol' }).format(minor / 100)
}

export function time(iso: string | number, tz: string): string {
  return dtf(tz, { hour: '2-digit', minute: '2-digit' }).format(new Date(iso))
}

/** YYYY-MM-DD of a moment in the zone. */
export function dayOf(moment: string | number | Date, tz: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date(moment))
}

export function todayIn(tz: string): string {
  return dayOf(Date.now(), tz)
}

/** A real calendar date in YYYY-MM-DD within a sane range (2026-02-30 and "garbage" are not). */
export function isDay(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false
  const [y, m, d] = s.split('-').map(Number)
  const t = new Date(Date.UTC(y, m - 1, d))
  return y >= 1970 && y <= 9998 && t.getUTCMonth() === m - 1 && t.getUTCDate() === d
}

/** Calendar arithmetic on YYYY-MM-DD (no time zone involved). */
export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10)
}

// a calendar date is formatted at UTC noon, so the zone never shifts it to another day
function noon(day: string): Date {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d, 12))
}

export function weekdayShort(day: string): string {
  return dtf('UTC', { weekday: 'short' }).format(noon(day))
}

export function dayNumber(day: string): string {
  return dtf('UTC', { day: 'numeric' }).format(noon(day))
}

export function monthShort(day: string): string {
  return dtf('UTC', { month: 'short' }).format(noon(day))
}

export function dateLong(day: string): string {
  return dtf('UTC', { weekday: 'short', day: 'numeric', month: 'long' }).format(noon(day))
}

export function dateFull(day: string): string {
  return dtf('UTC', { weekday: 'long', day: 'numeric', month: 'long' }).format(noon(day))
}

export function hoursLabel(n: number): string {
  return n === 1 ? '1 hour' : `${n} hours`
}

export function plusHours(iso: string, hours: number): number {
  return Date.parse(iso) + hours * HOUR_MS
}

export { HOUR_MS }
