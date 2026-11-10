import { useCallback, useEffect, useState } from 'react'
import AccessPage from './AccessPage'
import { api, currentSession, setSession, setUnauthorizedHandler, type Session } from './api'
import BarrierPage from './BarrierPage'
import BookingPage from './BookingPage'
import BookingsPage from './BookingsPage'
import ComputePage from './ComputePage'
import { Header } from './components'
import ErrorBoundary from './ErrorBoundary'
import LoginPage from './LoginPage'
import type { View } from './shared'
import { useUrlState } from './useUrlState'

const VIEWS: View[] = ['book', 'bookings', 'access', 'barrier', 'compute']

export default function App() {
  const [params, setParams] = useUrlState()
  // O3.1: the login session (token in memory + sessionStorage, see api.ts); null -> login page
  const [session, setSessionState] = useState<Session | null>(currentSession)
  const v = params.get('view') as View | null
  const view: View = v && VIEWS.includes(v) ? v : 'book'

  // any 401 to a request with a token (logged out, expired) -> back to the login page
  useEffect(() => {
    setUnauthorizedHandler(() => setSessionState(null))
    return () => setUnauthorizedHandler(null)
  }, [])

  function navigate(v: View) {
    setParams({ view: v === 'book' ? undefined : v, tab: undefined }, 'push')
    window.scrollTo({ top: 0 })
  }

  function login(s: Session) {
    setSession(s)
    setSessionState(s)
  }

  const logout = useCallback(async () => {
    try {
      await api.logout() // deletes the session on the server: the token is dead on both instances
    } catch {
      // the server is unreachable: the token is still forgotten here and expires on the server
    }
    setSession(null)
    setSessionState(null)
  }, [])

  // the DPC pages use their own DPC token and work without the Web login
  const needsLogin = view === 'book' || view === 'bookings' || view === 'access'
  const user = session?.user ?? null
  return (
    <>
      <a className="skip" href="#content">
        Skip to content
      </a>
      <Header view={view} onNavigate={navigate} user={user} onLogout={logout} />
      <ErrorBoundary key={`${view}/${user?.id ?? '-'}`}>
        {view === 'compute' ? (
          <ComputePage />
        ) : view === 'barrier' ? (
          <BarrierPage params={params} setParams={setParams} />
        ) : needsLogin && !user ? (
          <LoginPage onLogin={login} />
        ) : view === 'access' && user ? (
          <AccessPage user={user} />
        ) : view === 'bookings' ? (
          <BookingsPage params={params} setParams={setParams} onNavigate={navigate} />
        ) : (
          <BookingPage params={params} setParams={setParams} onNavigate={navigate} />
        )}
      </ErrorBoundary>
    </>
  )
}
