// Constants and helpers shared by the pages (kept out of component files for React Fast Refresh).

export type View = 'book' | 'bookings' | 'access' | 'barrier' | 'compute'

/** Short id of a booking shown to people: the first group of the uuid (8 hex digits). The server
 *  accepts it in GET /v1/bookings/{id} too, among the bookings of the logged-in user only. */
export function bookingCode(id: string): string {
  return '#' + id.split('-')[0].toUpperCase()
}
