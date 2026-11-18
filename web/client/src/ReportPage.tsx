// DPC lab 4: the batch report of Spark. "Refresh report" starts the job (POST /v1/reports/main/refresh), the page
// polls GET /v1/reports/main while the job runs and shows the published rows next to the independent reference
// (GET /v1/reports/main/reference), compared by garage_id: counts and sums exactly, the average within 1e-9.

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'
import { dpc, type Report, type ReportReference, type ReportRow } from './dpc'
import { money } from './format'
import { AlertIcon, CheckCircle, Spinner, Wifi } from './icons'

const POLL_RUNNING_MS = 1000
const POLL_IDLE_MS = 5000
const TOLERANCE = 1e-9
const INT_FIELDS = ['sessions', 'total_minutes', 'revenue_minor'] as const

/** null = the row matches the reference row of the same garage; otherwise what differs. */
function rowDiff(row: ReportRow, ref: ReportRow | undefined): string | null {
  if (!ref) return 'missing in the reference'
  for (const f of INT_FIELDS) if (row[f] !== ref[f]) return `${f}: ${row[f]} ≠ ${ref[f]}`
  if (row.name !== ref.name) return 'name differs'
  const d = Math.abs(row.avg_minutes - ref.avg_minutes)
  if (d > TOLERANCE * Math.max(1, Math.abs(ref.avg_minutes))) return `avg_minutes differs by ${d.toExponential(1)}`
  return null
}

function compareAll(rows: ReportRow[], ref: ReportReference | null) {
  if (!ref) return null
  const byKey = new Map(ref.rows.map((r) => [r.garage_id, r]))
  const diffs = new Map(rows.map((r) => [r.garage_id, rowDiff(r, byKey.get(r.garage_id))]))
  const extra = ref.rows.filter((r) => !rows.some((x) => x.garage_id === r.garage_id)).length
  const ok = rows.length > 0 && extra === 0 && [...diffs.values()].every((d) => d === null)
  return { diffs, ok, extra }
}

function Problem({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 0) {
    return (
      <div className="alert alert-warn" role="status">
        <Wifi color="var(--accent)" />
        <span>The DPC service is unavailable. Start it with docker compose up -d api spark-runner.</span>
      </div>
    )
  }
  const e = error instanceof ApiError ? error : null
  const slug = (e?.problem.type ?? '').split('/').pop()
  const text: Record<string, [string, string]> = {
    'report-running': ['A report is already running', 'Wait for it to finish: the page shows its state.'],
    'runner-unavailable': ['Spark is not running', 'Start it with docker compose up -d spark-master spark-worker-1 spark-worker-2 spark-runner.'],
    'no-data': ['No data set', 'Generate it with docker compose run --rm generator.'],
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

const setName = (facts?: string | null) => (facts ? facts.split('/').slice(-2, -1)[0] : '–')

export default function ReportPage() {
  const [report, setReport] = useState<Report | null>(null)
  const [reference, setReference] = useState<ReportReference | null>(null)
  const [refLoading, setRefLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [dataset, setDataset] = useState<'small' | 'big'>('small')
  const refRun = useRef<string | null>(null)
  const alive = useRef(true)

  const loadReference = useCallback(async (run: string) => {
    refRun.current = run
    setRefLoading(true)
    try {
      const ref = await dpc.reportReference()
      if (alive.current && refRun.current === run) setReference(ref)
    } catch (err) {
      if (alive.current) setError(err)
    } finally {
      if (alive.current) setRefLoading(false)
    }
  }, [])

  const poll = useCallback(async () => {
    try {
      const r = await dpc.report()
      if (!alive.current) return null
      setReport(r)
      // the reference is computed once per published run (the same set as that run)
      if (r.published && refRun.current !== r.published.run) void loadReference(r.published.run)
      return r
    } catch (err) {
      if (alive.current) setError(err)
      return null
    }
  }, [loadReference])

  useEffect(() => {
    alive.current = true
    let timer: ReturnType<typeof setTimeout>
    const loop = async () => {
      const r = await poll()
      if (alive.current) timer = setTimeout(loop, r?.job.state === 'running' ? POLL_RUNNING_MS : POLL_IDLE_MS)
    }
    void loop()
    return () => {
      alive.current = false
      clearTimeout(timer)
    }
  }, [poll])

  async function refresh() {
    setStarting(true)
    setError(null)
    try {
      const job = await dpc.refreshReport(dataset)
      setReport((r) => (r ? { ...r, job } : { job, published: null, rows: [] }))
    } catch (err) {
      setError(err)
    } finally {
      setStarting(false)
      void poll()
    }
  }

  const job = report?.job
  const running = job?.state === 'running'
  const rows = report?.rows ?? []
  const sameRun = reference && report?.published && reference.published?.run === report.published.run
  const cmp = sameRun ? compareAll(rows, reference) : null
  const totalSessions = rows.reduce((s, r) => s + r.sessions, 0)

  return (
    <main id="content" className="page">
      <div className="page-head">
        <div>
          <h1>Report</h1>
          <p>
            Sessions, average parking time and revenue per garage: a Spark job joins the parking history with the garages
            and groups by garage; an independent reference without Spark checks the published result.
          </p>
        </div>
      </div>

      <div className="columns">
        <div className="col-main">
          <section className="card section" aria-labelledby="report-run-title">
            <h2 id="report-run-title" className="eyebrow">
              Spark job
            </h2>
            <div className="row-actions">
              <button
                type="button"
                className="btn btn-primary"
                disabled={starting || running}
                aria-busy={starting || running || undefined}
                onClick={() => void refresh()}
              >
                {(starting || running) && <Spinner size={16} color="#fff" />}
                Refresh report
              </button>
              <label className="field inline">
                <span>Data set</span>
                <select className="input" value={dataset} disabled={running} onChange={(e) => setDataset(e.target.value as 'small' | 'big')}>
                  <option value="small">small (10⁵ rows)</option>
                  <option value="big">big (10⁷ rows)</option>
                </select>
              </label>
              {job && (
                <span className={`badge state ${job.state === 'running' ? 'processing' : job.state}`} data-testid="job-state">
                  {job.state === 'none' ? 'no runs yet' : job.state}
                  {job.seconds != null ? ` · ${job.seconds} s` : ''}
                </span>
              )}
            </div>
            {job?.run && (
              <p className="small muted mono">
                run {job.run} · set {setName(job.facts)} · join {job.join}
              </p>
            )}
            {job?.state === 'failed' && report?.published && (
              <p className="small">The last run failed; the previously published report {report.published.run} is shown.</p>
            )}
            {error != null && <Problem error={error} />}
          </section>

          <section className="card section" aria-labelledby="report-rows-title">
            <div className="row-between">
              <h2 id="report-rows-title" className="eyebrow">
                Published report
              </h2>
              {cmp ? (
                <span className={`badge ${cmp.ok ? 'confirmed' : 'danger'}`} data-testid="reference-match">
                  {cmp.ok ? 'Matches the reference' : 'Differs from the reference'}
                </span>
              ) : (
                rows.length > 0 && (
                  <span className="badge" data-testid="reference-match">
                    {refLoading ? 'Computing the reference…' : 'Reference not loaded'}
                  </span>
                )
              )}
            </div>
            {rows.length === 0 ? (
              <p className="muted">No published report yet. Press “Refresh report”.</p>
            ) : (
              <table className="compute-table report-table">
                <caption>
                  {report?.published && (
                    <>
                      Run {report.published.run}, set {setName(report.published.facts)}, {totalSessions.toLocaleString('en-US')}{' '}
                      sessions
                    </>
                  )}
                </caption>
                <thead>
                  <tr>
                    <th>Garage</th>
                    <th className="num">Sessions</th>
                    <th className="num">Avg time, min</th>
                    <th className="num">Revenue</th>
                    <th>Reference</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const d = cmp?.diffs.get(r.garage_id)
                    return (
                      <tr key={r.garage_id} className={d ? 'bad' : ''}>
                        <td>
                          {r.name} <span className="muted small mono">{r.garage_id}</span>
                        </td>
                        <td className="mono num">{r.sessions.toLocaleString('en-US')}</td>
                        <td className="mono num">{r.avg_minutes.toFixed(2)}</td>
                        <td className="mono num">{money(r.revenue_minor, 'UAH')}</td>
                        <td>
                          {cmp ? (
                            d === null ? (
                              <span className="ok-mark">
                                <CheckCircle color="var(--success)" size={14} /> equal
                              </span>
                            ) : (
                              <span className="small">{d}</span>
                            )
                          ) : (
                            '–'
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </section>
        </div>

        <aside className="col-side">
          <section className="card section" aria-labelledby="report-meta-title">
            <h2 id="report-meta-title" className="eyebrow">
              How it is checked
            </h2>
            <dl className="kv">
              <div>
                <dt>Published</dt>
                <dd className="mono">{report?.published ? new Date(report.published.written_at).toISOString().slice(0, 19).replace('T', ' ') + ' UTC' : '–'}</dd>
              </div>
              <div>
                <dt>Reference set</dt>
                <dd className="mono">{reference ? setName(reference.facts) : '–'}</dd>
              </div>
              <div>
                <dt>Reference time</dt>
                <dd className="mono">{reference ? `${reference.seconds} s` : '–'}</dd>
              </div>
              <div>
                <dt>Rule</dt>
                <dd>counts and sums equal, average within {TOLERANCE.toExponential(0)}</dd>
              </div>
            </dl>
            {cmp && cmp.extra > 0 && <p className="small">{cmp.extra} garages of the reference are missing in the report.</p>}
          </section>
        </aside>
      </div>
    </main>
  )
}
