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
  // Deck extraction gaps live in the left panel alongside the templates. Only one of
  // the two is open at a time — they share the slot, and stacking them would cover the
  // page on a laptop screen.
  const [showGaps, setShowGaps] = useState(false)

  // ONE sheet: the curriculum, whose "PPT Links" column carries each session's deck.
  const [courseLink, setCourseLink] = useState('')
  const [courseType, setCourseType] = useState('semester')
  const [courseName, setCourseName] = useState('Computer Networks')
  const [syncing, setSyncing] = useState(false)
  const [syncOut, setSyncOut] = useState(null)
  const [syncErr, setSyncErr] = useState(null)
  const [syncLogs, setSyncLogs] = useState([])
  const syncPollRef = useRef(null)

  // The agent's curriculum — loaded from the server, edited here, saved back. This is
  // the course after the first import; the sheet is not consulted again unless asked.
  const [curRows, setCurRows] = useState([])
  const [curPending, setCurPending] = useState(0)
  const [curSaving, setCurSaving] = useState(false)
  const [curIngesting, setCurIngesting] = useState(false)
  const [curLogs, setCurLogs] = useState([])
  const [importedFrom, setImportedFrom] = useState(null)
  const [showImport, setShowImport] = useState(false)
  const curDirty = curRows.some((r) => r._dirty)
  // Courses this person may work on. A course is a shared, team-owned thing now, so it
  // is picked from a list rather than typed — two people spelling the same course
  // differently used to end up with two separate curricula, and a course one person
  // imported was invisible to everyone else.
  const [courses, setCourses] = useState([])
  function loadCourses() {
    api.courses().then((d) => {
      setCourses(d.courses || [])
      if (d.active && !courseName) setCourseName(d.active)
    }).catch(() => {})
  }
  function switchCourse(name) {
    if (!name || name === courseName) return
    setCourseName(name)
    api.selectCourse(name, courseType).then((d) => {
      setCurRows(d.rows || [])
      setSessions(d.sessions || [])
      setSel((d.sessions || [])[0]?.number ?? null)
      setImportedFrom(d.imported_from || null)
      setShowImport(!(d.rows || []).length)
      setSyncOut(null); setCurLogs([])
      api.curriculum(name).then((c) => setCurPending(c.pending || 0)).catch(() => {})
    }).catch((e) => setCurLogs([e.message]))
  }

  function loadCurriculum(forCourse) {
    api.curriculum(forCourse || courseName || undefined).then((d) => {
      setCurRows(d.rows || [])
      setCurPending(d.pending || 0)
      setImportedFrom(d.imported_from || null)
      // Only ask for a sheet when there is nothing to show; otherwise the dashboard is
      // the course and the import form is a deliberate choice.
      setShowImport(!(d.rows || []).length)
    }).catch(() => {})
    // The sessions still needing a TR doc, so generation is available without a sync.
    api.sessions(forCourse || courseName || undefined).then((s) => {
      setSessions(s.sessions || [])
      setSel((cur) => cur ?? (s.sessions || [])[0]?.number ?? null)
    }).catch(() => {})
  }

  const [sessions, setSessions] = useState([])
  const [sel, setSel] = useState(null)
  // Generation policy comes from the harness via /api/status — the LLM quality check
  // and the 40-minute budget are always on, and every doc is page-capped. The literals
  // here are only the fallback for a status response that predates the `policy` field.
  const policy = status?.policy || {
    judge_always_on: true, time_always_enforced: true,
    max_minutes: 40, max_pages: 16, target_pages: 14,
  }

  const [result, setResult] = useState(null)
  const [genErr, setGenErr] = useState(null)

  // Guided generation is the ONLY way a doc is written: generate all chunks -> review
  // each -> finalize. The old one-shot mode (whole doc in a single unreviewed call) is
  // gone, so there is no mode to choose between any more.
  const [guidedId, setGuidedId] = useState(null)
  // The id of a guided run this browser started but never finished. The server
  // checkpoints guided runs, so one left behind by a reload or a server restart can
  // be resumed instead of stranding chunks that cost an LLM call each. Read once at
  // mount (before any state reset can clear it) and offered as an explicit Resume.
  const [resumableGid, setResumableGid] = useState(() => localStorage.getItem('tr_guided_id') || null)
  // …and the same question asked of the SERVER, which knows every unfinished run this
  // user started rather than only the ones this browser saw. Without it, starting a run
  // on one machine and signing in from another stranded chunks that had already been
  // paid for. Rows: {guided_id, session_no, title, status, chunks_done, total, updated}.
  const [serverResumable, setServerResumable] = useState([])
  function rememberGuided(gid) {
    if (gid) localStorage.setItem('tr_guided_id', gid)
    else localStorage.removeItem('tr_guided_id')
    setResumableGid(gid)
  }
  function refreshResumable() {
    api.guidedResumable().then((d) => setServerResumable(d.runs || [])).catch(() => {})
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
  // Ask on sign-in and after each finished doc — a run that just completed must drop
  // off the list, and one abandoned earlier must appear on it.
  useEffect(() => { if (user) refreshResumable() }, [user, result])
  // The curriculum is what the app opens onto now, so it loads with the session.
  useEffect(() => { if (user) { loadCourses(); loadCurriculum() } }, [user])

  function saveCurriculum() {
    setCurSaving(true); setCurLogs([])
    const dirty = curRows.filter((r) => r._dirty)
    api.saveCurriculum(dirty.map((r) => ({
      session_no: Number(r.session_no), topic: r.topic || '',
      session_name: r.session_name || '',
      key_takeaways: (r.key_takeaways || []).filter((l) => String(l).trim()),
      ppt_link: r.ppt_link ?? null,
    })), courseName || undefined).then((d) => {
      setCurLogs([`Saved ${d.saved} session(s).`])
      setCurRows(d.rows || [])
      api.curriculum(courseName || undefined).then((c) => setCurPending(c.pending || 0)).catch(() => {})
      api.sessions(courseName || undefined).then((s) => setSessions(s.sessions || [])).catch(() => {})
    }).catch((e) => setCurLogs([`Could not save: ${e.message}`]))
      .finally(() => setCurSaving(false))
  }

  function deleteCurriculumRow(row, i) {
    // A row never saved has no server side yet — drop it locally.
    if (row._new) { setCurRows((rs) => rs.filter((_, k) => k !== i)); return }
    if (!window.confirm(`Remove session ${row.session_no} from the curriculum?`)) return
    api.deleteCurriculumRow(row.session_no, courseName || undefined)
      .then((d) => { setCurRows(d.rows || []); setCurLogs([`Removed session ${row.session_no}.`]) })
      .catch((e) => setCurLogs([`Could not remove: ${e.message}`]))
  }

  // Fetch decks. Without force this touches ONLY links that are new or changed — the
  // whole reason a sync no longer costs a full re-download of the course.
  function ingestDecks(force) {
    setCurIngesting(true); setCurLogs([])
    api.ingestDecks(force, null, courseName || undefined).then(({ job_id }) => {
      const t = setInterval(async () => {
        try {
          const job = await api.job(job_id)
          setCurLogs(job.logs || [])
          if (job.status === 'done') {
            clearInterval(t); setCurIngesting(false)
            setSyncOut(job.result); setSessions(job.result.sessions || [])
            loadCurriculum()
          } else if (job.status === 'error') {
            clearInterval(t); setCurIngesting(false)
            setCurLogs((l) => [...l, `Failed: ${job.error}`])
          }
        } catch (e) { clearInterval(t); setCurIngesting(false); setCurLogs([e.message]) }
      }, 1000)
    }).catch((e) => { setCurIngesting(false); setCurLogs([e.message]) })
  }

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
  // Download/Google-Doc failures are shown IN the result card next to the copy-out
  // fallback, not as an alert() the user dismisses and then has nothing to act on.
  const [dlErr, setDlErr] = useState(null)
  const [copied, setCopied] = useState(false)
  // Feedback on a FINISHED doc — for a correction spotted only after assembly, which a
  // per-chunk regeneration reason can no longer capture.
  const [fbText, setFbText] = useState('')
  const [fbBusy, setFbBusy] = useState(false)
  const [fbDone, setFbDone] = useState(null)   // { rule, merged, message }
  const [fbErr, setFbErr] = useState(null)
  function sendFeedback(session_no) {
    setFbBusy(true); setFbErr(null); setFbDone(null)
    api.submitFeedback(session_no, fbText)
      .then((d) => { setFbDone(d); setFbText(''); refreshLearned() })
      .catch((e) => setFbErr(e.message))
      .finally(() => setFbBusy(false))
  }
  async function copyMarkdown(r) {
    // Prefer the markdown already in the result; fall back to fetching it, which also
    // works after a page reload (the server can recover it from the run's stored copy).
    let text = r?.markdown
    if (!text) {
      try { text = (await api.preview(r.session_no, r.run_id, r.docx_name)).markdown }
      catch (e) { setDlErr(e.message); return }
    }
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true); setTimeout(() => setCopied(false), 2500)
    } catch {
      setDlErr('Clipboard access was blocked by the browser — select the preview text below and copy it manually.')
    }
  }
  function createGoogleDoc(session_no, run_id, name) {
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
          api.createGdoc(session_no, resp.access_token, run_id, name)
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
    setShowGaps(false)          // one left panel at a time
  }

  function doSync() {
    setSyncing(true); setSyncErr(null); setSyncOut(null); setSyncLogs([])
    api.sync(courseLink, courseType, courseName).then(({ job_id }) => {
      syncPollRef.current = setInterval(async () => {
        try {
          const job = await api.job(job_id)
          setSyncLogs(job.logs || [])
          if (job.status === 'done') {
            clearInterval(syncPollRef.current); setSyncing(false)
            const out = job.result
            setSyncOut(out); setSessions(out.sessions || [])
            if (out.sessions?.length) setSel(out.sessions[0].number)
            // The import wrote into the agent's curriculum — show it, and put the
            // import form away again.
            loadCurriculum(); setShowImport(false)
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
    api.guidedStart(sel, true, policy.time_always_enforced).then(({ guided_id }) => {
      setGuidedId(guided_id)
      rememberGuided(guided_id)
      pollGuided(guided_id)
    }).catch((e) => setGenErr(e.message))
  }

  // Pick an unfinished guided run back up (after a reload, or after the server was
  // restarted/spun down mid-review). The chunks come back from the server's
  // checkpoint, so nothing already generated has to be paid for again.
  // Forget a run without resuming it. Local only: the server checkpoint expires on its
  // own after the purge window, and deleting it here would take away the ability to come
  // back to it from another browser — the exact gap the server list exists to close.
  function discardGuided(gid) {
    setServerResumable((rs) => rs.filter((r) => r.guided_id !== gid))
    if (gid === resumableGid) rememberGuided(null)
  }

  function resumeGuided(gidArg) {
    const gid = gidArg || resumableGid
    if (!gid) return
    setGenErr(null); setResult(null); setEvalReport(null); setEvalErr(null)
    setBusyAction(true)
    api.guidedState(gid).then((st) => {
      setBusyAction(false)
      if (st.status === 'done' || st.status === 'error') {
        discardGuided(gid)
        setGenErr(st.status === 'error'
          ? `That guided run had already failed: ${st.error || 'unknown error'}`
          : 'That guided run had already finished — nothing left to resume.')
        return
      }
      // Resuming makes this the ACTIVE run, so remember it here too: the id may have
      // come from the server list on a machine that had never seen it.
      rememberGuided(gid)
      if (st.session_no != null && st.session_no !== sel) {
        skipSelResetRef.current = true   // our own change; do not tear the run down
        setSel(st.session_no)
      }
      setGuidedId(gid); setGuided(st); setApproved({}); setShowCost(true)
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
    api.evalSets(result.session_no, true, policy.time_always_enforced).then(({ job_id }) => {
      evalPollRef.current = setInterval(async () => {
        try {
          const job = await api.job(job_id)
          if (job.status === 'done') { clearInterval(evalPollRef.current); setEvalRunning(false); setEvalReport(job.result) }
          else if (job.status === 'error') { clearInterval(evalPollRef.current); setEvalRunning(false); setEvalErr(job.error) }
        } catch (e) { clearInterval(evalPollRef.current); setEvalRunning(false); setEvalErr(e.message) }
      }, 1500)
    }).catch((e) => { setEvalRunning(false); setEvalErr(e.message) })
  }

  // Picking a DIFFERENT session drops the guided panel — it belongs to the session it
  // was generated for. Resuming also moves the selector (to the resumed run's session),
  // and that must NOT count: it would tear down the run we are in the middle of
  // restoring. The ref marks the one selector change that is ours.
  const skipSelResetRef = useRef(false)
  useEffect(() => {
    if (skipSelResetRef.current) { skipSelResetRef.current = false; return }
    guidedPollRef.current && clearInterval(guidedPollRef.current)
    setGuidedId(null); setGuided(null); setRegenFor(null); setRegenReason(''); setApproved({})
  }, [sel])

  useEffect(() => () => {
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
        {/* No provider/model/version chips: which LLM runs the agent is an internal
            detail that changes over time, and the user has no decision to make on it.
            A missing API key is surfaced where it blocks you — next to Generate. */}
        <div className="userbox">
          {user.picture && <img className="avatar" src={user.picture} alt="" referrerPolicy="no-referrer" />}
          <span className="uemail">{user.email}{user.is_admin && <span className="pill admin">admin</span>}</span>
          <button className="link" onClick={signOut}>Sign out</button>
        </div>
      </header>

      <p className="sub">Generate a recording-ready Word TR doc for one session, from the curriculum your team keeps in the agent.</p>

      {/* THE COURSE PICKER. A course belongs to a TEAM, so whatever one member imports
          is what everyone on that team opens — no re-pasting a sheet, no second copy of
          the same course under a slightly different name. The list is exactly the
          courses this person's teams own. */}
      {courses.length > 0 && (
        <div className="coursebar">
          <label htmlFor="coursepick">Course</label>
          <select id="coursepick" value={courseName}
                  onChange={(e) => switchCourse(e.target.value)}>
            {!courses.some((c) => c.name === courseName) && courseName && (
              <option value={courseName}>{courseName} (not imported yet)</option>
            )}
            {courses.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} — {c.sessions} session{c.sessions === 1 ? '' : 's'}
                {c.teams?.length ? ` · ${c.teams.join(', ')}` : ''}
              </option>
            ))}
          </select>
          <button className="link" onClick={() => setShowImport((v) => !v)}>
            + Import another course
          </button>
        </div>
      )}

      {/* Cost of the TR doc being generated right now — sticky side panel
          (falls back to a normal block on narrow screens). */}
      {showCost && (guidedActive || result) && (
        <CostSidePanel
          cost={result?.cost}
          sessionNo={result?.session_no ?? sel}
          pending={!result && guidedActive}
          onClose={() => setShowCost(false)}
        />
      )}

      <button className="link" onClick={loadGuide}>
        📋 {showGuide ? 'Hide' : 'Show'} the required sheet template
      </button>
      {showGuide && <TemplateSidePanel markdown={guide} onClose={() => setShowGuide(false)} />}
      {showGaps && syncOut?.extraction_warnings?.length > 0 && (
        <GapsSidePanel warnings={syncOut.extraction_warnings} onClose={() => setShowGaps(false)} />
      )}

      {/* THE CURRICULUM the agent holds. Shown first because it IS the course now —
          the sheet below is only how a course gets in the first time. */}
      {curRows.length > 0 && (
        <CurriculumDashboard
          course={courseName} rows={curRows} setRows={setCurRows}
          onSave={saveCurriculum} onDelete={deleteCurriculumRow} onIngest={ingestDecks}
          saving={curSaving} ingesting={curIngesting} dirty={curDirty}
          pending={curPending} importedFrom={importedFrom} logs={curLogs}
          onReimport={() => setShowImport((v) => !v)}
        />
      )}

      {/* IMPORT — asked for when the agent has no curriculum yet, or on request. */}
      {(showImport || curRows.length === 0) && (
      <section className="card">
        <h2><span className="num">{curRows.length ? '↺' : '1'}</span>{' '}
          {curRows.length ? 'Re-import from a sheet' : 'Import your course'}</h2>
        <div className="settingsrow">
          <div className="settingcol">
            <label>Course</label>
            <input value={courseName} onChange={(e) => setCourseName(e.target.value)}
                   placeholder="e.g. Computer Networks" />
            <span className="hint">
              The name this course is imported under. Once imported it appears in the
              course picker for everyone on your team.
            </span>
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
        {/* The sheet is an IMPORT FORMAT, not a dependency. It is asked for when the
            agent has no curriculum for this course, or when the user explicitly chooses
            to re-import; the rest of the time the dashboard below is the course. */}
        <label>Course Curriculum Structure — Google Sheet link</label>
        <input value={courseLink} onChange={(e) => setCourseLink(e.target.value)}
               placeholder="https://docs.google.com/spreadsheets/d/.../edit" />
        <span className="hint">
          One sheet only. Its <b>PPT Links</b> column holds each past session's Google
          Slides deck — leave that cell blank for a session not recorded yet.
          <b> You only need this once</b>: after importing, the curriculum lives in the
          agent and you edit it in the dashboard below.
        </span>
        <button className="primary" disabled={!courseLink || syncing} onClick={doSync}>
          {syncing ? 'Importing…' : (curRows.length ? '↺ Re-import from this sheet' : '📥 Import this course')}
        </button>
        {curRows.length > 0 && (
          <span className="hint">
            Re-importing refreshes names, takeaways and links from the sheet. Rows you
            added in the agent are kept, and no already-extracted deck is downloaded again.
          </span>
        )}

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
            {/* Extraction gaps are DIAGNOSTICS, not something the sync did wrong: a
                handful of decks always have an image-only or divider slide with no
                extractable text. Even collapsed, a full-width block in the middle of the
                sync result reads as "something needs your attention" on every sync. It is
                reference material you consult if you feel like it, so it belongs in the
                left panel with the sheet templates — one line here, the detail there. */}
            {syncOut.extraction_warnings?.length > 0 && (
              <button className="link gapslink" onClick={() => { setShowGaps((v) => !v); setShowGuide(false) }}>
                🔍 {showGaps ? 'Hide' : 'Show'} deck extraction gaps ({syncOut.extraction_warnings.length})
              </button>
            )}
          </div>
        )}
      </section>
      )}

      {/* STEP 2 — generation, available as soon as the agent holds a curriculum */}
      {/* Gated on the curriculum the agent HOLDS, not on having just run a sync in this
          browser: the course is in the database now, so generation is available the
          moment you open the app. */}
      {sessions.length > 0 && (
        <section className="card">
          <h2><span className="num">2</span> Generate a TR doc</h2>
          <label>Session</label>
          <select value={sel ?? ''} onChange={(e) => setSel(Number(e.target.value))}>
            {sessions.map((s) => <option key={s.number} value={s.number}>{s.number} — {s.name}</option>)}
          </select>
          {/* Says out loud what the list is, so a session leaving it after a deck is
              attached reads as the rule working rather than as something going wrong. */}
          <span className="hint">
            Only sessions that still need a TR doc are listed. Attaching a deck in the
            curriculum marks a session as already recorded, so it moves into the agent's
            course memory and leaves this list.
          </span>
          {selSession && (
            <details className="takeaways">
              <summary>Key takeaways ({selSession.takeaways.length})</summary>
              {/* No bullet markers: the curriculum lines already begin with their own
                  "1." / "2." numbering, so a bulleted list numbered them twice. */}
              <ul className="plainlist">{selSession.takeaways.map((k, i) => <li key={i}>{k}</li>)}</ul>
            </details>
          )}
          {/* The policy chips (LLM quality check / 40-minute session / max pages) are
              gone from here. They restated three constants on every visit and there was
              no decision attached to any of them — all three are enforced regardless.
              They still show where they are ACTIONABLE: the budgets appear in the hint
              under the generate button, and the grading bar appears next to the score. */}

          {/* The only server-config fact worth showing a user: without a key the
              Generate button below is dead, and a silently disabled button is worse
              than no button. */}
          {status && !status.key_ok && (
            <div className="alert error">
              <b>The generator is not configured</b>
              <pre>No API key is set on the server, so generation is disabled. Ask an admin to set it.</pre>
            </div>
          )}

          {/* Guided review is the only generation path. The one-shot mode that wrote a
              whole doc in a single unreviewed call has been removed: nothing it produced
              was ever seen by a human before it was assembled. */}
          {(
            <>
              {!guidedId && (
                <button className="primary" disabled={sel == null || !status?.key_ok} onClick={startGuided}>
                  🚦 Generate all chunks
                </button>
              )}
              {/* Unfinished runs. Every chunk is checkpointed as it is generated, so
                  nothing already paid for is lost when a run is abandoned, the browser
                  reloads, or the server restarts mid-review. The list comes from the
                  SERVER (this user's runs, any browser); the localStorage id is kept as
                  a fallback for a run the server list has not caught up with. */}
              {!guidedId && (serverResumable.length > 0 || resumableGid) && (
                <div className="resumebox">
                  <b>↩ Unfinished TR doc{serverResumable.length > 1 ? 's' : ''}</b>
                  <div className="hint">
                    Every chunk generated so far is saved on the server, so resuming picks
                    up where you stopped instead of paying for those chunks again.
                    Checkpoints are kept for <b>72 hours</b>.
                  </div>
                  {serverResumable.map((r) => (
                    <div key={r.guided_id} className="resumerow">
                      <span className="rtitle">
                        Session {r.session_no}{r.title ? ` — ${r.title}` : ''}
                      </span>
                      <span className="muted rmeta">
                        {r.chunks_done}/{r.total} chunk{r.total === 1 ? '' : 's'} generated
                        {r.status === 'reviewing' ? ' · waiting for your review' : ` · ${r.status}`}
                      </span>
                      <span className="ractions">
                        <button className="ghostbtn" disabled={busyAction}
                                onClick={() => resumeGuided(r.guided_id)}>↩ Resume</button>
                        <button className="ghostbtn" disabled={busyAction}
                                onClick={() => discardGuided(r.guided_id)}>Discard</button>
                      </span>
                    </div>
                  ))}
                  {/* Only if the server list does not already contain it. */}
                  {resumableGid && !serverResumable.some((r) => r.guided_id === resumableGid) && (
                    <div className="resumerow">
                      <span className="rtitle">A run started in this browser</span>
                      <span className="ractions">
                        <button className="ghostbtn" disabled={busyAction}
                                onClick={() => resumeGuided(resumableGid)}>↩ Resume</button>
                        <button className="ghostbtn" onClick={() => rememberGuided(null)}>
                          Discard
                        </button>
                      </span>
                    </div>
                  )}
                </div>
              )}
              <div className="hint">
                Generates <b>every chunk first</b> (one per key takeaway), then you
                <b> review each</b>, <b>approve</b> it or <b>regenerate</b> with a reason
                (that reason also teaches the agent for future sessions). All chunks must be
                approved before <b>Create final TR Doc</b>.
                {` Fitted to the ${policy.max_minutes}-minute budget and ${policy.max_pages} pages.`}
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
                        {/* Shown BEFORE the Approve button, because this is the cheap
                            moment to fix it: regenerating one section costs a fraction
                            of a repair pass over the assembled document, and these same
                            bullets will fail the run at finalize otherwise. */}
                        {!regenning && c.repetition?.length > 0 && (
                          <div className="alert warn">
                            <b>⚠ {c.repetition.length} bullet(s) repeat the paragraph above them</b>
                            <ul>{c.repetition.map((x, k) => <li key={k}>{x}</li>)}</ul>
                            The page budget is fixed, so a repeated line is a line that
                            teaches nothing new. Regenerate with a reason like{' '}
                            <i>"rewrite the bullets to carry what the paragraph does not
                            say — the steps, values, conditions and trade-offs"</i>.
                          </div>
                        )}
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
                    sub={`budget ${result.time.max_minutes}`} />
            {result.pages && (
              <Metric label="Length" value={`~${result.pages.estimated_pages} pages`}
                      sub={`max ${result.pages.max_pages}`} />
            )}
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
            <button className="primary download" onClick={() => api.downloadDoc(result.session_no, result.run_id, result.docx_name).catch((e) => setDlErr(e.message))}>⬇️ Download Word (.docx)</button>
            <button className="ghostbtn" disabled={gdocBusy} onClick={() => createGoogleDoc(result.session_no, result.run_id, result.docx_name)}>
              {gdocBusy ? 'Creating Google Doc…' : '📄 Create Google Doc'}
            </button>
            {/* Last-resort escape hatch. A reviewer once had BOTH the download and the
                Google Doc fail on a finished document and copied it out of the preview
                by hand. Both paths are fixed, but the copy button stays: no one should
                ever be one broken button away from losing an hour of review. */}
            <button className="ghostbtn" onClick={() => copyMarkdown(result)}>
              {copied ? '✓ Copied' : '📋 Copy full text'}
            </button>
          </div>
          {dlErr && (
            <div className="alert error">
              <b>Could not produce the file.</b>
              <pre>{dlErr}</pre>
              Your document is not lost — use <b>Copy full text</b> above, or the preview
              at the bottom of this page.
            </div>
          )}
          {gdoc?.session_no === result.session_no && gdoc.link && (
            <a className="gdoclink" href={gdoc.link} target="_blank" rel="noreferrer">
              🔗 Open in Google Docs — you have edit access
            </a>
          )}

          {/* Teach the agent from the finished document: the
              note is distilled into a durable rule injected into every future doc for
              this course, and the distilled text is shown back so a bad distillation
              can be spotted and deleted rather than silently applied for months. */}
          <details className="panel feedback">
            <summary>🧠 Teach the agent — what should change in future docs?</summary>
            <div className="fbbody">
              <textarea
                rows={3} value={fbText} disabled={fbBusy}
                placeholder="e.g. Don't put an analogy on a worked-example slide. Use realistic hex base addresses in memory examples."
                onChange={(e) => setFbText(e.target.value)} />
              <div className="row">
                <button className="ghostbtn" disabled={fbBusy || fbText.trim().length < 5}
                        onClick={() => sendFeedback(result.session_no)}>
                  {fbBusy ? 'Learning…' : 'Save as a rule'}
                </button>
                <span className="hint" style={{ marginTop: 0 }}>
                  Applied to every future doc in this course, above the style guide.
                </span>
              </div>
              {fbErr && <div className="alert error"><pre>{fbErr}</pre></div>}
              {fbDone && (
                <div className="alert ok">
                  <b>{fbDone.merged ? 'Folded into an existing rule.' : 'Learned.'}</b>
                  <div className="learnedtext">“{fbDone.rule?.text}”</div>
                  {fbDone.rule?.hits > 1 && (
                    <div className="hint" style={{ marginTop: 4 }}>
                      Raised {fbDone.rule.hits}× — flagged to the model as a repeated miss.
                    </div>
                  )}
                </div>
              )}
            </div>
          </details>

          {result.judge?.scores && <RubricPanel judge={result.judge} />}

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
        {/* "Total cost" was read as the price of ONE document — it is the running total
            across every generation. Labelled explicitly, with the per-doc average next to
            it, so the number that matters for a single run is visible directly. */}
        <Metric label="Cost — all runs" value={`$${(s.total_cost || 0).toFixed(4)}`}
                sub={`${s.total_runs || 0} generation(s)`} />
        <Metric label="Avg per doc"
                value={`$${(s.total_runs ? (s.total_cost || 0) / s.total_runs : 0).toFixed(4)}`} />
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
                {done ? <a href="#" onClick={(e) => { e.preventDefault(); api.downloadDoc(r.session_no, r.id, r.docx_name).catch((err) => alert(err.message)) }}>⬇️ .docx</a> : '—'}
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

// THE CURRICULUM DASHBOARD — the agent's own copy of the course, edited here.
//
// The sheet used to be the curriculum, which meant re-pasting a link to change one
// takeaway and re-reading it on every visit. It is an IMPORT FORMAT now: bring a course
// in once, then own it here. Adding a session, fixing a takeaway or attaching a deck
// link all happen in this table.
//
// The rule that makes it fast: saving a row never re-fetches a deck. A deck is
// downloaded once per LINK (Google offers no way to ask "did this change?" without
// sending the whole ~4.7 MB file), so only a new or changed link is marked pending, and
// "Fetch new decks" collects exactly those.
function CurriculumDashboard({ course, rows, setRows, onSave, onDelete, onIngest,
                               saving, ingesting, dirty, pending, importedFrom,
                               onReimport, logs }) {
  function edit(i, field, value) {
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, [field]: value, _dirty: true } : r)))
  }
  function addRow() {
    const next = rows.reduce((m, r) => Math.max(m, Number(r.session_no) || 0), 0) + 1
    setRows((rs) => [...rs, {
      // A stable key: React must not remount the row when its session number is edited,
      // or the field being typed into loses focus on every keystroke.
      _key: `new-${Date.now()}`,
      session_no: next, topic: '', session_name: '', key_takeaways: [],
      ppt_link: '', deck_status: 'none', extracted: false, _dirty: true, _new: true,
    }])
  }
  const deckChip = (r) => {
    if (!r.ppt_link) return <span className="chip">no deck</span>
    if (r.extracted) return <span className="chip good" title="Already extracted — syncing will not download it again">extracted</span>
    return <span className="chip mid" title="Will be fetched by 'Fetch new decks'">pending</span>
  }
  return (
    <section className="card">
      <h2><span className="num">1</span> Curriculum — {course}</h2>
      <p className="hint">
        This is the agent's own copy of the course. Edit a session, add a new one, or
        paste a deck link and press <b>Save</b>. Decks are downloaded <b>once per link</b>,
        so saving an edit never re-fetches anything.
      </p>

      <div className="curactions">
        <button className="ghostbtn" onClick={addRow}>+ Add session</button>
        <button className="primary" disabled={!dirty || saving} onClick={onSave}>
          {saving ? 'Saving…' : '💾 Save changes'}
        </button>
        <button className="ghostbtn" disabled={ingesting || !pending} onClick={() => onIngest(false)}
                title="Downloads only decks that are new or whose link changed">
          {ingesting ? 'Fetching…' : `⬇ Fetch new decks${pending ? ` (${pending})` : ''}`}
        </button>
        <button className="ghostbtn" disabled={ingesting} onClick={() => onIngest(true)}
                title="Re-downloads every deck. Only needed if you edited the slides behind a link that did not change.">
          ↻ Re-check all decks
        </button>
        <span className="curspacer" />
        <button className="link" onClick={onReimport}>
          {importedFrom ? '↺ Re-import from the sheet' : '📥 Import from a sheet'}
        </button>
      </div>

      {logs?.length > 0 && <pre className="logs">{logs.join('\n')}</pre>}

      {/* THE SHEET'S OWN SHAPE. Takeaways and the deck link used to hide behind a
          per-row expander, so reading the course meant opening every session one at a
          time — the opposite of what a spreadsheet is for. Every column is inline now,
          and the takeaways cell is a real multi-line box, so the whole curriculum reads
          at a glance exactly as it does in the sheet it came from. */}
      <div className="curtable sheetlike">
        <div className="currow curhead">
          <span className="c-no">Session</span>
          <span className="c-topic">Topic name</span>
          <span className="c-name">Session name</span>
          <span className="c-kt">Key takeaways — one per line</span>
          <span className="c-ppt">PPT link</span>
          <span className="c-deck">Deck</span>
          <span className="c-act" />
        </div>
        {rows.map((r, i) => (
          <div key={r._key ?? `${r.session_no}-${i}`} className={`currow ${r._dirty ? 'dirty' : ''}`}>
            <input className="c-no" type="number" value={r.session_no}
                   onChange={(e) => edit(i, 'session_no', Number(e.target.value))} />
            <input className="c-topic" value={r.topic || ''} placeholder="Topic"
                   onChange={(e) => edit(i, 'topic', e.target.value)} />
            <input className="c-name" value={r.session_name || ''} placeholder="Session name"
                   onChange={(e) => edit(i, 'session_name', e.target.value)} />
            <textarea className="c-kt" rows={Math.max(3, (r.key_takeaways || []).length)}
                      value={(r.key_takeaways || []).join('\n')}
                      placeholder={'1. Topic: sub-topic; sub-topic\n2. Topic: sub-topic'}
                      title="Each line becomes an agenda item, a section and a Key Takeaway verbatim. Everything after the colon is a promise the session must teach."
                      onChange={(e) => edit(i, 'key_takeaways', e.target.value.split('\n'))} />
            <textarea className="c-ppt" rows={2} value={r.ppt_link || ''}
                      placeholder="https://docs.google.com/presentation/d/…"
                      title="The deck for this session, if it has been recorded. Blank means the session still needs a TR doc. Changing this link is the only thing that makes the agent download a deck again."
                      onChange={(e) => edit(i, 'ppt_link', e.target.value)} />
            <span className="c-deck">{deckChip(r)}</span>
            <span className="c-act">
              <button className="ghostbtn tiny" title="Remove this session"
                      onClick={() => onDelete(r, i)}>✕</button>
            </span>
          </div>
        ))}
        {rows.length === 0 && (
          <div className="hint curempty">
            No curriculum yet — import one from a sheet, or add sessions by hand.
          </div>
        )}
      </div>
      <span className="hint">
        Each takeaway line becomes an agenda item, a section and a Key Takeaway
        <b> verbatim</b>; everything after the colon is treated as a promise the session
        must teach. A blank PPT link means the session still needs a TR doc.
      </span>
    </section>
  )
}

// Deck extraction gaps — the same left slot as the templates, for the same reason:
// reference material you open deliberately, not a verdict pushed at you. A few decks
// always contain an image-only or divider slide with no extractable text, so this list
// appears on every sync; inline it read as a standing alarm about a non-problem.
function GapsSidePanel({ warnings, onClose }) {
  return (
    <aside className="tmplside gapsside" aria-label="Deck extraction gaps">
      <div className="tsidehead">
        <span className="tsidetitle">🔍 Extraction gaps ({warnings.length})</span>
        <button className="csideclose" onClick={onClose} title="Hide">×</button>
      </div>
      <div className="tsidebody gapsbody">
        <p className="hint">
          Slides that gave us no title, body or table — usually an image-only slide, a
          section divider or a screenshot. The rest of each deck is ingested normally,
          so this is a completeness note, not a failure.
        </p>
        <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
      </div>
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

// The rubric panel used to be a flat list of thirteen dimensions in whatever order the
// judge returned them, with the total on top. That shows a number without explaining it:
// "why is this 86 and not 95?" was unanswerable without reading every justification and
// knowing the weights by heart. Now the dimensions are ordered by what they actually COST
// — weight x how far below 5 they scored — and the bar is stated, so the panel answers
// the question it used to raise. A doc needs the total AND at least the per-dimension
// minimum on every single dimension, so a lone 3 fails a 95-point document.
function RubricPanel({ judge }) {
  const weights = judge.weights || {}
  const minTotal = judge.gates?.min_total
  const minDim = judge.gates?.min_per_dimension
  const rows = Object.entries(judge.scores).map(([dim, o]) => {
    const w = weights[dim] || 0
    return { dim, ...o, weight: w, lost: w ? ((5 - (o.score || 0)) / 5) * w : 0 }
  }).sort((a, b) => b.lost - a.lost || (a.score || 0) - (b.score || 0))
  const totalLost = rows.reduce((s, r) => s + r.lost, 0)
  const belowBar = minDim ? rows.filter((r) => (r.score || 0) < minDim) : []
  const perfect = rows.filter((r) => r.lost <= 0)
  return (
    <details className="panel rubric" open>
      <summary>
        Rubric — judge score <b>{judge.weighted_total}/100</b>
        <span className="muted"> ({rows.length} dimensions)</span>
      </summary>
      {minTotal != null && (
        <div className="rubricbar">
          To be accepted a doc needs <b>{minTotal}/100</b> overall
          {minDim != null && <> and at least <b>{minDim}/5</b> on <i>every</i> dimension</>}.
          {totalLost > 0 && <> This one gave away <b>{totalLost.toFixed(1)} points</b>, listed
            worst first below — a 4/5 means “strong, negligible issues”, so several 4s
            alone put a document in the mid-80s.</>}
          {belowBar.length > 0 && (
            <div className="rubricblock">
              ⚠ Below the per-dimension bar, which fails the run on its own:{' '}
              <b>{belowBar.map((r) => pretty(r.dim)).join(', ')}</b>
            </div>
          )}
        </div>
      )}
      <div className="scorelist">
        {rows.map((r) => (
          <div key={r.dim} className={`scorerow ${r.lost > 0 ? 'cost' : ''}`}>
            <div className="scorehead">
              <ScoreChip score={r.score} />
              <span className="dimname">{pretty(r.dim)}</span>
              {r.weight > 0 && (
                <span className="dimcost muted">
                  weight {r.weight}
                  {r.lost > 0 ? ` · −${r.lost.toFixed(1)} pts` : ' · full marks'}
                </span>
              )}
            </div>
            <div className="just">{r.justification}</div>
          </div>
        ))}
      </div>
      {perfect.length === rows.length && (
        <div className="ok-note">Every dimension at full marks.</div>
      )}
    </details>
  )
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
                {/* A rule a guardrail now enforces is no longer injected OR checked by
                    the judge — the judge used to re-adjudicate it from prose and could
                    fail a compliant doc on a violation that wasn't there. Shown, not
                    hidden, so it's clear the correction is still in force. */}
                {r.gated && <span className="chip good" title={`Enforced automatically by ${r.gated}. No longer sent to the model or the judge — the check is exact now.`}>auto-enforced</span>}
                {/* These rules now outrank the style guide, so a badly-generalised one
                    has to be removable — otherwise it is pushed at every session. */}
                <button className="link" disabled={busy === i} title="Remove this rule"
                        onClick={() => remove(i, r.text)}>✕</button>
              </div>
              {r.gated && <div className="just">Now enforced by {r.gated} — a hard gate rather than a prompt instruction.</div>}
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
