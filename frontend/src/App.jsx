import React, { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from './api'

export default function App() {
  const [status, setStatus] = useState(null)
  const [guide, setGuide] = useState('')
  const [showGuide, setShowGuide] = useState(false)

  const [courseLink, setCourseLink] = useState('')
  const [detailsLink, setDetailsLink] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [syncOut, setSyncOut] = useState(null)
  const [syncErr, setSyncErr] = useState(null)
  const [syncLogs, setSyncLogs] = useState([])
  const syncPollRef = useRef(null)

  const [sessions, setSessions] = useState([])
  const [sel, setSel] = useState(null)
  const [useJudge, setUseJudge] = useState(true)
  const [enforceTime, setEnforceTime] = useState(true)

  const [logs, setLogs] = useState([])
  const [generating, setGenerating] = useState(false)
  const [result, setResult] = useState(null)
  const [genErr, setGenErr] = useState(null)
  const pollRef = useRef(null)

  // Guided mode: generate all chunks -> review each -> finalize
  const [mode, setMode] = useState('oneshot')
  const [guidedId, setGuidedId] = useState(null)
  const [guided, setGuided] = useState(null)
  const [regenReason, setRegenReason] = useState('')
  const [regenFor, setRegenFor] = useState(null)
  const [busyAction, setBusyAction] = useState(false)
  const [approved, setApproved] = useState({})
  const guidedPollRef = useRef(null)

  // Eval-sets (System B) run on the finished doc
  const [evalReport, setEvalReport] = useState(null)
  const [evalRunning, setEvalRunning] = useState(false)
  const [evalErr, setEvalErr] = useState(null)
  const evalPollRef = useRef(null)

  useEffect(() => {
    api.status().then((s) => {
      setStatus(s)
      if (s.saved_links?.course) setCourseLink(s.saved_links.course)
      if (s.saved_links?.details) setDetailsLink(s.saved_links.details)
    })
    // Sessions appear ONLY after a successful Connect & Sync — never before.
  }, [])

  async function loadGuide() {
    if (!guide) { const g = await api.templateGuide(); setGuide(g.markdown) }
    setShowGuide((v) => !v)
  }

  function doSync() {
    setSyncing(true); setSyncErr(null); setSyncOut(null); setSyncLogs([])
    api.sync(courseLink, detailsLink).then(({ job_id }) => {
      syncPollRef.current = setInterval(async () => {
        try {
          const job = await api.job(job_id)
          setSyncLogs(job.logs || [])
          if (job.status === 'done') {
            clearInterval(syncPollRef.current); setSyncing(false)
            const out = job.result
            setSyncOut(out); setSessions(out.sessions || [])
            if (out.sessions?.length) setSel(out.sessions[0].number)
          } else if (job.status === 'error') {
            clearInterval(syncPollRef.current); setSyncing(false)
            setSyncErr({ kind: job.error_kind, message: job.error })
          }
        } catch (e) {
          clearInterval(syncPollRef.current); setSyncing(false)
          setSyncErr({ kind: e.kind, message: e.message })
        }
      }, 1000)
    }).catch((e) => { setSyncing(false); setSyncErr({ kind: e.kind, message: e.message }) })
  }

  function startGenerate() {
    setGenerating(true); setResult(null); setGenErr(null); setLogs([]); setEvalReport(null); setEvalErr(null)
    api.generate(sel, useJudge, enforceTime).then(({ job_id }) => {
      pollRef.current = setInterval(async () => {
        try {
          const job = await api.job(job_id)
          setLogs(job.logs || [])
          if (job.status === 'done') {
            clearInterval(pollRef.current); setGenerating(false); setResult(job.result)
          } else if (job.status === 'error') {
            clearInterval(pollRef.current); setGenerating(false); setGenErr(job.error)
          }
        } catch (e) { clearInterval(pollRef.current); setGenerating(false); setGenErr(e.message) }
      }, 1500)
    }).catch((e) => { setGenerating(false); setGenErr(e.message) })
  }

  function startGuided() {
    setResult(null); setGenErr(null); setGuided(null); setRegenFor(null); setRegenReason(''); setApproved({}); setEvalReport(null); setEvalErr(null)
    api.guidedStart(sel, useJudge).then(({ guided_id }) => {
      setGuidedId(guided_id)
      guidedPollRef.current = setInterval(async () => {
        try {
          const st = await api.guidedState(guided_id)
          setGuided(st)
          if (st.status === 'done') { clearInterval(guidedPollRef.current); setResult(st.result) }
          else if (st.status === 'error') { clearInterval(guidedPollRef.current); setGenErr(st.error) }
        } catch (e) { clearInterval(guidedPollRef.current); setGenErr(e.message) }
      }, 1500)
    }).catch((e) => setGenErr(e.message))
  }

  function approveChunk(i) { setApproved((a) => ({ ...a, [i]: true })) }

  function regenerateChunk(index) {
    const reason = regenReason.trim()
    if (!reason) return
    setBusyAction(true)
    api.guidedRegenerate(guidedId, index, reason).then(() => {
      setRegenFor(null); setRegenReason(''); setBusyAction(false)
      setApproved((a) => { const c = { ...a }; delete c[index]; return c })
    }).catch((e) => { setBusyAction(false); setGenErr(e.message) })
  }

  function finalizeGuided() {
    setBusyAction(true)
    api.guidedFinalize(guidedId).then(() => setBusyAction(false))
      .catch((e) => { setBusyAction(false); setGenErr(e.message) })
  }

  function runEvalSets() {
    setEvalRunning(true); setEvalReport(null); setEvalErr(null)
    api.evalSets(result.session_no, true, enforceTime).then(({ job_id }) => {
      evalPollRef.current = setInterval(async () => {
        try {
          const job = await api.job(job_id)
          if (job.status === 'done') { clearInterval(evalPollRef.current); setEvalRunning(false); setEvalReport(job.result) }
          else if (job.status === 'error') { clearInterval(evalPollRef.current); setEvalRunning(false); setEvalErr(job.error) }
        } catch (e) { clearInterval(evalPollRef.current); setEvalRunning(false); setEvalErr(e.message) }
      }, 1500)
    }).catch((e) => { setEvalRunning(false); setEvalErr(e.message) })
  }

  useEffect(() => {
    guidedPollRef.current && clearInterval(guidedPollRef.current)
    setGuidedId(null); setGuided(null); setRegenFor(null); setRegenReason(''); setApproved({})
  }, [sel])

  useEffect(() => () => {
    pollRef.current && clearInterval(pollRef.current)
    syncPollRef.current && clearInterval(syncPollRef.current)
    guidedPollRef.current && clearInterval(guidedPollRef.current)
    evalPollRef.current && clearInterval(evalPollRef.current)
  }, [])

  const selSession = sessions.find((s) => s.number === sel)
  const gStatus = guided?.status
  const guidedGenAll = gStatus === 'generating_all'
  const guidedReviewing = gStatus === 'reviewing' || gStatus === 'regenerating'
  const guidedAssembling = gStatus === 'assembling'
  const guidedActive = guided && gStatus !== 'done' && gStatus !== 'error'
  const allApproved = guided?.chunks?.length > 0 && guided.chunks.every((_, i) => approved[i])

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">📝 <b>TR Doc Generator</b></div>
        {status && (
          <div className="status">
            <span className="pill">{status.provider}</span>
            <span className="pill">{(status.model || '').split('/').pop()}</span>
            <span className={`pill ${status.key_ok ? 'ok' : 'bad'}`}>
              {status.key_ok ? 'API key ✓' : 'API key ✗'}
            </span>
            <span className="pill ghost">v{status.version}</span>
          </div>
        )}
      </header>

      <p className="sub">Generate a recording-ready Word TR doc for one session, in sync with your two Google Sheets.</p>

      <button className="link" onClick={loadGuide}>
        {showGuide ? '▲ Hide' : '▼ Show'} the required sheet template
      </button>
      {showGuide && <div className="card guide"><ReactMarkdown remarkPlugins={[remarkGfm]}>{guide}</ReactMarkdown></div>}

      {/* STEP 1 */}
      <section className="card">
        <h2><span className="num">1</span> Connect your sheets</h2>
        <label>Course Curriculum Structure — Google Sheet link</label>
        <input value={courseLink} onChange={(e) => setCourseLink(e.target.value)}
               placeholder="https://docs.google.com/spreadsheets/d/.../edit" />
        <label>Session Details (past decks) — Google Sheet link</label>
        <input value={detailsLink} onChange={(e) => setDetailsLink(e.target.value)}
               placeholder="https://docs.google.com/spreadsheets/d/.../edit" />
        <button className="primary" disabled={!courseLink || !detailsLink || syncing} onClick={doSync}>
          {syncing ? 'Syncing…' : '🔄 Connect & Sync'}
        </button>

        {(syncing || syncLogs.length > 0) && (
          <>
            {syncing && <Busy label="Syncing sheets…" />}
            <pre className="logs">{syncLogs.join('\n') || 'Starting…'}</pre>
          </>
        )}

        {syncErr && (
          <div className={`alert ${syncErr.kind === 'template' ? 'warn' : 'error'}`}>
            <b>{syncErr.kind === 'template' ? 'Template check failed — sheet discarded' : 'Could not read the sheet'}</b>
            <pre>{syncErr.message}</pre>
          </div>
        )}
        {syncOut && (
          <div className="synced">
            <div className="metrics">
              <Metric label="Sessions" value={syncOut.counts.sessions} />
              <Metric label="Decks ingested" value={syncOut.counts.ingested} />
              <Metric label="Decks cached" value={syncOut.counts.cached} />
            </div>
            {syncOut.changelog?.length > 0 ? (
              <div className="changelog">
                <b>Changes this sync</b>
                <ul>{syncOut.changelog.map((c, i) => <li key={i}>{c}</li>)}</ul>
              </div>
            ) : <div className="ok-note">In sync — no changes since last time.</div>}
            {syncOut.errors?.map((e, i) => <div key={i} className="alert warn"><pre>{e}</pre></div>)}
            {syncOut.extraction_warnings?.length > 0 && (
              <div className="alert warn">
                <b>⚠ Deck extraction gaps (some slide content may be missing):</b>
                <ul>{syncOut.extraction_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </div>
            )}
          </div>
        )}
      </section>

      {/* STEP 2 — only after a successful sync */}
      {syncOut && sessions.length > 0 && (
        <section className="card">
          <h2><span className="num">2</span> Generate a TR doc</h2>
          <label>Session</label>
          <select value={sel ?? ''} onChange={(e) => setSel(Number(e.target.value))}>
            {sessions.map((s) => <option key={s.number} value={s.number}>{s.number} — {s.name}</option>)}
          </select>
          {selSession && (
            <details className="takeaways">
              <summary>Key takeaways ({selSession.takeaways.length})</summary>
              <ul>{selSession.takeaways.map((k, i) => <li key={i}>{k}</li>)}</ul>
            </details>
          )}
          <label className="check">
            <input type="checkbox" checked={useJudge} onChange={(e) => setUseJudge(e.target.checked)} />
            Run the LLM quality judge (rubric /100)
          </label>
          <label className="check">
            <input type="checkbox" checked={enforceTime} onChange={(e) => setEnforceTime(e.target.checked)} />
            Keep within the 40-minute recording limit
          </label>

          <div className="mode">
            <label className={`modeopt ${mode === 'oneshot' ? 'on' : ''}`}>
              <input type="radio" name="mode" checked={mode === 'oneshot'}
                     disabled={generating || guidedActive} onChange={() => setMode('oneshot')} />
              One-shot <span className="msub">whole doc, ~2–4 min</span>
            </label>
            <label className={`modeopt ${mode === 'guided' ? 'on' : ''}`}>
              <input type="radio" name="mode" checked={mode === 'guided'}
                     disabled={generating || guidedActive} onChange={() => setMode('guided')} />
              Guided <span className="msub">generate all, review, then finalize</span>
            </label>
          </div>

          {mode === 'oneshot' && (
            <>
              <button className="primary" disabled={generating || sel == null || !status?.key_ok} onClick={startGenerate}>
                {generating ? 'Generating…' : '✨ Generate TR Doc'}
              </button>
              <div className="hint">
                The model drafts, grades, and (if needed) revises the whole doc — ~<b>2–4 min</b>.
                {enforceTime ? ' Forced to fit the 40-minute budget.' : ' 40-minute limit is OFF.'}
              </div>
              {(generating || logs.length > 0) && (
                <>
                  {generating && <Busy label="Generating… (drafts → grades → revises)" />}
                  <pre className="logs">{logs.join('\n') || 'Starting…'}</pre>
                </>
              )}
            </>
          )}

          {mode === 'guided' && (
            <>
              {!guidedId && (
                <button className="primary" disabled={sel == null || !status?.key_ok} onClick={startGuided}>
                  🚦 Generate all chunks
                </button>
              )}
              <div className="hint">
                Generates <b>every chunk first</b> (one per key takeaway), then you
                <b> review each</b>, <b>approve</b> it or <b>regenerate</b> with a reason
                (that reason also teaches the agent for future sessions). All chunks must be
                approved before <b>Create final TR Doc</b>.
              </div>

              {guidedGenAll && (
                <div className="guided">
                  <div className="gprogress">Generating chunk <b>{Math.min(guided.index + 1, guided.total)}</b> of {guided.total}</div>
                  <Busy label="Generating all chunks… (you'll review them next)" />
                  <pre className="logs">{(guided.logs || []).join('\n') || 'Working…'}</pre>
                </div>
              )}

              {guided?.chunks?.length > 0 && (guidedReviewing || guidedAssembling || gStatus === 'done') && (
                <div className="guided">
                  {guidedReviewing && (
                    <div className="gprogress">
                      Review each chunk — <b>Approve</b> or <b>Regenerate</b>.
                      <span className="gcount"> · {guided.chunks.filter((_, i) => approved[i]).length}/{guided.chunks.length} approved</span>
                    </div>
                  )}
                  {gStatus === 'done' && (
                    <div className="ok-note">✅ Final doc created — see the result below. Chunks kept here for reference.</div>
                  )}
                  {guided.chunks.map((c, i) => {
                    const regenning = gStatus === 'regenerating' && guided.regen_index === i
                    const isOk = !!approved[i]
                    return (
                      <details key={i} className={`review-chunk ${isOk ? 'ok' : ''}`} open={gStatus !== 'done'}>
                        <summary>{isOk ? '✅' : `${i + 1}.`} {c.label}</summary>
                        {regenning
                          ? <Busy label="Regenerating this chunk…" />
                          : <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{c.markdown}</ReactMarkdown></div>}
                        {guidedReviewing && !regenning && (
                          <div className="chunk-actions">
                            <div className="gactions">
                              {isOk
                                ? <span className="approved-badge">✓ Approved</span>
                                : <button className="primary" disabled={busyAction} onClick={() => approveChunk(i)}>✅ Approve</button>}
                              {regenFor !== i && (
                                <button className="ghostbtn" disabled={busyAction || gStatus === 'regenerating'}
                                        onClick={() => { setRegenFor(i); setRegenReason('') }}>🔄 Regenerate…</button>
                              )}
                            </div>
                            {regenFor === i && (
                              <div className="regen">
                                <label>Why regenerate? <span className="req">(required — instructs the model & is remembered)</span></label>
                                <textarea rows={3} value={regenReason} onChange={(e) => setRegenReason(e.target.value)}
                                          placeholder="e.g. Make the analogy concrete, and shorten this to ~9 minutes." />
                                <div className="gactions">
                                  <button className="primary" disabled={busyAction || !regenReason.trim()} onClick={() => regenerateChunk(i)}>Regenerate</button>
                                  <button className="ghostbtn" disabled={busyAction} onClick={() => { setRegenFor(null); setRegenReason('') }}>Cancel</button>
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </details>
                    )
                  })}
                  {guidedReviewing && (
                    <>
                      <button className="primary bigfinal" disabled={busyAction || gStatus === 'regenerating' || !allApproved} onClick={finalizeGuided}>
                        📝 Create final TR Doc
                      </button>
                      {!allApproved && <div className="hint">Approve every chunk to enable creating the final doc.</div>}
                    </>
                  )}
                  {guidedAssembling && <Busy label="Assembling & grading the full doc…" />}
                </div>
              )}
            </>
          )}

          {genErr && <div className="alert error"><pre>{genErr}</pre></div>}
        </section>
      )}

      {/* STEP 3 */}
      {result && (
        <section className="card">
          <h2><span className="num">3</span> Result</h2>
          <div className="metrics">
            <Metric label="Accepted" value={result.accepted ? '✅ Yes' : '⚠️ Review'} />
            <Metric label="Est. recording" value={`${result.time.estimated_minutes} min`}
                    sub={enforceTime ? `budget ${result.time.max_minutes}` : 'limit off'} />
            <Metric label="Slides" value={result.time.slide_count} />
            {result.judge && <Metric label="Rubric" value={`${result.judge.weighted_total}/100`} />}
          </div>
          {!result.accepted && result.issues?.length > 0 && (
            <div className="alert warn">
              <b>Below one or more gates — best attempt shown:</b>
              <ul>{result.issues.map((i, k) => <li key={k}>{i}</li>)}</ul>
            </div>
          )}
          <a className="primary download" href={api.downloadUrl(result.session_no)}>⬇️ Download Word (.docx)</a>

          {result.judge?.scores && (
            <details className="panel rubric" open>
              <summary>Rubric — judge score <b>{result.judge.weighted_total}/100</b>
                <span className="muted"> ({Object.keys(result.judge.scores).length} dimensions)</span>
              </summary>
              <div className="scorelist">
                {Object.entries(result.judge.scores).map(([dim, o]) => (
                  <div key={dim} className="scorerow">
                    <div className="scorehead"><ScoreChip score={o.score} /><span className="dimname">{pretty(dim)}</span></div>
                    <div className="just">{o.justification}</div>
                  </div>
                ))}
              </div>
            </details>
          )}

          <div className="panel evalsets">
            <div className="evalhead">
              <div><b>Eval sets</b> <span className="muted">— score this doc against all {19} quality dimensions</span></div>
              <button className="ghostbtn" disabled={evalRunning} onClick={runEvalSets}>
                {evalRunning ? 'Running…' : '🧪 Run eval sets'}
              </button>
            </div>
            {evalRunning && <Busy label="Scoring against the eval sets… (deterministic + LLM, ~1–2 min)" />}
            {evalErr && <div className="alert error"><pre>{evalErr}</pre></div>}
            {evalReport && <EvalReport report={evalReport} />}
          </div>

          {result.markdown && (
            <details className="panel preview" open>
              <summary>Preview the TR doc</summary>
              <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{result.markdown}</ReactMarkdown></div>
            </details>
          )}
        </section>
      )}
    </div>
  )
}

function Busy({ label }) {
  return <div className="busyrow"><span className="spinner" /> {label}</div>
}

function pretty(id) {
  return id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function ScoreChip({ score, max = 5 }) {
  const cls = score >= 4 ? 'good' : score >= 3 ? 'mid' : 'bad'
  return <span className={`chip ${cls}`}>{score}/{max}</span>
}

function EvalReport({ report }) {
  return (
    <div className="evalreport">
      <div className="evalsummary">
        <span className={`badge ${report.overall_pass ? 'good' : 'bad'}`}>
          {report.overall_pass ? 'PASS' : 'REVIEW'}
        </span>
        <span><b>{report.passed}</b>/{report.scored} sets passed</span>
        <span className="muted">· {report.skipped} skipped</span>
      </div>
      {report.sets.map((s) => (
        <div key={s.id} className={`setrow ${s.skipped ? 'skip' : (s.passed ? 'pass' : 'fail')}`}>
          <div className="setmain">
            {s.skipped ? <span className="chip skip">skip</span> : <ScoreChip score={s.score} />}
            <span className="dimname">{pretty(s.id)}</span>
            {!s.skipped && <span className="tag">{s.grader}</span>}
          </div>
          <div className="just">{s.skipped ? s.reason : s.detail}</div>
        </div>
      ))}
    </div>
  )
}

function Metric({ label, value, sub }) {
  return (
    <div className="metric">
      <div className="mv">{value}</div>
      <div className="ml">{label}{sub && <span className="ms"> · {sub}</span>}</div>
    </div>
  )
}
