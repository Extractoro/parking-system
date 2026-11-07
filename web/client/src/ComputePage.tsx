import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'
import { dpc, type ComputeJob, type ComputeParams, type ComputeResult, type Method, type SumMode } from './dpc'
import { AlertIcon, CheckCircle, Spinner, Wifi } from './icons'

const POLL_MS = 200
const LIST_MS = 2000
const MAX_BUSY_RETRIES = 30

const METHODS: Method[] = ['midpoint', 'trapezoid', 'simpson']
const MODES: { id: SumMode; label: string }[] = [
  { id: 'local', label: 'local sums' },
  { id: 'lock', label: 'shared + lock' },
  { id: 'none', label: 'shared, no lock' },
]

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const fmt = (v: number, digits = 6) => v.toLocaleString('en-US', { maximumFractionDigits: digits })
const sci = (v: number) => (v === 0 ? '0' : v.toExponential(2))
const sec = (v: number) => `${v.toFixed(3)} s`

function ComputeProblem({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 0) {
    return (
      <div className="alert alert-warn" role="status">
        <Wifi color="var(--accent)" />
        <span>The DPC service is unavailable. Start it with docker compose up -d api.</span>
      </div>
    )
  }
  const e = error instanceof ApiError ? error : null
  const slug = (e?.problem.type ?? '').split('/').pop()
  const text: Record<string, [string, string]> = {
    'compute-busy': ['Another computation is running', 'Only one computation runs at a time. Try again in a moment.'],
    'invalid-request': ['Check the parameters', 'a < b within 0-14, n from 1 to 10^9, Simpson needs an even n.'],
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

/** POST (waiting out 429 compute-busy), then poll the job until it is no longer running. */
async function runJob(params: ComputeParams, onJob: (j: ComputeJob) => void, onNote: (s: string | null) => void) {
  let job: ComputeJob | null = null
  for (let attempt = 1; !job; attempt++) {
    try {
      job = await dpc.startCompute(params)
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 429 || attempt >= MAX_BUSY_RETRIES) throw err
      onNote('Another computation is running, waiting…')
      await sleep((err.retryAfter ?? 1) * 1000)
    }
  }
  onNote(null)
  onJob(job)
  while (job.state === 'running') {
    await sleep(POLL_MS)
    job = await dpc.compute(job.id)
    onJob(job)
  }
  if (job.state !== 'done' || !job.result) throw new ApiError(500, { title: 'The computation failed', detail: job.error ?? '' }, null)
  return job.result
}

function ResultCard({ job }: { job: ComputeJob }) {
  const r = job.result
  if (!r) {
    return (
      <div className="alert alert-warn" role="status">
        <Spinner size={18} />
        <div className="alert-body">
          <strong>Computing on {job.params.workers} process{job.params.workers > 1 ? 'es' : ''}…</strong>
          <span>Active parts: {job.active_parts}</span>
        </div>
      </div>
    )
  }
  return (
    <section className="card section compute-result" aria-labelledby="compute-result-title">
      <div className="row-between">
        <h2 id="compute-result-title" className="eyebrow">
          Result
        </h2>
        <span className={`badge ${r.within_tolerance ? 'confirmed' : 'danger'}`}>
          {r.within_tolerance ? `Within tolerance ${sci(r.tolerance)}` : `Outside tolerance ${sci(r.tolerance)}`}
        </span>
      </div>
      <p className="compute-value">
        <span className="mono">{fmt(r.value, 9)}</span> <span className="muted">car-hours</span>
      </p>
      <dl className="kv">
        <div>
          <dt>Reference F(b) − F(a)</dt>
          <dd className="mono">{fmt(r.reference, 9)}</dd>
        </div>
        <div>
          <dt>Absolute / relative error</dt>
          <dd className="mono">
            {sci(r.abs_error)} / {sci(r.rel_error)}
          </dd>
        </div>
        <div>
          <dt>Time (pool start)</dt>
          <dd className="mono">
            {sec(r.seconds)} ({sec(r.pool_start_seconds)})
          </dd>
        </div>
        <div>
          <dt>Longest part</dt>
          <dd className="mono">{sec(r.timing.compute_s)}</dd>
        </div>
        <div>
          <dt>Processes</dt>
          <dd className="mono">{r.timing.processes}</dd>
        </div>
        <div>
          <dt>Method, mode, n</dt>
          <dd className="mono">
            {r.method}, {r.mode}, {r.n.toLocaleString('en-US')}
          </dd>
        </div>
      </dl>
    </section>
  )
}

function CompareTable({ runs }: { runs: ComputeResult[] }) {
  const t1 = runs.find((r) => r.workers === 1)?.seconds
  return (
    <table className="compute-table">
      <caption>Speed-up of one run each: S_p = T_1 / T_p, E_p = S_p / p</caption>
      <thead>
        <tr>
          <th>p</th>
          <th>T_p</th>
          <th>S_p</th>
          <th>E_p</th>
          <th>rel. error</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => {
          const s = t1 ? t1 / r.seconds : null
          return (
            <tr key={r.workers}>
              <td className="mono">{r.workers}</td>
              <td className="mono">{sec(r.seconds)}</td>
              <td className="mono">{s ? s.toFixed(2) : '–'}</td>
              <td className="mono">{s ? (s / r.workers).toFixed(2) : '–'}</td>
              <td className="mono">{sci(r.rel_error)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default function ComputePage() {
  const [a, setA] = useState('0')
  const [b, setB] = useState('14')
  const [n, setN] = useState('20000000')
  const [workers, setWorkers] = useState<1 | 2 | 4>(4)
  const [method, setMethod] = useState<Method>('midpoint')
  const [mode, setMode] = useState<SumMode>('local')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [job, setJob] = useState<ComputeJob | null>(null)
  const [compare, setCompare] = useState<ComputeResult[]>([])
  const [history, setHistory] = useState<ComputeJob[]>([])
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const list = await dpc.computeList()
      if (alive.current) setHistory(list.items)
    } catch {
      /* the form shows the errors of its own requests */
    }
  }, [])

  useEffect(() => {
    alive.current = true
    void refresh()
    const timer = setInterval(() => void refresh(), LIST_MS)
    return () => {
      alive.current = false
      clearInterval(timer)
    }
  }, [refresh])

  function params(p: 1 | 2 | 4 = workers): ComputeParams {
    return { a: Number(a), b: Number(b), n: Number(n), workers: p, method, mode }
  }

  async function guarded(work: () => Promise<void>) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await work()
    } catch (err) {
      setNote(null)
      setError(err)
    } finally {
      setBusy(false)
      void refresh()
    }
  }

  const onJob = (j: ComputeJob) => alive.current && setJob(j)

  function run(e: React.FormEvent) {
    e.preventDefault()
    void guarded(async () => {
      setCompare([])
      await runJob(params(), onJob, setNote)
    })
  }

  function runCompare() {
    void guarded(async () => {
      const results: ComputeResult[] = []
      setCompare([])
      for (const p of [1, 2, 4] as const) {
        setNote(`Comparing: ${p} process${p > 1 ? 'es' : ''}…`)
        results.push(await runJob(params(p), onJob, () => {}))
        if (alive.current) setCompare([...results])
      }
      setNote(null)
    })
  }

  return (
    <main id="content" className="page">
      <div className="page-head">
        <div>
          <h1>Compute</h1>
          <p>
            Car-hours of a working day: the integral of the occupancy O(t) = 16·Omax/T⁴ · t²(T − t)², T = 14, Omax =
            81, computed by a pool of processes of the DPC service.
          </p>
        </div>
      </div>

      <div className="columns">
        <div className="col-main">
          <form className="card section compute-form" onSubmit={run} noValidate>
            <div className="compute-grid">
              <label className="field">
                <span>a, hour</span>
                <input className="input mono" inputMode="decimal" value={a} onChange={(e) => setA(e.target.value)} />
              </label>
              <label className="field">
                <span>b, hour</span>
                <input className="input mono" inputMode="decimal" value={b} onChange={(e) => setB(e.target.value)} />
              </label>
              <label className="field">
                <span>n, subintervals</span>
                <input className="input mono" inputMode="numeric" value={n} onChange={(e) => setN(e.target.value)} />
              </label>
              <label className="field">
                <span>Method</span>
                <select className="input" value={method} onChange={(e) => setMethod(e.target.value as Method)}>
                  {METHODS.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
              </label>
              <fieldset className="field">
                <span>Processes</span>
                <div className="segmented" role="radiogroup">
                  {([1, 2, 4] as const).map((p) => (
                    <button
                      key={p}
                      type="button"
                      role="radio"
                      aria-checked={workers === p}
                      className={workers === p ? 'on' : ''}
                      onClick={() => setWorkers(p)}
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </fieldset>
              <label className="field">
                <span>Sum</span>
                <select className="input" value={mode} onChange={(e) => setMode(e.target.value as SumMode)}>
                  {MODES.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="row-actions">
              <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
                {busy && <Spinner size={16} color="#fff" />}
                Compute
              </button>
              <button type="button" className="btn" disabled={busy} onClick={runCompare}>
                Compare 1/2/4
              </button>
              {note && <span className="muted">{note}</span>}
            </div>
            {error != null && <ComputeProblem error={error} />}
          </form>

          {job && <ResultCard job={job} />}
          {compare.length > 0 && (
            <section className="card section">
              <CompareTable runs={compare} />
              {compare.length === 3 && (
                <p className="small muted">
                  <CheckCircle color="var(--success)" size={14} /> One run per p: repeat to see the spread.
                </p>
              )}
            </section>
          )}
        </div>

        <aside className="col-side">
          <section className="card section" aria-labelledby="history-title">
            <h2 id="history-title" className="eyebrow">
              Last computations
            </h2>
            {history.length === 0 ? (
              <p className="muted">No computations yet.</p>
            ) : (
              <table className="compute-table">
                <thead>
                  <tr>
                    <th>p</th>
                    <th>n</th>
                    <th>method / sum</th>
                    <th>time</th>
                    <th>rel. error</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((j) => (
                    <tr key={j.id} className={j.result && !j.result.within_tolerance ? 'bad' : ''}>
                      <td className="mono">{j.params.workers}</td>
                      <td className="mono">{j.params.n.toExponential(0)}</td>
                      <td>
                        {j.params.method.slice(0, 4)} / {j.params.mode}
                      </td>
                      <td className="mono">{j.result ? j.result.seconds.toFixed(2) : j.state}</td>
                      <td className="mono">{j.result ? sci(j.result.rel_error) : '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </aside>
      </div>
    </main>
  )
}
