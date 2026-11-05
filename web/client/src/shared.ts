// Constants and helpers shared by the pages (kept out of component files for React Fast Refresh).

export const USERS = [
  { id: 'u-1', login: 'user1' },
  { id: 'u-2', login: 'user2' },
]

export type View = 'book' | 'bookings' | 'barrier'

/** Short human reference of a booking instead of its full id. */
export function bookingCode(id: string): string {
  return '#' + id.replace(/-/g, '').slice(0, 6).toUpperCase()
}
