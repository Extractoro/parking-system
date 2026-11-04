import { useCallback, useEffect, useState } from 'react'

// The selection (page, garage, level, day, start, hours, tab) lives in the query string, so a
// reload or a shared link opens the same state and Back/Forward work.

type Params = Record<string, string | undefined>

function read(): URLSearchParams {
  return new URLSearchParams(window.location.search)
}

export function useUrlState(): [URLSearchParams, (patch: Params, mode?: 'push' | 'replace') => void] {
  const [params, setParams] = useState(read)

  useEffect(() => {
    const onPop = () => setParams(read())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const update = useCallback((patch: Params, mode: 'push' | 'replace' = 'replace') => {
    const next = read()
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === '') next.delete(k)
      else next.set(k, v)
    }
    const url = `${window.location.pathname}${next.size ? `?${next}` : ''}`
    if (mode === 'push') window.history.pushState(null, '', url)
    else window.history.replaceState(null, '', url)
    setParams(next)
  }, [])

  return [params, update]
}

/** href for a link that keeps Cmd/Ctrl+click working; the click itself is handled in place. */
export function hrefWith(patch: Params): string {
  const next = new URLSearchParams()
  for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v)
  return `${window.location.pathname}${next.size ? `?${next}` : ''}`
}
