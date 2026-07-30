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
  // The id of a guided run this browser started but never finished. The server
  // checkpoints guided runs, so one left behind by a reload or a server restart can
  // be resumed instead of stranding chunks that cost an LLM call each. Read once at
  // mount (before any state reset can clear it) and offered as an explicit Resume.
  const [resumableGid, setResumableGid] = useState(() => localStorage.getItem('tr_guided_id') || null)
  function rememberGuided(gid) {
    if (gid) localStorage.setItem('tr_guided_id', gid)
    else localStorage.removeItem('tr_guided_id')
    setResumableGid(gid)
  }
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
  const [learnedCourse, setLearnedCourse] = useState('')
  function refreshLearned() {
    api.learnedRules()
      .then((d) => { setLearned(d.rules || []); setLearnedCourse(d.course || '') })
      .catch(() => {})
  }
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

  // The sheet templates live in a side panel, not inline: they are reference material
  // you keep open while filling the two link fields in Step 1, and as an inline
  // collapsible they pushed the whole form down the page every time you opened them.
  async function loadGuide() {
    if (!guide) {
      try { const g = await api.templateGuide(); setGuide(g.markdown) }
      catch (e) { setGuide(`Could not load the template guide: ${e.message}`) }
    }
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

  // Side panel showing the cost of the CURRENT generation only (dismissable).
  const [showCost, setShowCost] = useState(true)

  function startGenerate() {
    setGenerating(true); setResult(null); setGenErr(null); setLogs([]); setEvalReport(null); setEvalErr(null); setGdoc(null); setShowCost(true)
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

  // Poll guided state ONLY while it's in a transient state (generating_all,
  // regenerating, assembling). We STOP as soon as it reaches a stable state:
  // 'reviewing' waits for the user, so continuing to poll would re-render the
  // chunk list every 1.5s and make the text flicker/blink while you read it.
  // Polling is resumed explicitly when the user regenerates or finalizes.
  // The server restores an interrupted guided run from its checkpoint, so a
  // 'guided_gone' error means the run is truly unrecoverable. Clear the dead run
  // instead of leaving a stuck screen with a red box, so 'Generate all chunks'
  // comes back and the user can start over.
  function handleGuidedError(e) {
    clearInterval(guidedPollRef.current)
    setGenErr(e.message)
    if (e.kind === 'guided_gone') {
      setGuidedId(null); setGuided(null); setApproved({}); setRegenFor(null); setRegenReason('')
      rememberGuided(null)      // nothing left to resume
    }
  }

  function pollGuided(gid) {
    clearInterval(guidedPollRef.current)
    const tick = async () => {
      try {
        const st = await api.guidedState(gid)
        setGuided(st)
        if (st.status === 'reviewing') { clearInterval(guidedPollRef.current) }
        else if (st.status === 'done') {
          clearInterval(guidedPollRef.current); setResult(st.result); rememberGuided(null)
        }
        else if (st.status === 'error') { clearInterval(guidedPollRef.current); setGenErr(st.error) }
      } catch (e) { handleGuidedError(e) }
    }
    guidedPollRef.current = setInterval(tick, 1500)
    tick()   // fetch immediately so the UI reacts without a 1.5s lag
  }

  function startGuided() {
    setResult(null); setGenErr(null); setGuided(null); setRegenFor(null); setRegenReason(''); setApproved({}); setEvalReport(null); setEvalErr(null); setShowCost(true)
    api.guidedStart(sel, useJudge, enforceTime).then(({ guided_id }) => {
      setGuidedId(guided_id)
      rememberGuided(guided_id)
      pollGuided(guided_id)
    }).catch((e) => setGenErr(e.message))
  }

  // Pick an unfinished guided run back up (after a reload, or after the server was
  // restarted/spun down mid-review). The chunks come back from the server's
  // checkpoint, so nothing already generated has to be paid for again.
  function resumeGuided() {
    const gid = resumableGid
    if (!gid) return
    setGenErr(null); setResult(null); setEvalReport(null); setEvalErr(null)
    setBusyAction(true)
    api.guidedState(gid).then((st) => {
      setBusyAction(false)
      if (st.status === 'done' || st.status === 'error') {
        rememberGuided(null)
        setGenErr(st.status === 'error'
          ? `That guided run had already failed: ${st.error || 'unknown error'}`
          : 'That guided run had already finished — nothing left to resume.')
        return
      }
      setMode('guided'); setGuidedId(gid); setGuided(st); setApproved({}); setShowCost(true)
      if (st.status !== 'reviewing') pollGuided(gid)
    }).catch((e) => { setBusyAction(false); handleGuidedError(e) })
  }

  function approveChunk(i) { setApproved((a) => ({ ...a, [i]: true })) }

  // busyAction disables EVERY button in the review panel (approve, regenerate,
  // create-final), so it must never be left stuck on: a request that neither
  // resolves nor rejects would make the whole panel look unclickable. Clearing it in
  // finally() guarantees release on every path.
  function regenerateChunk(index) {
    const reason = regenReason.trim()
    if (!reason || !guidedId) return
    setGenErr(null)          // don't leave a previous attempt's error on screen
    setBusyAction(true)
    api.guidedRegenerate(guidedId, index, reason).then(() => {
      setRegenFor(null); setRegenReason('')
      setApproved((a) => { const c = { ...a }; delete c[index]; return c })
      pollGuided(guidedId)   // resume polling to watch regenerating -> reviewing
    }).catch(handleGuidedError).finally(() => setBusyAction(false))
  }

  function finalizeGuided() {
    if (!guidedId) return
    setGenErr(null)
    setBusyAction(true)
    api.guidedFinalize(guidedId).then(() => pollGuided(guidedId))  // watch assembling -> done
      .catch(handleGuidedError).finally(() => setBusyAction(false))
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

      {/* Cost of the TR doc being generated right now — sticky side panel
          (falls back to a normal block on narrow screens). */}
      {showCost && (generating || guidedActive || result) && (
        <CostSidePanel
          cost={result?.cost}
          sessionNo={result?.session_no ?? sel}
          pending={!result && (generating || guidedActive)}
          onClose={() => setShowCost(false)}
        />
      )}

      <button className="link" onClick={loadGuide}>
        📋 {showGuide ? 'Hide' : 'Show'} the required sheet templates
      </button>
      {showGuide && <TemplateSidePanel markdown={guide} onClose={() => setShowGuide(false)} />}

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
              {!guidedId && resumableGid && (
                <div className="hint">
                  You have an <b>unfinished guided run</b>. Its chunks are saved on the
                  server, so you can carry on where you left off.{' '}
                  <button className="ghostbtn" disabled={busyAction} onClick={resumeGuided}>
                    ↩ Resume it
                  </button>{' '}
                  <button className="ghostbtn" onClick={() => rememberGuided(null)}>
                    Discard
                  </button>
                </div>
              )}
              <div className="hint">
                Generates <b>every chunk first</b> (one per key takeaway), then you
                <b> review each</b>, <b>approve</b> it or <b>regenerate</b> with a reason
                (that reason also teaches the agent for future sessions). All chunks must be
                approved before <b>Create final TR Doc</b>.
                {enforceTime ? ' Forced to fit the 40-minute budget.' : ' 40-minute limit is OFF.'}
              </div>

              {guidedGenAll && (
                <div className="guided">
                  <div className="gprogress">Generating chunk <b>{Math.min(guided.index + 1, guided.total)}</b> of {guided.total}</div>
                  <Busy label="Generating all chunks… (you'll review them next)" />
                  <pre className="logs">{(guided.logs || []).join('\n') || 'Working…'}</pre>
                </div>
              )}

              {/* gStatus === 'error' is included so a run that died during
                  generate-all still SHOWS the chunks it produced (each one cost an
                  LLM call) instead of hiding them behind a bare red box. */}
              {guided?.chunks?.length > 0 && (guidedReviewing || guidedAssembling || gStatus === 'done' || gStatus === 'error') && (
                <div className="guided">
                  {guidedReviewing && (
                    <div className="gprogress">
                      Review each chunk — <b>Approve</b> or <b>Regenerate</b>.
                      <span className="gcount"> · {guided.chunks.filter((_, i) => approved[i]).length}/{guided.chunks.length} approved</span>
                    </div>
                  )}
                  {/* A step that failed but left the run intact. Shown here, inside the
                      panel, so it reads as "that click didn't work, try again" rather
                      than tearing the review screen down. */}
                  {guided.last_error && guidedReviewing && (
                    <div className="alert warn">
                      <b>That step didn’t complete — nothing was lost.</b>
                      <pre>{guided.last_error}</pre>
                      Your chunks are all still here. Click the same button again to retry.
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
                                  {/* Also blocked while ANOTHER chunk is regenerating —
                                      the server only runs one step at a time and would
                                      reject the second click with a 409. */}
                                  <button className="primary"
                                          disabled={busyAction || gStatus === 'regenerating' || !regenReason.trim()}
                                          onClick={() => regenerateChunk(i)}>Regenerate</button>
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

          {learned && <LearnedRules rules={learned} sessionNo={result.session_no}
                                    course={learnedCourse} isAdmin={!!user.is_admin}
                                    onChanged={refreshLearned} />}

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

// Sheet templates as a docked side panel on the LEFT of the content column, so you
// can read the required columns while filling in the two links in Step 1. Wide
// screens get a fixed, independently-scrolling panel in the left gutter; narrower
// screens (no gutter) fall back to a normal card in the flow — see .tmplside in
// styles.css. Mirrors CostSidePanel, which occupies the right gutter.
function TemplateSidePanel({ markdown, onClose }) {
  return (
    <aside className="tmplside" aria-label="Required sheet templates">
      <div className="tsidehead">
        <span className="tsidetitle">📋 Sheet templates</span>
        <button className="csideclose" onClick={onClose} title="Hide">×</button>
      </div>
      {markdown
        ? <div className="md tsidebody">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
          </div>
        : <div className="csidepending"><span className="spinner" /> Loading…</div>}
    </aside>
  )
}

// Side panel with the cost of the CURRENT generation only — nothing cumulative.
// Sticky on the right of the content column on wide screens; a normal block on
// narrow ones (see .costside in styles.css).
function CostSidePanel({ cost, sessionNo, pending, onClose }) {
  const t = cost?.totals || {}
  const calls = t.calls || (cost?.calls || []).length
  const hasCost = t.cost != null      // native Anthropic SDK reports tokens but no $
  return (
    <aside className="costside" aria-label="Cost of this generation">
      <div className="csidehead">
        <span className="csidetitle">💰 This TR Doc</span>
        <button className="csideclose" onClick={onClose} title="Hide">×</button>
      </div>
      {pending ? (
        <div className="csidepending"><span className="spinner" /> Generating…
          <div className="csidenote">Cost shows here as soon as the doc is ready.</div>
        </div>
      ) : (
        <>
          <div className="csideval">{hasCost ? `$${t.cost.toFixed(4)}` : '—'}</div>
          <div className="csidesub">
            {hasCost ? 'cost of this generation' : 'this provider does not report $ cost'}
          </div>
          <div className="csiderows">
            <div><span>Session</span><b>{sessionNo ?? '—'}</b></div>
            <div><span>Tokens</span><b>{(t.total_tokens || 0).toLocaleString()}</b></div>
            <div><span>LLM calls</span><b>{calls || 0}</b></div>
          </div>
        </>
      )}
    </aside>
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

function LearnedRules({ rules, sessionNo, course, isAdmin, onChanged }) {
  const newCount = rules.filter((r) => r.session_no === sessionNo).length
  const applied = rules.filter((r) => r.applies !== false).length
  // Rules stored before the upgrade have no scope and were never distilled. They are
  // still injected (as house style), so flag them and offer the one-click migration —
  // a deploy cannot carry the locally-migrated store, since it is gitignored.
  const unmigrated = rules.filter((r) => !r.scope).length
  const srcLabel = { regeneration: 'human', judge: 'auto · judge', eval_set: 'auto · eval', feedback: 'human' }
  const [busy, setBusy] = useState(null)
  const [migrating, setMigrating] = useState(false)
  function remove(i, text) {
    if (!window.confirm(`Stop applying this rule to future generations?\n\n${text}`)) return
    setBusy(i)
    api.deleteLearnedRule(i).then(() => onChanged && onChanged())
      .catch((e) => alert(e.message)).finally(() => setBusy(null))
  }
  function migrate() {
    setMigrating(true)
    api.migrateLearnedRules()
      .then((d) => {
        onChanged && onChanged()
        alert(`Migrated.\nDistilled: ${d.distil.merged} merged, ${d.distil.after} kept.\n`
              + `Scoped: ${d.scope.global} house, ${d.scope.course} course-specific.`)
      })
      .catch((e) => alert(e.message)).finally(() => setMigrating(false))
  }
  return (
    <details className="panel learned" open={newCount > 0}>
      <summary>
        🧠 What the agent has learned
        <span className="muted"> — {applied} of {rules.length} rule{rules.length === 1 ? '' : 's'} applied to <b>{course || 'this course'}</b></span>
        {newCount > 0 && <span className="chip good" style={{ marginLeft: 8 }}>+{newCount} this run</span>}
      </summary>
      {rules.length === 0 ? (
        <div className="just" style={{ padding: '6px 2px' }}>
          Nothing learned yet. As generations and eval runs surface defects, durable rules appear here and are injected into later sessions automatically.
        </div>
      ) : (
        <div className="scorelist">
          <div className="just" style={{ padding: '2px 2px 8px' }}>
            <b>House</b> rules apply to every course. <b>Course</b> rules are about one
            curriculum's subject matter and apply only there — a greyed row was learned
            on a different course and is not being applied now.
          </div>
          {unmigrated > 0 && (
            <div className="alert warn">
              <b>{unmigrated} rule{unmigrated === 1 ? '' : 's'} predate the current format.</b>
              <div>They are stored as the raw note you typed and are being applied to
              <b> every</b> course. Migrating distils each into a standalone instruction
              and marks it house-style or course-specific. Your original wording is kept.</div>
              {isAdmin
                ? <button className="ghostbtn" disabled={migrating} onClick={migrate}>
                    {migrating ? 'Migrating…' : '🔧 Distil & scope them'}
                  </button>
                : <div className="hint">An admin needs to run this.</div>}
            </div>
          )}
          {rules.map((r, i) => (
            <div key={i} className={`setrow ${r.session_no === sessionNo ? 'pass' : ''}`}
                 style={r.applies === false ? { opacity: 0.45 } : undefined}>
              <div className="setmain">
                <span className="tag">{srcLabel[r.source] || r.source || 'rule'}</span>
                <span className="tag" title={r.scope === 'course'
                  ? `Subject-matter rule — applies only to ${r.course || 'its course'}`
                  : 'House-style rule — applies to every course'}>
                  {r.scope === 'course' ? `course · ${r.course || '?'}` : 'house'}
                </span>
                <span className="dimname">{r.text}</span>
                {r.hits > 1 && <span className="chip mid" title="you have asked for this more than once">×{r.hits}</span>}
                {r.session_no === sessionNo && <span className="chip good">new</span>}
                {/* These rules now outrank the style guide, so a badly-generalised one
                    has to be removable — otherwise it is pushed at every session. */}
                <button className="link" disabled={busy === i} title="Remove this rule"
                        onClick={() => remove(i, r.text)}>✕</button>
              </div>
              {r.raw && <div className="just">you wrote: “{r.raw}”</div>}
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
