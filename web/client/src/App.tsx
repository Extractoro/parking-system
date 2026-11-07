import { useState } from 'react'
import BarrierPage from './BarrierPage'
import BookingPage from './BookingPage'
import BookingsPage from './BookingsPage'
import ComputePage from './ComputePage'
import { Header } from './components'
import ErrorBoundary from './ErrorBoundary'
import { USERS, type View } from './shared'
import { useUrlState } from './useUrlState'

export default function App() {
  const [params, setParams] = useUrlState()
  // until lab 3 the "account" is one of the pre-created users (sent as X-User-Id)
  const [userId, setUserId] = useState(() => {
    const saved = sessionStorage.getItem('userId')
    return USERS.some((u) => u.id === saved) ? (saved as string) : USERS[0].id
  })
  const v = params.get('view')
  const view: View = v === 'bookings' || v === 'barrier' || v === 'compute' ? v : 'book'

  function navigate(v: View) {
    setParams({ view: v === 'book' ? undefined : v, tab: undefined }, 'push')
    window.scrollTo({ top: 0 })
  }

  function changeUser(id: string) {
    sessionStorage.setItem('userId', id)
    setUserId(id)
  }

  return (
    <>
      <a className="skip" href="#content">
        Skip to content
      </a>
      <Header view={view} onNavigate={navigate} userId={userId} onUser={changeUser} />
      <ErrorBoundary key={`${view}/${userId}`}>
        {view === 'compute' ? (
          <ComputePage />
        ) : view === 'barrier' ? (
          <BarrierPage params={params} setParams={setParams} />
        ) : view === 'bookings' ? (
          <BookingsPage userId={userId} params={params} setParams={setParams} onNavigate={navigate} />
        ) : (
          <BookingPage userId={userId} params={params} setParams={setParams} onNavigate={navigate} />
        )}
      </ErrorBoundary>
    </>
  )
}
