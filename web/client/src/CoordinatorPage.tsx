// DPC lab 6: the tariff manager. Two coordinators compete for the role /roles/tariff-manager in etcd; the owner
// registers its generation (create_revision of the role key) on the resource and sets the tariffs every 3 s. The page
// polls once a second: the owner and the generation (GET /v1/coordinator), the current tariffs (GET /v1/tariff), the
// last 20 operations of the resource with accepted / rejected and the reason (GET /v1/history) and the one-command
// check of the history (GET /v1/history/check).

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'
import { dpc, type Coordinator, type HistoryCheck, type HistoryRow, type Tariff } from './dpc'
import { money } from './format'
import { AlertIcon, CheckCircle, Wifi } from './icons'

const POLL_MS = 1000
const HISTORY_LIMIT = 20
const SCOPES = [
  { id: '', name: 'All garages' },
  { id: 'mall', name: 'mall' },
  { id: 'center', name: 'center' },
  { id: 'dpc-race', name: 'dpc-race (test)' },
]

const REASON_TEXT: Record<HistoryRow['reason'], string> = {
  registered: 'generation registered',
  accepted: 'accepted',
  unfenced: 'accepted without check',
  stale: 'rejected: stale generation',
  unregistered: 'rejected: not registered',
}

function clock(iso: string): string {
  return new Date(iso).toISOString().slice(11, 23)
}

function Problem({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 0) {
    return (
      <div className="alert alert-warn" role="status">
        <Wifi color="var(--accent)" />
        <span>The DPC service is unavailable. Start it with docker compose up -d api etcd coordinator-a coordinator-b.</span>
      </div>
    )
  }
  const e = error instanceof ApiError ? error : null
  const slug = (e?.problem.type ?? '').split('/').pop()
  const text: Record<string, [string, string]> = {
    'etcd-unavailable': ['etcd is not running', 'Start it with docker compose up -d etcd.'],
    unauthorized: ['Wrong API token', 'Set VITE_DPC_TOKEN in web/client/.env.local.'],
  }
  const [title, detail] = text[slug ?? ''] ?? [e?.problem.title ?? 'Something went wrong', e?.problem.detail ?? String(error)]
  return (
    <div className="alert alert-danger" role="alert">
      <AlertIcon color="var(--danger)" size={20} />
      <div className="alert-body">
        <strong>{title}</strong>
        <span>{detail}</span>
      </div>
    </div>
  )
}

export default function CoordinatorPage() {
  const [coordinator, setCoordinator] = useState<Coordinator | null>(null)
  const [tariffs, setTariffs] = useState<Tariff[]>([])
  const [history, setHistory] = useState<HistoryRow[]>([])
  const [check, setCheck] = useState<HistoryCheck | null>(null)
  const [scope, setScope] = useState('')
  const [error, setError] = useState<unknown>(null)
  const alive = useRef(true)

  const poll = useCallback(async () => {
    try {
      const [c, t, h, k] = await Promise.all([
        dpc.coordinator(),
        dpc.tariffs(),
        dpc.history(HISTORY_LIMIT, scope || undefined),
        dpc.historyCheck(scope || undefined),
      ])
      if (!alive.current) return
      setCoordinator(c)
      setTariffs(t)
      setHistory(h)
      setCheck(k)
      setError(null)
    } catch (err) {
      if (alive.current) setError(err)
    }
  }, [scope])

  useEffect(() => {
    alive.current = true
    let timer: ReturnType<typeof setTimeout>
    const loop = async () => {
      await poll()
      if (alive.current) timer = setTimeout(loop, POLL_MS)
    }
    void loop()
    return () => {
      alive.current = false
      clearTimeout(timer)
    }
  }, [poll])

  const registered = new Map((coordinator?.registered ?? []).map((r) => [r.garage_id, r]))

  return (
    <main id="content" className="page">
      <div className="page-head">
        <div>
          <h1>Coordinator</h1>
          <p>
            The dynamic tariff manager: one of two coordinators holds the role through an etcd lease. Its generation is
            the revision of the role key; the resource accepts a tariff only from the registered current generation.
          </p>
        </div>
      </div>

      {error != null && <Problem error={error} />}

      <div className="columns">
        <div className="col-main">
          <section className="card section" aria-labelledby="coord-owner-title">
            <h2 id="coord-owner-title" className="eyebrow">
              Role {coordinator?.role ?? '/roles/tariff-manager'}
            </h2>
            <dl className="kv" data-testid="coordinator-owner">
              <div>
                <dt>Owner</dt>
                <dd className="mono">
                  {coordinator ? (
                    coordinator.owner ? (
                      <span className="badge confirmed">{coordinator.owner}</span>
                    ) : (
                      <span className="badge danger">no owner</span>
                    )
                  ) : (
                    '–'
                  )}
                </dd>
              </div>
              <div>
                <dt>Generation</dt>
                <dd className="mono" data-testid="coordinator-generation">
                  {coordinator?.generation ?? '–'}
                </dd>
              </div>
              <div>
                <dt>Lease</dt>
                <dd className="mono">
                  {coordinator ? `TTL ${coordinator.lease_ttl_s} s` : '–'}
                  {coordinator?.ttl_left_s != null ? `, ${coordinator.ttl_left_s} s left` : ''}
                </dd>
              </div>
            </dl>
          </section>

          <section className="card section" aria-labelledby="coord-tariff-title">
            <h2 id="coord-tariff-title" className="eyebrow">
              Current tariffs
            </h2>
            {tariffs.length === 0 ? (
              <p className="muted">No tariffs yet.</p>
            ) : (
              <table className="compute-table">
                <thead>
                  <tr>
                    <th>Garage</th>
                    <th className="num">Tariff per hour</th>
                    <th>Set by</th>
                    <th className="num">Generation</th>
                    <th className="num">Registered</th>
                  </tr>
                </thead>
                <tbody>
                  {tariffs.map((t) => (
                    <tr key={t.garage_id}>
                      <td>
                        {t.name} <span className="muted small mono">{t.garage_id}</span>
                      </td>
                      <td className="mono num">{money(t.minor_per_hour, t.currency)}</td>
                      <td className="mono">{t.owner ?? 'seed'}</td>
                      <td className="mono num">{t.generation}</td>
                      <td className="mono num">{registered.get(t.garage_id)?.generation ?? t.registered.generation}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="card section" aria-labelledby="coord-history-title">
            <div className="row-between">
              <h2 id="coord-history-title" className="eyebrow">
                Last {HISTORY_LIMIT} operations of the resource
              </h2>
              <label className="field inline">
                <span>Garage</span>
                <select className="input" value={scope} onChange={(e) => setScope(e.target.value)} data-testid="history-scope">
                  {SCOPES.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {history.length === 0 ? (
              <p className="muted">No operations yet.</p>
            ) : (
              <table className="compute-table" data-testid="history">
                <thead>
                  <tr>
                    <th className="num">#</th>
                    <th>Time, UTC</th>
                    <th>Garage</th>
                    <th>Owner</th>
                    <th className="num">Gen.</th>
                    <th className="num">Tariff</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h) => (
                    <tr key={h.id} className={h.accepted ? '' : 'bad'}>
                      <td className="mono num">{h.id}</td>
                      <td className="mono">{clock(h.created_at)}</td>
                      <td className="mono">{h.garage_id}</td>
                      <td className="mono">{h.owner}</td>
                      <td className="mono num">{h.generation}</td>
                      <td className="mono num">{h.minor_per_hour != null ? money(h.minor_per_hour, 'UAH') : '–'}</td>
                      <td>
                        <span
                          className={`badge state ${h.accepted ? (h.reason === 'unfenced' ? 'processing' : 'done') : 'rejected'}`}
                        >
                          {REASON_TEXT[h.reason]}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>

        <aside className="col-side">
          <section className="card section" aria-labelledby="coord-check-title">
            <h2 id="coord-check-title" className="eyebrow">
              History check
            </h2>
            {check ? (
              <>
                <p data-testid="history-check">
                  {check.ok ? (
                    <span className="ok-mark">
                      <CheckCircle color="var(--success)" size={16} /> ok: no violations
                    </span>
                  ) : (
                    <span className="badge danger">ok = false: {check.violations.length} violations</span>
                  )}
                </p>
                <dl className="kv">
                  <div>
                    <dt>Scope</dt>
                    <dd className="mono">{check.garage_id ?? 'all garages'}</dd>
                  </div>
                  <div>
                    <dt>Writes</dt>
                    <dd className="mono">{check.writes}</dd>
                  </div>
                  <div>
                    <dt>Rejected</dt>
                    <dd className="mono">{check.rejected}</dd>
                  </div>
                  <div>
                    <dt>Registrations</dt>
                    <dd className="mono">{check.registrations}</dd>
                  </div>
                </dl>
                {check.violations.slice(0, 5).map((v, i) => (
                  <p key={i} className="small mono">
                    {v.kind} · {v.garage_id}
                    {v.kind === 'stale-accepted'
                      ? ` · ${String(v.owner)} gen ${String(v.generation)} after gen ${String(v.registered)}`
                      : ''}
                  </p>
                ))}
              </>
            ) : (
              <p className="muted">–</p>
            )}
            <p className="small muted">
              Rules: no accepted write of an older generation after a newer one was registered; the tariff equals the
              last accepted write.
            </p>
          </section>
        </aside>
      </div>
    </main>
  )
}
