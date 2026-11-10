// Constants and helpers shared by the pages (kept out of component files for React Fast Refresh).

export type View = 'book' | 'bookings' | 'access' | 'barrier' | 'compute'

/** Short human reference of a booking instead of its full id. */
export function bookingCode(id: string): string {
  return '#' + id.replace(/-/g, '').slice(0, 6).toUpperCase()
}
