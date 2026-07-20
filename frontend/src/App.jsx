import React, { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, setAuthToken, setOnUnauthorized } from './api'

export default function App() {
  // --- Auth (Google Sign-In, @nxtwave.co.in only) ---
  const [authCfg, setAuthCfg] = useState(null)   // {client_id, allowed_domain, configured, auth_disabled}
  const [user, setUser] = useState(null)         // {email, name, picture, is_admin}
  const [authErr, setAuthErr] = useState(null)

  const [status, setStatus] = useState(null)
  const [guide, setGuide] = useState('')
  const [showGuide, setShowGuide] = useState(false)

  const [courseLink, setCourseLink] = useState('')
  const [detailsLink, setDetailsLink] = useState('')
  const [refDate, setRefDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [courseType, setCourseType] = useState('semester')
  const [courseName, setCourseName] = useState('Computer Networks')
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

  // Self-evolution: durable rules the agent has learned from feedback + defects
  const [learned, setLearned] = useState(null)
  function refreshLearned() { api.learnedRules().then((d) => setLearned(d.rules || [])).catch(() => {}) }
  // Reload after a result appears or an eval run finishes — both can add rules.
  useEffect(() => { if (result) refreshLearned() }, [result, evalReport])

  // The user's own history (grouped by course) + their teams' shared docs.
  const [history, setHistory] = useState(null)
  const [teams, setTeams] = useState(null)
  function refreshMine() {
    api.myHistory().then(setHistory).catch(() => {})
    api.myTeams().then((d) => setTeams(d.teams || [])).catch(() => {})
  }
  useEffect(() => { if (user) refreshMine() }, [result, user])

  // Auth bootstrap: figure out whether login is required, and restore a session.
  useEffect(() => {
    setOnUnauthorized(() => { setAuthToken(''); setUser(null) })
    api.authConfig().then((cfg) => {
      setAuthCfg(cfg)
      if (cfg.auth_disabled) {
        setUser({ email: 'dev@local', name: 'Dev (auth off)', is_admin: true })
        return
      }
      const tok = localStorage.getItem('tr_auth_token')
      if (tok) {
        setAuthToken(tok)
        api.me().then(setUser).catch(() => setAuthToken(''))
      }
    }).catch(() => {})
  }, [])

  // Load status + saved settings once signed in.
  useEffect(() => {
    if (!user) return
    api.status().then((s) => {
      setStatus(s)
      if (s.saved_links?.course) setCourseLink(s.saved_links.course)
      if (s.saved_links?.details) setDetailsLink(s.saved_links.details)
      if (s.settings?.reference_date) setRefDate(s.settings.reference_date)
      if (s.settings?.course_type) setCourseType(s.settings.course_type)
      if (s.settings?.course_name) setCourseName(s.settings.course_name)
    }).catch(() => {})
    // Sessions appear ONLY after a successful Connect & Sync — never before.
  }, [user])

  function onSignIn(credential) {
    setAuthErr(null)
    setAuthToken(credential)
    api.login(credential)
      .then((u) => setUser(u))
      .catch((e) => { setAuthToken(''); setAuthErr(e.message || 'Sign-in failed.') })
  }
  function signOut() {
    setAuthToken(''); setUser(null); setStatus(null); setHistory(null); setTeams(null)
    if (window.google?.accounts?.id) window.google.accounts.id.disableAutoSelect()
  }

  // Create a Google Doc of the final TR doc in the SIGNED-IN user's own Drive
  // (they own it -> only they can edit). Uses a one-time Drive token via GIS.
  const [gdoc, setGdoc] = useState(null)          // { session_no, link }
  const [gdocBusy, setGdocBusy] = useState(false)
  function createGoogleDoc(session_no) {
    if (authCfg?.auth_disabled) {
      alert('Creating a Google Doc needs Google sign-in. Turn AUTH_DISABLED off and sign in with your @nxtwave.co.in account.')
      return
    }
    if (!authCfg?.client_id || !window.google?.accounts?.oauth2) {
      alert('Google library not ready — refresh the page and sign in, then try again.')
      return
    }
    setGdocBusy(true)
    try {
      const tc = window.google.accounts.oauth2.initTokenClient({
        client_id: authCfg.client_id,
        scope: 'https://www.googleapis.com/auth/drive.file',
        callback: (resp) => {
          if (!resp || !resp.access_token) { setGdocBusy(false); alert('Google Drive permission was not granted.'); return }
          api.createGdoc(session_no, resp.access_token)
            .then((d) => { setGdoc({ session_no, link: d.link }); if (d.link) window.open(d.link, '_blank', 'noopener') })
            .catch((e) => alert(e.message))
            .finally(() => setGdocBusy(false))
        },
      })
      tc.requestAccessToken()
    } catch (e) { setGdocBusy(false); alert('Could not start Google authorization: ' + e.message) }
  }

  async function loadGuide() {
    if (!guide) { const g = await api.templateGuide(); setGuide(g.markdown) }
    setShowGuide((v) => !v)
  }

  function doSync() {
    setSyncing(true); setSyncErr(null); setSyncOut(null); setSyncLogs([])
    api.sync(courseLink, detailsLink, refDate, courseType, courseName).then(({ job_id }) => {
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
    setGenerating(true); setResult(null); setGenErr(null); setLogs([]); setEvalReport(null); setEvalErr(null); setGdoc(null)
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

  // --- Auth gate: block the whole app until a valid @nxtwave.co.in login ---
  if (!authCfg) return <div className="app"><p className="sub">Loading…</p></div>
  if (!user) return <LoginGate cfg={authCfg} onSignIn={onSignIn} err={authErr} />

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
        <div className="userbox">
          {user.picture && <img className="avatar" src={user.picture} alt="" referrerPolicy="no-referrer" />}
          <span className="uemail">{user.email}{user.is_admin && <span className="pill admin">admin</span>}</span>
          <button className="link" onClick={signOut}>Sign out</button>
        </div>
      </header>

      <p className="sub">Generate a recording-ready Word TR doc for one session, in sync with your two Google Sheets.</p>

      <button className="link" onClick={loadGuide}>
        {showGuide ? '▲ Hide' : '▼ Show'} the required sheet template
      </button>
      {showGuide && <div className="card guide"><ReactMarkdown remarkPlugins={[remarkGfm]}>{guide}</ReactMarkdown></div>}

      {/* STEP 1 */}
      <section className="card">
        <h2><span className="num">1</span> Connect your sheets</h2>
        <div className="settingsrow">
          <div className="settingcol">
            <label>Course name</label>
            <input value={courseName} onChange={(e) => setCourseName(e.target.value)}
                   placeholder="e.g. Computer Networks" />
            <span className="hint">Groups your docs, history & team by course.</span>
          </div>
          <div className="settingcol">
            <label>Reference date (recency baseline)</label>
            <input type="date" value={refDate} onChange={(e) => setRefDate(e.target.value)} />
            <span className="hint">The agent treats info as current as of this date.</span>
          </div>
          <div className="settingcol">
            <label>Course type</label>
            <select value={courseType} onChange={(e) => setCourseType(e.target.value)}>
              <option value="semester">Semester — deep theoretical dive</option>
              <option value="interview">Interview-targeted</option>
            </select>
            <span className="hint">Both help clear interviews; semester goes deeper on theory.</span>
          </div>
        </div>
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
            {result.cost?.totals && (
              <Metric label="Cost" value={`$${(result.cost.totals.cost || 0).toFixed(4)}`}
                      sub={`${(result.cost.totals.total_tokens || 0).toLocaleString()} tok`} />
            )}
          </div>
          {!result.accepted && result.issues?.length > 0 && (
            <div className="alert warn">
              <b>Below one or more gates — best attempt shown:</b>
              <ul>{result.issues.map((i, k) => <li key={k}>{i}</li>)}</ul>
            </div>
          )}
          <div className="dlrow">
            <button className="primary download" onClick={() => api.downloadDoc(result.session_no).catch((e) => alert(e.message))}>⬇️ Download Word (.docx)</button>
            <button className="ghostbtn" disabled={gdocBusy} onClick={() => createGoogleDoc(result.session_no)}>
              {gdocBusy ? 'Creating Google Doc…' : '📄 Create Google Doc'}
            </button>
          </div>
          {gdoc?.session_no === result.session_no && gdoc.link && (
            <a className="gdoclink" href={gdoc.link} target="_blank" rel="noreferrer">
              🔗 Open in Google Docs — you have edit access
            </a>
          )}

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

          {result.cost?.calls?.length > 0 && <CostBreakdown cost={result.cost} />}

          {learned && <LearnedRules rules={learned} sessionNo={result.session_no} />}

          {result.markdown && (
            <details className="panel preview" open>
              <summary>Preview the TR doc</summary>
              <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{result.markdown}</ReactMarkdown></div>
            </details>
          )}
        </section>
      )}

      {/* MY HISTORY — everything this user has generated, grouped by course */}
      {history?.courses?.length > 0 && <MyHistory history={history} />}

      {/* MY TEAMS — docs the team is building together, per course */}
      {teams?.length > 0 && <MyTeams teams={teams} />}
    </div>
  )
}

function MyHistory({ history }) {
  const s = history.summary || {}
  return (
    <section className="card">
      <h2>📚 My TR Docs — History</h2>
      <div className="metrics">
        <Metric label="Docs generated" value={s.total_runs || 0} />
        <Metric label="Approved" value={s.approved_docs || 0} />
        <Metric label="Total cost" value={`$${(s.total_cost || 0).toFixed(4)}`} />
        <Metric label="Total tokens" value={(s.total_tokens || 0).toLocaleString()} />
      </div>
      {history.courses.map((c, i) => (
        <div key={i} className="coursegroup">
          <div className="coursehead">📗 {c.course}
            <span className="muted"> — {c.summary.total_runs} doc(s) · ${(c.summary.total_cost || 0).toFixed(4)}</span>
          </div>
          <RunTable runs={c.runs} />
        </div>
      ))}
    </section>
  )
}

function MyTeams({ teams }) {
  return (
    <section className="card">
      <h2>👥 My Teams</h2>
      {teams.map((t, i) => (
        <div key={i} className="coursegroup">
          <div className="coursehead">🧩 {t.team.name}
            <span className="muted"> — {t.team.course || 'no course'} · {t.members.length} member(s): {t.members.join(', ')}</span>
          </div>
          {t.courses.length === 0
            ? <div className="just" style={{ padding: '4px 2px' }}>No docs built by the team yet.</div>
            : t.courses.map((c, j) => <RunTable key={j} runs={c.runs} />)}
        </div>
      ))}
    </section>
  )
}

function RunTable({ runs }) {
  const [open, setOpen] = useState(null)
  return (
    <div className="scorelist">
      <div className="setrow dashhead">
        <div className="setmain">
          <span className="dashcell grow">Session · by</span>
          <span className="dashcell">Status</span>
          <span className="dashcell">Rubric</span>
          <span className="dashcell">Cost</span>
          <span className="dashcell">Output</span>
        </div>
      </div>
      {runs.map((r, i) => {
        const isOpen = open === i
        const done = r.status === 'done'
        return (
          <div key={i} className="setrow dashrow">
            <div className="setmain">
              <span className="dashcell grow dashclick" onClick={() => setOpen(isOpen ? null : i)}>
                <span className="tw">{isOpen ? '▾' : '▸'}</span> S{r.session_no}: {r.title}
                {r.enforce_time === false && <span className="tag" style={{ marginLeft: 6 }}>depth</span>}
                <span className="uref"> · {r.user_email || 'unknown'}</span>
              </span>
              <span className="dashcell">
                {r.status === 'running' ? <span className="chip mid">● {r.stage || 'running'}</span>
                  : r.status === 'error' ? <span className="chip bad">error</span>
                  : r.accepted ? <span className="chip good">✓</span> : <span className="chip bad">review</span>}
              </span>
              <span className="dashcell">{r.rubric != null ? `${r.rubric}` : '—'}</span>
              <span className="dashcell">${((r.cost || {}).cost || 0).toFixed(4)}</span>
              <span className="dashcell">
                {done ? <a href="#" onClick={(e) => { e.preventDefault(); api.downloadDoc(r.session_no).catch((err) => alert(err.message)) }}>⬇️ .docx</a> : '—'}
              </span>
            </div>
            {isOpen && <CostBreakdown cost={{ totals: r.cost, calls: r.calls }} embedded ts={r.ts} rounds={r.rounds} />}
          </div>
        )
      })}
    </div>
  )
}

function LoginGate({ cfg, onSignIn, err }) {
  const btnRef = useRef(null)
  const [scriptReady, setScriptReady] = useState(!!window.google?.accounts?.id)

  // Load the Google Identity Services script once.
  useEffect(() => {
    if (window.google?.accounts?.id) { setScriptReady(true); return }
    const existing = document.getElementById('gsi-script')
    if (existing) { existing.addEventListener('load', () => setScriptReady(true)); return }
    const s = document.createElement('script')
    s.src = 'https://accounts.google.com/gsi/client'
    s.async = true; s.defer = true; s.id = 'gsi-script'
    s.onload = () => setScriptReady(true)
    document.head.appendChild(s)
  }, [])

  // Initialise + render the Google button once the script and client id are ready.
  useEffect(() => {
    if (!scriptReady || !cfg?.client_id || !btnRef.current) return
    try {
      window.google.accounts.id.initialize({
        client_id: cfg.client_id,
        callback: (resp) => onSignIn(resp.credential),
        hd: cfg.allowed_domain,          // hint Google to prefer the org domain
        auto_select: false,
      })
      window.google.accounts.id.renderButton(btnRef.current,
        { theme: 'filled_blue', size: 'large', text: 'signin_with', shape: 'pill' })
    } catch (e) { /* GIS not ready yet */ }
  }, [scriptReady, cfg, onSignIn])

  return (
    <div className="app logingate">
      <div className="card loginbox">
        <div className="brand big">📝 <b>TR Doc Generator</b></div>
        <p className="sub">Sign in to continue.</p>
        {!cfg.configured ? (
          <div className="alert warn">
            <b>Google Sign-In isn’t configured yet.</b>
            <p>Set <code>GOOGLE_CLIENT_ID</code> in <code>.env</code> (OAuth client for
            the <code>{cfg.allowed_domain}</code> workspace), then restart the backend.
            For local dev only, you can set <code>AUTH_DISABLED=1</code> to bypass login.</p>
          </div>
        ) : (
          <>
            <div ref={btnRef} className="gsi-btn" />
            <p className="hint">Only <b>@{cfg.allowed_domain}</b> Google accounts are allowed.</p>
          </>
        )}
        {err && <div className="alert error"><pre>{err}</pre></div>}
      </div>
    </div>
  )
}

function CostBreakdown({ cost, embedded, ts, rounds }) {
  const calls = cost.calls || []
  const t = cost.totals || {}
  const body = (
    <div className="scorelist">
      {ts && <div className="just" style={{ padding: '2px 2px 6px' }}>Generated {ts.replace('T', ' ')}{rounds ? ` · ${rounds} round(s)` : ''}</div>}
      <div className="setrow dashhead">
        <div className="setmain">
          <span className="dashcell grow">Call</span>
          <span className="dashcell">Model</span>
          <span className="dashcell">In</span>
          <span className="dashcell">Out</span>
          <span className="dashcell">Total</span>
          <span className="dashcell">Cost</span>
        </div>
      </div>
      {calls.map((c, i) => (
        <div key={i} className="setrow">
          <div className="setmain">
            <span className="dashcell grow"><span className="tag">{c.label || 'call'}</span></span>
            <span className="dashcell mono">{(c.model || '').replace('anthropic/', '')}</span>
            <span className="dashcell">{(c.prompt_tokens || 0).toLocaleString()}</span>
            <span className="dashcell">{(c.completion_tokens || 0).toLocaleString()}</span>
            <span className="dashcell">{(c.total_tokens || 0).toLocaleString()}</span>
            <span className="dashcell">${(c.cost || 0).toFixed(4)}</span>
          </div>
        </div>
      ))}
      <div className="setrow dashtotal">
        <div className="setmain">
          <span className="dashcell grow"><b>Total</b></span>
          <span className="dashcell" />
          <span className="dashcell">{(t.prompt_tokens || 0).toLocaleString()}</span>
          <span className="dashcell">{(t.completion_tokens || 0).toLocaleString()}</span>
          <span className="dashcell">{(t.total_tokens || 0).toLocaleString()}</span>
          <span className="dashcell"><b>${(t.cost || 0).toFixed(4)}</b></span>
        </div>
      </div>
    </div>
  )
  if (embedded) return <div className="costembed">{body}</div>
  return (
    <details className="panel">
      <summary>💰 Cost breakdown
        <span className="muted"> — ${(t.cost || 0).toFixed(4)} · {(t.total_tokens || 0).toLocaleString()} tokens · {calls.length} call(s)</span>
      </summary>
      {body}
    </details>
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

function LearnedRules({ rules, sessionNo }) {
  const newCount = rules.filter((r) => r.session_no === sessionNo).length
  const srcLabel = { regeneration: 'human', judge: 'auto · judge', eval_set: 'auto · eval', feedback: 'human' }
  return (
    <details className="panel learned" open={newCount > 0}>
      <summary>
        🧠 What the agent has learned
        <span className="muted"> — {rules.length} rule{rules.length === 1 ? '' : 's'} applied to every future generation</span>
        {newCount > 0 && <span className="chip good" style={{ marginLeft: 8 }}>+{newCount} this run</span>}
      </summary>
      {rules.length === 0 ? (
        <div className="just" style={{ padding: '6px 2px' }}>
          Nothing learned yet. As generations and eval runs surface defects, durable rules appear here and are injected into later sessions automatically.
        </div>
      ) : (
        <div className="scorelist">
          {rules.map((r, i) => (
            <div key={i} className={`setrow ${r.session_no === sessionNo ? 'pass' : ''}`}>
              <div className="setmain">
                <span className="tag">{srcLabel[r.source] || r.source || 'rule'}</span>
                <span className="dimname">{r.text}</span>
                {r.session_no === sessionNo && <span className="chip good">new</span>}
              </div>
              {r.session_no != null && <div className="just">learned at Session {r.session_no}</div>}
            </div>
          ))}
        </div>
      )}
    </details>
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
