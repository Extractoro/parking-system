import { useState } from 'react'
import { api, type Session } from './api'
import { ProblemAlert } from './components'
import { Spinner } from './icons'

/** Login (O3.1): POST /v1/auth/login; the token goes to memory + sessionStorage (api.setSession). */
export default function LoginPage({ onLogin }: { onLogin: (s: Session) => void }) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!login.trim() || !password) return
    setBusy(true)
    setError(null)
    try {
      onLogin(await api.login(login.trim(), password))
    } catch (err) {
      setError(err)
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main id="content" className="page page-narrow">
      <form className="card section login-card" onSubmit={submit} data-testid="login-form">
        <div>
          <h1>Log in</h1>
          <p className="muted">Bookings belong to your account. The catalog of free spots is open to everyone.</p>
        </div>
        <label className="field">
          <span>Login</span>
          <input
            className="input"
            name="username"
            autoComplete="username"
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            autoFocus
            required
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            className="input"
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error != null && <ProblemAlert error={error} />}
        <button type="submit" className="btn btn-primary btn-lg" disabled={busy || !login.trim() || !password}>
          {busy && <Spinner />}
          Log in
        </button>
        <p className="demo-accounts">
          Demo accounts: <code>user1</code> / <code>user1-pass</code>, <code>user2</code> / <code>user2-pass</code>{' '}
          (drivers), <code>staff1</code> / <code>staff1-pass</code> (parking staff).
        </p>
      </form>
    </main>
  )
}
