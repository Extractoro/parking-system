// Money is integer kopecks + currency; moments are shown in the zone of the garage, not of the browser.

export function money(minor: number, currency: string): string {
  const units = Math.trunc(minor / 100)
  const cents = Math.abs(minor % 100).toString().padStart(2, '0')
  return `${units}.${cents} ${currency}`
}

export function timeIn(iso: string, tz: string): string {
  return new Intl.DateTimeFormat('uk-UA', { timeZone: tz, hour: '2-digit', minute: '2-digit' }).format(new Date(iso))
}

export function dateTimeIn(iso: string, tz: string): string {
  return new Intl.DateTimeFormat('uk-UA', {
    timeZone: tz,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(iso))
}

/** Today's date (YYYY-MM-DD) in the zone of the garage. */
export function todayIn(tz: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date())
}
