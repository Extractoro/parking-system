import { Component, type ReactNode } from 'react'

/** Last line of defence: an unexpected render error shows a message instead of a blank page. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <main id="content" className="page page-narrow">
        <div className="card empty" role="alert">
          <strong style={{ fontSize: 15 }}>Something went wrong on this page</strong>
          <span className="muted">Your bookings are safe. Start again from a clean page.</span>
          <a className="btn btn-primary" href={window.location.pathname}>
            Start again
          </a>
        </div>
      </main>
    )
  }
}
