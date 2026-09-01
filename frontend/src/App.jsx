import React, { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, setAuthToken, setOnUnauthorized, setRenewCredential } from './api'
import Icon from './Icon'

export default function App() {
  // --- Auth (Google Sign-In, @nxtwave.co.in only) ---
  const [authCfg, setAuthCfg] = useState(null)   // {client_id, allowed_domain, configured, auth_disabled}
  // Read by the silent-renewal callback, which is registered once and must not be torn
  // down and rebuilt every time the config object changes identity.
  const authCfgRef = useRef(null)
  const [user, setUser] = useState(null)         // {email, name, picture, is_admin}
  const [authErr, setAuthErr] = useState(null)

  const [status, setStatus] = useState(null)
  const [guide, setGuide] = useState('')
  const [showGuide, setShowGuide] = useState(false)
  // Deck extraction gaps live in the left panel alongside the templates. Only one of
  // the two is open at a time — they share the slot, and stacking them would cover the
  // page on a laptop screen.
  const [showGaps, setShowGaps] = useState(false)
  // "How skills work" — the explanation that used to be printed down the skills page.
  // Same docked slot as the sheet templates, one at a time.
  const [showSkillHelp, setShowSkillHelp] = useState(false)

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
  // The course's length budget (and what it falls back to). Held here because both
  // the dashboard control and the per-row placeholders need it.
  const [budget, setBudget] = useState(null)
  function loadBudget(forCourse) {
    api.courseSettings(forCourse || courseName || undefined)
      .then(setBudget).catch(() => {})
  }
  // One session's own budget. Its own call, not part of the curriculum save: that
  // path upserts the whole row, so a request carrying only a session number and two
  // numbers would blank the session's name and takeaways.
  function saveSessionBudget(sessionNo, patch) {
    api.saveSessionSettings(courseName || undefined, sessionNo, patch)
      .then(applyCurriculumReply)
      .catch((e) => setCurLogs([e.message]))
  }

  function saveBudget(patch) {
    const next = { ...(budget?.settings || {}), ...patch }
    setBudget((b) => ({ ...(b || {}), settings: next }))
    api.saveCourseSettings(courseName || undefined, next)
      .then((d) => setBudget((b) => ({ ...(b || {}), effective: d.effective })))
      .catch(() => {})
  }
  // The course's own instructions, and what its learners already knew. Loaded when the
  // tab is opened rather than on every sign-in — neither is needed to draw the page.
  const [skillState, setSkillState] = useState(null)
  const [prereqState, setPrereqState] = useState(null)
  const [skillBusy, setSkillBusy] = useState(false)
  // Live progress of an external prerequisite's deck extraction — see pollJob.
  const [prereqJob, setPrereqJob] = useState(null)
  const [skillMsg, setSkillMsg] = useState(null)
  const approvedSkills = skillState?.approved || 0
  // Names the course whose creation just finished, so its rules page can say why it
  // opened. Cleared as soon as the user moves on.
  const [justCreated, setJustCreated] = useState(null)
  const [showImport, setShowImport] = useState(false)
  // Which job the import card is doing: creating a course that does not exist yet
  // (name editable, becomes a new entry in the picker) or re-importing the sheet into
  // the course already open (name locked, so a re-import cannot silently fork a second
  // copy of the course under a different name).
  const [newCourse, setNewCourse] = useState(true)

  // WHERE the user is working, and WHICH view they are looking at. The whole app used
  // to be one scrolling column; these two pieces of state are what turn it into an
  // application with places you can be.
  // WHICH SECTION IS OPEN — AND WHICH RUN — in the URL: `#skills`, `#generate/<id>`.
  //
  // Both were held only in React state. A reload dropped you back on Curriculum however
  // deep in a review you were, and the open run survived only in this browser's
  // localStorage, so the way back into a half-finished review was to find it in the
  // "unfinished docs" list and press Resume. Neither could be linked to. The hash is the
  // whole mechanism — no router, no dependency — and an unknown value falls back to the
  // curriculum rather than rendering nothing.
  // `window.`-qualified throughout. This component already has state called `history`
  // (the user's finished docs), which shadows the global — so a bare `history.replaceState`
  // read the React state, found null, and took the whole app down with it on first
  // render. The globals are not worth the two characters saved.
  const readHash = () => {
    const [t, g] = (window.location.hash || '').replace('#', '').split('/')
    return { tab: t || 'curriculum', gid: g || null }
  }
  const [tab, _setTab] = useState(() => readHash().tab)
  // The run named by the URL at load, tried once. A ref, not state: it is a one-shot
  // instruction to resume, and re-running it on every render would fight the user.
  const hashGidRef = useRef(readHash().gid)
  const writeHash = (t, gid) => {
    const h = `#${t}${gid ? `/${gid}` : ''}`
    if (window.location.hash === h) return
    if (typeof window.history?.replaceState === 'function') {
      window.history.replaceState(null, '', h)
    } else {
      window.location.hash = h
    }
  }
  const setTab = (id) => { _setTab(id); writeHash(id, id === 'generate' ? guidedId : null) }
  useEffect(() => {
    const onHash = () => _setTab(readHash().tab)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const [workspace, setWorkspace] = useState(() => {
    try { return JSON.parse(localStorage.getItem('tr_workspace')) || { kind: 'individual' } }
    catch (e) { return { kind: 'individual' } }
  })
  const [myTeams, setMyTeams] = useState([])
  const activeTeamInfo = myTeams.find((t) => t.id === workspace.team_id)
  // `keepCourse` is for the one caller that has just made the current course this team's
  // (see shareCourseWithTeam). `myTeams` is still the pre-share copy at that moment, so
  // the landing rule below would decide the course does not belong to the team and switch
  // away from the very curriculum the user was editing.
  function switchWorkspace(ws, keepCourse = false) {
    setWorkspace(ws)
    localStorage.setItem('tr_workspace', JSON.stringify(ws))
    if (keepCourse) return
    // Moving into a team means working on ITS courses, so land on one of them rather
    // than leaving the previous course selected and quietly writing into the wrong place.
    const t = myTeams.find((x) => x.id === ws.team_id)
    if (ws.kind === 'team' && t?.courses?.length && !t.courses.includes(courseName)) {
      switchCourse(t.courses[0])
      return
    }
    // …AND THE SAME COMING BACK. Only the team direction was handled, so switching to
    // Individual left a team course selected — which then showed in the individual
    // picker as the "currently open" entry, exactly the course this workspace is not
    // supposed to hold. Land on one of your own instead, or on nothing if you have none.
    if (ws.kind === 'individual' && !user?.is_admin && courses.length) {
      const mineOnly = courses.filter((c) => c.shelf === 'individual')
      if (!mineOnly.some((c) => c.name === courseName)) {
        if (mineOnly.length) {
          switchCourse(mineOnly[0].name)
        } else {
          // Nothing of your own yet: clear the course rather than keep showing a team's
          // curriculum under a workspace that does not own it. The create-course card is
          // what should be on screen here.
          setCourseName(''); setCurRows([]); setCurPending(0); setCurLogs([])
          setSessions([]); setSel(null); setShowImport(true); setNewCourse(true)
        }
      }
    }
  }
  function startNewCourse() {
    // The form lives in the Curriculum section, so go there — pressing this from
    // History or Generate otherwise looked like it did nothing at all.
    setTab('curriculum')
    setNewCourse(true); setShowImport(true)
    setCourseName(''); setCourseLink(''); setSyncErr(null); setSyncOut(null); setSyncLogs([])
    // …and clear the course currently on screen. Only the NAME was being cleared, so
    // the previous course's curriculum stayed on display underneath the create form —
    // as if the new course already had 34 sessions in it.
    setCurRows([]); setCurPending(0); setCurLogs([])
  }
  // startReimport() went with the toolbar button that was its only caller.
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
    api.workspaces().then((d) => setMyTeams(d.teams || [])).catch(() => {})
  }
  function switchCourse(name) {
    if (!name || name === courseName) return
    setCourseName(name)
    setSyncOut(null); setCurLogs([])
    // Make it the active course, then draw everything from ONE bootstrap. This used to
    // be a select call plus a second curriculum call whose only purpose was a count the
    // first response already had.
    api.selectCourse(name, courseType)
      .then(() => bootstrap(name))
      .catch((e) => setCurLogs([e.message]))
  }

  function loadCurriculum(forCourse) {
    loadBudget(forCourse)
    api.curriculum(forCourse || courseName || undefined).then((d) => {
      // The server resolves the course when we did not name one, so adopt its answer —
      // otherwise the picker and the table can drift apart after a cancel.
      if (d.course) setCourseName((c) => c || d.course)
      setCurRows(d.rows || [])
      setCurPending(d.pending || 0)
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
  // Carry this note into every chunk after the one being regenerated. Almost every
  // reviewer note is about the document rather than the one chunk in front of them, and
  // retyping it into six chunks in turn — waiting for each — is the same instruction six
  // times over.
  const [regenAll, setRegenAll] = useState(false)
  const [busyAction, setBusyAction] = useState(false)
  // Splitting a slide: which chunk's picker is open, and which slide it names.
  const [splitFor, setSplitFor] = useState(null)
  // Asking the agent about a chunk. Separate from busyAction on purpose: a question is
  // read-only, so it must not grey out Approve and Regenerate while it is in flight —
  // the reviewer can carry on working while an answer is being written.
  // PER SECTION, not one at a time. `askFor` was a single index, so opening one
  // section's chat closed another's — and the draft you were typing went with it. Both
  // are maps keyed by section index (-1 is the whole-document panel).
  //
  // Open state is the USER'S CHOICE where they have made one, and defaults to open once
  // a section has a conversation: nobody wants to reopen the answer they just asked for,
  // and nobody wants a wall of empty boxes on the sections they never asked about.
  const [chatOpen, setChatOpen] = useState({})
  const [askText, setAskText] = useState({})
  const [askWeb, setAskWeb] = useState(true)
  const [asking, setAsking] = useState(false)
  // Which chat suggestions have already been filed as draft skills, so the button can
  // say so instead of letting the same rule be proposed four times.
  const [rulePosted, setRulePosted] = useState({})
  const [splitSlide, setSplitSlide] = useState('')
  const [splitErr, setSplitErr] = useState(null)
  // Create-final-TR-doc has to say it is working the moment it is pressed. The status
  // only turns to 'assembling' when the next poll lands, and assembling a doc takes long
  // enough that a button which merely greys out reads as a click that did nothing.
  const [finalizing, setFinalizing] = useState(false)
  // WHICH CHUNKS ARE TICKED comes from the server now — it is derived, not held here.
  // It used to be local state, which meant a reload wiped the review and the two copies
  // could disagree about whether the final doc could be created.
  const approvedSet = new Set(guided?.approved_chunks || [])
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
  // Load on sign-in, and reload after a result or an eval run (both can add rules).
  // It used to fetch ONLY after a generation finished, which was fine while the list
  // was appended to the result page — but it has its own section now, and that section
  // sat empty until you happened to generate something in the same visit.
  useEffect(() => {
    if (user && (tab === 'rules' || result)) refreshLearned()
  }, [tab, user, result, evalReport])

  // The user's own history (grouped by course) + their teams' shared docs.
  const [history, setHistory] = useState(null)
  const [teams, setTeams] = useState(null)
  function refreshMine() {
    api.myHistory().then(setHistory).catch(() => {})
    api.myTeams().then((d) => setTeams(d.teams || [])).catch(() => {})
  }
  // History and Team data is only ever shown in those tabs, so it is fetched when one
  // is opened rather than on every sign-in — /my/teams alone is four queries. It also
  // refreshes after a run finishes, since that is when it changes.
  useEffect(() => {
    if (user && (tab === 'history' || tab === 'team')) refreshMine()
  }, [tab, user, result])

  function refreshSkills() {
    if (!courseName) return
    api.skills(courseName).then(setSkillState).catch(() => setSkillState(null))
  }
  function refreshPrereqs() {
    if (!courseName) return
    // A FAILED REFRESH IS NOT AN EMPTY ANSWER. Blanking the state on any error made the
    // panel say "None." — so a rate-limited status check, arriving right after a deck
    // import was interrupted, read as "your prerequisite and the 16 decks behind it are
    // gone". They were not: the row and every stored deck were exactly where they had
    // been. Keeping the last known state is both truer and calmer; the next successful
    // refresh replaces it.
    api.prereqs(courseName).then(setPrereqState).catch(() => {})
  }
  function refreshCourseRules() { refreshSkills(); refreshPrereqs() }
  // Also on a course switch: these are per COURSE, and showing the previous course's
  // rules under a new one is worse than showing none.
  useEffect(() => {
    if (user && (tab === 'skills' || tab === 'prereqs')) refreshCourseRules()
  }, [tab, user, courseName])

  // Follow a background job to completion, then refresh. Used by the external
  // prerequisite import, which fetches decks and so takes about as long as a sync.
  // Reading a course's decks takes seconds per link. The POST returns immediately, so
  // without live progress the page went silent while a dozen Google Slides decks were
  // pulled, and there was no way to tell that from nothing happening at all.
  // A poll that fails is not a job that failed. The instance this runs on is a free one:
  // it sleeps, it redeploys, and a single request can come back 502/503/504 from the
  // proxy while the read behind it is perfectly alive. This used to clear the interval on
  // the first such answer, so one blip at deck 9 of 29 ended the progress bar, the form,
  // and any refresh of the panel — for a job that went on reading. Transient answers are
  // now ridden out for a while, and only a run of them gives up.
  const POLL_GRACE = 12            // unanswered polls ridden out before we stop believing
  const POLL_MS = 2500             // see below — 1.2s got this page rate-limited
  function pollJob(id, done) {
    setPrereqJob({ done: 0, total: 0, slides: 0, failed: 0, stage: 'starting' })
    let misses = 0
    // BACK OFF ON FAILURE, rather than asking the same question 12 more times at full
    // speed. A deck import runs for minutes, and polling it every 1.2 seconds was enough
    // to earn an HTTP 429 from the host part-way through — at which point the page was
    // hammering hardest precisely when the server least wanted to hear from it. The base
    // interval is slower now (a job measured in minutes does not need sub-second
    // resolution) and each consecutive failure waits longer than the last.
    let skip = 0
    const tick = async () => {
      if (skip > 0) { skip -= 1; return }
      try {
        const job = await api.job(id)
        misses = 0
        if (job.progress) setPrereqJob(job.progress)
        if (job.status === 'done') {
          clearInterval(t); setPrereqJob(null)
          const n = job.result?.decks ?? 0
          setSkillMsg({ ok: true, text:
            `Read ${n} deck(s)`
            + (job.result?.slides ? `, ${job.result.slides} slide(s)` : '')
            + `. ${job.result?.topics ?? 0} topic(s) are now assumed knowledge for this `
            + `course, and their slide content is searched when it writes.`
            + (job.result?.errors?.length
                ? ` ${job.result.errors.length} link(s) could not be read: `
                  + job.result.errors.join('; ') : '') })
          done?.()
        } else if (job.status === 'error') {
          clearInterval(t); setPrereqJob(null)
          setSkillMsg({ ok: false, text: job.error }); done?.()
        }
      } catch (e) {
        // 404 means the SERVER no longer knows this job: it restarted, and the job list
        // is in memory. Nothing more will ever come, so stop at once — but the decks read
        // before it went down are saved as they land, so say what is actually there
        // rather than implying the whole read was lost.
        if (e.status === 404) {
          clearInterval(t); setPrereqJob(null)
          setSkillMsg({ ok: false, text:
            'The server restarted while the decks were being read, so it can no longer '
            + 'report on that job. Every deck read before that is kept — the count below '
            + 'is the real one. Paste the same list of links again to read the rest: the '
            + 'ones already read are skipped, not fetched twice.' })
          done?.()
          return
        }
        misses += 1
        if (misses < POLL_GRACE) {
          // Keep the bar up and say why it is not moving, instead of tearing it down —
          // and wait longer before each retry, so a server that is busy or throttling
          // gets quieter rather than louder.
          skip = Math.min(misses, 8)
          setPrereqJob((j) => ({
            ...(j || {}),
            stage: e.status === 429 ? 'server busy — asking less often'
                                    : 'waiting for the server',
          }))
          return
        }
        clearInterval(t); setPrereqJob(null)
        setSkillMsg({ ok: false, text:
          `${e.message} The reading itself may still be running — this only means the `
          + 'page stopped being able to ask. Reopen this panel to see what landed, and '
          + 'paste the same links again to finish: decks already read are skipped.' })
        done?.()
      }
    }
    const t = setInterval(tick, POLL_MS)
    // ASK ONCE IMMEDIATELY. With a 2.5s interval and no leading call, the bar sat
    // motionless for the first two and a half seconds of every import — which is exactly
    // the moment the reviewer is looking at it to find out whether their click worked.
    tick()
  }

  // Resolves TRUE only if the action actually succeeded, so a caller can decide whether
  // to clear its input. Clearing on click threw away what the author typed the moment
  // anything went wrong — and what they typed is the one thing they cannot get back.
  // `refetch` is what the action CHANGED that its own response does not already carry.
  // It used to refetch everything unconditionally, so approving one skill cost three
  // requests and sixteen DB round-trips — and /prereqs is the expensive one, because it
  // recomputes the coverage report over every prerequisite deck. Against a remote DB
  // where each round-trip is its own connection, that is the whole of the delay: the
  // approve response already contains the updated skills.
  function runSkillAction(fn, note, refetch = 'none') {
    setSkillBusy(true); setSkillMsg(null)
    return fn().then((r) => {
      if (r?.skills) setSkillState((st) => ({ ...(st || {}), ...r }))
      if (r?.prereqs || r?.report) setPrereqState((st) => ({ ...(st || {}), ...r }))
      setSkillMsg({ ok: true, text: note })
      if (refetch === 'all') refreshCourseRules()
      else if (refetch === 'prereqs') refreshPrereqs()
      else if (refetch === 'skills') refreshSkills()
      return true
    }).catch((e) => { setSkillMsg({ ok: false, text: e.message }); return false })
      .finally(() => setSkillBusy(false))
  }
  // Ask on sign-in and after each finished doc — a run that just completed must drop
  // off the list, and one abandoned earlier must appear on it.
  // The resumable list comes with the bootstrap; refresh it only when it can change.
  useEffect(() => { if (user && result) refreshResumable() }, [result])
  // The curriculum is what the app opens onto now, so it loads with the session.
  useEffect(() => { if (user) bootstrap() }, [user])

  // The rows the user has edited but not yet saved, in the shape the API wants.
  function dirtyPayload(rows) {
    return rows.filter((r) => r._dirty).map((r) => ({
      session_no: Number(r.session_no), topic: r.topic || '',
      session_name: r.session_name || '',
      key_takeaways: (r.key_takeaways || []).filter((l) => String(l).trim()),
      ppt_link: r.ppt_link ?? null,
    }))
  }

  // EVERY curriculum-mutating reply lands here. The table and the Generate dropdown are
  // two views of one curriculum, and they used to be updated in two different places:
  // save refreshed both (the dropdown via a second request), while insert and delete
  // refreshed only the table — so a session deleted from the curriculum stayed in the
  // dropdown, and picking it started a run against a session that no longer existed.
  // The server now returns both in one reply; applying it is one function.
  function applyCurriculumReply(d) {
    setCurRows(d.rows || [])
    if (d.sessions) setSessions(d.sessions)
  }

  function saveCurriculum() {
    setCurSaving(true); setCurLogs([])
    api.saveCurriculum(dirtyPayload(curRows), courseName || undefined).then((d) => {
      setCurLogs([`Saved ${d.saved} session(s).`])
      // The dropdown comes back WITH the save now, so the extra /api/sessions round
      // trip this used to fire afterwards is gone.
      applyCurriculumReply(d)
      api.curriculum(courseName || undefined).then((c) => setCurPending(c.pending || 0)).catch(() => {})
    }).catch((e) => setCurLogs([`Could not save: ${e.message}`]))
      .finally(() => setCurSaving(false))
  }

  // Insert and delete RENUMBER on the server and hand back the whole table, so any row
  // edited-but-not-saved would be replaced by the server's copy of it — the edit gone
  // without a word. It cannot simply be carried across either: the numbers it was made
  // against have just moved. So the pending edits are saved FIRST, against the
  // numbering they were made under, and the shift then carries them along with their
  // rows. Returns a promise so the caller runs after the save has landed.
  function savePendingFirst() {
    const pendingRows = dirtyPayload(curRows)
    if (!pendingRows.length) return Promise.resolve(0)
    return api.saveCurriculum(pendingRows, courseName || undefined)
      .then((d) => d.saved || pendingRows.length)
  }

  // Insert a session at a POSITION. Done on the server, not locally, because it moves
  // every row below it AND their extracted decks — one operation, or the course is left
  // with two rows claiming one number.
  function insertCurriculumRow(atSessionNo) {
    let saved = 0
    savePendingFirst()
      .then((n) => { saved = n; return api.insertCurriculumRow(atSessionNo, courseName || undefined) })
      .then((d) => {
        applyCurriculumReply(d)
        setCurLogs([(saved ? `Saved ${saved} edited session(s) first. ` : '') + (d.shifted
          ? `Inserted session ${d.inserted}. The ${d.shifted} session(s) below it moved `
            + `down one, and their extracted decks moved with them.`
          : `Added session ${d.inserted}.`)])
      })
      .catch((e) => setCurLogs([`Could not insert: ${e.message}`]))
  }

  function deleteCurriculumRow(row, i) {
    // A row never saved has no server side yet — drop it locally.
    if (row._new) { setCurRows((rs) => rs.filter((_, k) => k !== i)); return }
    // Say that the numbers move BEFORE it happens: removing session 5 of 34 makes the
    // old 6 the new 5, which is right for the curriculum but is not what "delete a row"
    // sounds like.
    const below = curRows.filter((r) => Number(r.session_no) > Number(row.session_no)).length
    const warn = below
      ? `\n\nThe ${below} session(s) below it move up one — the old `
        + `${Number(row.session_no) + 1} becomes ${row.session_no} — and their decks move `
        + `with them. Documents already generated keep the numbers they were built under.`
      : ''
    if (!window.confirm(`Remove session ${row.session_no} from the curriculum?${warn}`)) return
    // Same reason as the insert: deleting renumbers and returns the whole table, so
    // pending edits are saved against the numbering they were made under first.
    let saved = 0
    savePendingFirst()
      .then((n) => { saved = n; return api.deleteCurriculumRow(row.session_no, courseName || undefined) })
      .then((d) => {
        applyCurriculumReply(d)
        setCurLogs([(saved ? `Saved ${saved} edited session(s) first. ` : '') + (d.shifted
          ? `Removed session ${d.removed}. The ${d.shifted} session(s) below it moved up `
            + `one, and their extracted decks moved with them.`
          : `Removed session ${d.removed}.`)])
      })
      .catch((e) => setCurLogs([`Could not remove: ${e.message}`]))
  }

  // Fetch decks. Without force this touches ONLY links that are new or changed — the
  // whole reason a sync no longer costs a full re-download of the course.
  // MERGE A COURSE INTO A TEAM. A course started alone stays yours until you say
  // otherwise; handing it to a team makes its curriculum and its ENTIRE history the
  // team's, because both are gathered by course — so the people joining you see
  // everything already built, not just what happens next.
  const [sharing, setSharing] = useState(false)
  function shareCourseWithTeam(teamId) {
    if (!teamId || !courseName) return
    setSharing(true)
    const teamName = myTeams.find((x) => x.id === Number(teamId))?.name || 'the team'
    api.teamAddCourse(Number(teamId), courseName)
      .then(() => {
        setCurLogs([`“${courseName}” now belongs to ${teamName}. `
          + `Everyone on it can open this curriculum and see every doc built for it. `
          + `It has moved out of your individual workspace and into ${teamName}'s — `
          + `you are now working there.`])
        // Sharing MOVES the course: it leaves the individual shelf. Following it into the
        // team workspace keeps the curriculum the user was editing on screen instead of
        // having it disappear out from under them.
        switchWorkspace({ kind: 'team', team_id: Number(teamId) }, true)
        loadCourses()
      })
      .catch((e) => setCurLogs([`Could not share: ${e.message}`]))
      .finally(() => setSharing(false))
  }

  // MEMBERSHIP, when the signed-in user is this team's course owner (or an admin).
  // Adding a colleague is routine and low-stakes; routing every one through the single
  // admin account meant, in practice, that people did not get added at all. The server
  // decides whether it is allowed — `can_manage` on the team only decides whether to
  // OFFER it, and a refusal comes back as a message shown here.
  const [memberBusy, setMemberBusy] = useState(false)
  const [memberMsg, setMemberMsg] = useState(null)
  function changeMembers(fn, note) {
    setMemberBusy(true); setMemberMsg(null)
    fn()
      .then(() => { setMemberMsg({ ok: true, text: note }); refreshMine(); loadCourses() })
      .catch((e) => setMemberMsg({ ok: false, text: e.message }))
      .finally(() => setMemberBusy(false))
  }
  function addTeamMember(teamId, email) {
    const e = (email || '').trim()
    if (!e) return
    changeMembers(() => api.teamAddMember(teamId, e),
                  `${e} is on the team. They can open its courses and see everything built for them.`)
  }
  function removeTeamMember(teamId, email) {
    changeMembers(() => api.teamRemoveMember(teamId, email),
                  `${email} was removed. Their finished docs stay in the team's history.`)
  }

  // DELETING A COURSE you own. Two-step when it is shared: the first request comes back
  // 409 naming the teams, and that list is what the confirmation puts in front of the
  // user — a course on a team's shelf is the curriculum they work from, so it must not
  // disappear because somebody clicked once.
  const [deleting, setDeleting] = useState(false)
  const [deleteAsk, setDeleteAsk] = useState(null)   // {course, teams:[], message}
  function askDeleteCourse(name) {
    setDeleteAsk({ course: name, teams: [], message: null })
  }
  function doDeleteCourse(name, detach) {
    setDeleting(true)
    api.deleteCourse(name, detach)
      .then((r) => {
        setDeleteAsk(null)
        setCurLogs([`“${name}” was deleted — ${r.sessions_removed} session(s) removed.`
          + (r.teams_detached?.length
              ? ` It is no longer on ${r.teams_detached.map((t) => t.name).join(', ')}.` : '')
          + ` Documents already generated for it are kept, and stay downloadable from History.`])
        setCourses(r.courses || [])
        // Land on whatever the server moved the active course to, so the page is never
        // left showing a curriculum that no longer exists.
        setCourseName(r.course || '')
        setTab('curriculum')
        bootstrap(r.course || undefined)
        refreshMine()
      })
      .catch((e) => {
        if (e.status === 409 && e.kind === 'course_shared') {
          setDeleteAsk({ course: name, teams: e.detail?.teams || [], message: e.message })
        } else {
          setDeleteAsk({ course: name, teams: [], message: null, error: e.message })
        }
      })
      .finally(() => setDeleting(false))
  }

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
    // Hand the API layer a way to get a FRESH credential without any interaction, so a
    // token that lapses while the page is open renews instead of logging you out. GIS
    // answers from the existing Google session; if it cannot, we fall through to the
    // login screen exactly as before.
    setRenewCredential(() => new Promise((resolve) => {
      const gid = window.google?.accounts?.id
      if (!gid) return resolve(null)
      let settled = false
      const done = (t) => { if (!settled) { settled = true; resolve(t || null) } }
      try {
        // A one-shot callback for this renewal only; the sign-in callback is restored
        // by the initialise effect on the login screen.
        gid.initialize({
          client_id: authCfgRef.current?.client_id,
          callback: (resp) => done(resp?.credential),
          auto_select: true,
        })
        gid.prompt((n) => {
          // Not displayed / skipped / dismissed all mean "no silent credential".
          if (n?.isNotDisplayed?.() || n?.isSkippedMoment?.() || n?.isDismissedMoment?.())
            done(null)
        })
      } catch { done(null) }
      setTimeout(() => done(null), 8000)   // never hang a request on this
    }))
    api.authConfig().then((cfg) => {
      authCfgRef.current = cfg
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

  // ONE call for everything the page needs. It used to fan out into eight requests
  // (status, courses, workspaces, course-settings, curriculum, sessions, history,
  // teams, resumable), each its own round-trip and each re-reading what the others
  // had just read — which is what made selecting a course feel slow.
  function bootstrap(forCourse) {
    api.bootstrap(forCourse || undefined).then((b) => {
      setStatus(b.status)
      // The SERVER's idea of who this is, which is the one that decides what the reply
      // contains. With AUTH_DISABLED the client invents a stand-in admin so the login
      // gate can be skipped locally; letting that placeholder stand meant the UI kept
      // admin-shaped rules (see visibleCourses) for whoever the server actually is.
      //
      // It MUST return the same object when nothing changed. The effect that calls
      // bootstrap is keyed on `user`, so handing back a fresh object every time is an
      // infinite loop — bootstrap -> setUser -> effect -> bootstrap — which hangs the
      // page rather than failing visibly.
      if (b.user?.email) {
        setUser((cur) => (cur && cur.email === b.user.email
                          && cur.is_admin === b.user.is_admin
                          ? cur
                          : { ...(cur || {}), ...b.user }))
      }
      if (b.status?.saved_links?.course) setCourseLink(b.status.saved_links.course)
      if (b.status?.settings?.course_type) setCourseType(b.status.settings.course_type)
      setCourseName(b.course || '')
      setCourses(b.courses || [])
      setMyTeams(b.workspaces?.teams || [])
      setCurRows(b.curriculum?.rows || [])
      setCurPending(b.curriculum?.pending || 0)
      setShowImport(!(b.curriculum?.rows || []).length && !(b.courses || []).length)
      setBudget(b.budget || null)
      setServerResumable(b.resumable || [])
      const list = b.sessions || []
      setSessions(list)
      setSel((cur) => (list.some((s) => s.number === cur) ? cur : (list[0]?.number ?? null)))
    }).catch(() => {})
  }

  // (status/settings arrive with the bootstrap above — this legacy loader is kept for
  // nothing and removed.)
  // status, settings and saved links all arrive with the bootstrap call above.



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
    api.submitFeedback(session_no, fbText, result?.course || courseName || undefined)
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
            // The course now exists in the agent: show its curriculum, put the form
            // away, and refresh the picker so the new course is in the list from here on.
            // A course created inside a TEAM workspace belongs to the team from the
            // moment it exists — otherwise the person who imported it would be the
            // only one able to see it, which is the failure this model exists to end.
            if (workspace.kind === 'team' && workspace.team_id && courseName) {
              api.teamAddCourse(workspace.team_id, courseName)
                 .catch(() => {}).finally(loadCourses)
            }
            loadCurriculum(); loadCourses()
            setShowImport(false); setNewCourse(false)
            // A NEW COURSE LANDS ON ITS OWN RULES. What a course is written under —
            // its skills and what its learners already know — belongs at the start,
            // before the first document is generated under rules nobody set. Every
            // one of them stays editable afterwards; this only decides where you
            // arrive, not when you may decide.
            setJustCreated(courseName)
            setTab('skills')
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
  // A FAILED REQUEST IS NOT A FAILED RUN, and this used to treat them as the same thing:
  // it cleared the poll interval on ANY error, and every poll routed its failure here.
  // So one transient answer from the host — a 429 while it throttles, a 502 while it
  // wakes — stopped the review panel polling for good. Everything after that silently
  // froze: a regeneration never appeared to finish, a chat answer written seconds later
  // never arrived, and the panel sat showing a state the server had long since moved on
  // from. The run itself was fine the whole time.
  //
  // Only an error that means the RUN is gone stops the loop now. `fatal` is passed by
  // callers that are not the poll — a click, whose failure is its own and should not
  // take the loop with it either, but which has nothing left to wait for.
  function handleGuidedError(e, { stopPolling = false } = {}) {
    if (stopPolling || e.kind === 'guided_gone') clearInterval(guidedPollRef.current)
    setGenErr(e.message)
    if (e.kind === 'guided_gone') {
      setGuidedId(null); setGuided(null); setRegenFor(null); setRegenReason('')
      rememberGuided(null)      // nothing left to resume
    }
  }

  function pollGuided(gid) {
    clearInterval(guidedPollRef.current)
    let pollMisses = 0
    const tick = async () => {
      try {
        const st = await api.guidedState(gid)
        setGuided(st)
        // The final-doc button stays in its loading state from the click until the run
        // has actually finished assembling — the status is still 'reviewing' for the
        // first poll or two, which is exactly the window the spinner is for.
        if (st.status !== 'assembling' && st.status !== 'reviewing') setFinalizing(false)
        if (st.status === 'reviewing') {
          // …unless an answer to a question is still being written. Status stays
          // 'reviewing' throughout — a question changes nothing, which is the point —
          // so stopping here would leave the reviewer watching a spinner that no poll
          // was ever going to clear.
          if (!st.chat_pending) clearInterval(guidedPollRef.current)
          // Back in review while we thought we were assembling means finalize failed and
          // left the run usable (see _guided_step_failed) — release the button.
          if (st.last_error) setFinalizing(false)
        }
        // A finished document can still be asked about, and polling stops on 'done'
        // below — so keep it alive while an answer is outstanding.
        else if (st.status === 'done' && st.chat_pending) { /* keep polling */ }
        else if (st.status === 'done') {
          clearInterval(guidedPollRef.current); setResult(st.result); rememberGuided(null)
        }
        else if (st.status === 'error') { clearInterval(guidedPollRef.current); setGenErr(st.error) }
        pollMisses = 0
      } catch (e) {
        // Ride out a run of bad answers rather than giving up on the first — and stay
        // quiet about it until it looks like more than a hiccup, so a single 429 does
        // not put a red box over a review that is going perfectly well.
        pollMisses += 1
        if (e.kind === 'guided_gone' || pollMisses >= 10) {
          handleGuidedError(e, { stopPolling: true })
        }
      }
    }
    guidedPollRef.current = setInterval(tick, 1500)
    tick()   // fetch immediately so the UI reacts without a 1.5s lag
  }

  function startGuided() {
    setResult(null); setGenErr(null); setGuided(null); setRegenFor(null); setRegenReason(''); setEvalReport(null); setEvalErr(null); setShowCost(true)
    setRegenAll(false); setSplitFor(null); setSplitSlide(''); setSplitErr(null); setFinalizing(false)
    api.guidedStart(sel, true, policy.time_always_enforced,
                    workspace.kind === 'team' ? workspace.team_id : null,
                    courseName || undefined).then(({ guided_id }) => {
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
  // Discard a run for good. Recorded on the SERVER: forgetting it only in this browser
  // meant the next page load asked the server for unfinished runs and was handed the
  // same one straight back, so the prompt kept reappearing with no way to dismiss it.
  function discardGuided(gid) {
    setServerResumable((rs) => rs.filter((r) => r.guided_id !== gid))
    if (gid === resumableGid) rememberGuided(null)
    api.guidedDiscard(gid).catch(() => {}).finally(refreshResumable)
  }

  // The open run belongs in the URL for as long as it is open, and must leave it the
  // moment it is not — a stale id in the address bar would try to resume a finished run
  // on the next reload.
  useEffect(() => { if (tab === 'generate') writeHash('generate', guidedId) }, [guidedId, tab])

  // …and a URL that names a run opens it, once, as soon as there is a user to open it
  // for. Cleared before the call, so a run that cannot be resumed produces one error
  // rather than an attempt on every render.
  useEffect(() => {
    const gid = hashGidRef.current
    if (!user || !gid || guidedId) return
    hashGidRef.current = null
    resumeGuided(gid)
  }, [user, guidedId])

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
      setGuidedId(gid); setGuided(st); setShowCost(true)
      if (st.status !== 'reviewing') pollGuided(gid)
    }).catch((e) => { setBusyAction(false); handleGuidedError(e) })
  }

  function approveChunk(i, approved = true) {
    if (!guidedId) return
    setBusyAction(true)
    // The reply is the whole view, so the tick and "can the doc be created yet" arrive
    // from the same place and cannot drift apart.
    api.guidedApproveChunk(guidedId, i, approved)
      .then(setGuided).catch(handleGuidedError).finally(() => setBusyAction(false))
  }

  // busyAction disables EVERY button in the review panel (approve, regenerate,
  // create-final), so it must never be left stuck on: a request that neither
  // resolves nor rejects would make the whole panel look unclickable. Clearing it in
  // finally() guarantees release on every path.
  function regenerateChunk(index) {
    const reason = regenReason.trim()
    if (!reason || !guidedId) return
    const all = regenAll
    setGenErr(null)          // don't leave a previous attempt's error on screen
    setBusyAction(true)
    api.guidedRegenerate(guidedId, index, reason, all).then(() => {
      setRegenFor(null); setRegenReason(''); setRegenAll(false)
      // The approvals of the rewritten chunks are dropped BY THE SERVER as each one is
      // replaced (see _unapprove) — an approval is of the text that was on screen, and
      // that text has changed. Polling brings the new list back.
      pollGuided(guidedId)   // resume polling to watch regenerating -> reviewing
    }).catch(handleGuidedError).finally(() => setBusyAction(false))
  }

  // A question. It cannot change the document, so it deliberately does NOT set
  // busyAction — the reviewer keeps every other control while the answer is written.
  // The reply is the whole view, with their question already on it, so what they typed
  // appears the instant they send it rather than after the model has answered.
  function askAboutChunk(index) {
    const q = (askText[index] || '').trim()
    if (!q || !guidedId) return
    setAsking(true)
    setAskText((m) => ({ ...m, [index]: '' }))
    // Asking pins this section open, so the answer cannot land behind a collapsed panel.
    setChatOpen((m) => ({ ...m, [index]: true }))
    api.guidedAsk(guidedId, index, q, askWeb)
      .then((st) => { setGuided(st); pollGuided(guidedId) })
      // The question goes back in the box on failure — it is theirs, and retyping it is
      // the one cost a failed request must never impose.
      .catch((e) => { setAskText((m) => ({ ...m, [index]: q })); handleGuidedError(e) })
      .finally(() => setAsking(false))
  }
  // Whether one section's chat is expanded: what the reader last chose, or — if they
  // have not chosen — open when there is something to read.
  function chatIsOpen(index) {
    const chosen = chatOpen[index]
    if (chosen !== undefined) return chosen
    return (guided?.chat || []).some((m) => m.index === index)
  }

  // Promote a standing preference the conversation settled into a DRAFT course skill.
  // Filed against the RUN's course, not the page's selected one — after resuming
  // somebody else's run those differ, and a rule landing on the wrong course is worse
  // than no rule. It goes through the ordinary skills path, so it is articulated and
  // then waits for approval under Skills; nothing about the course changes here.
  function makeSkillFromChat(msgId, text) {
    const course = guided?.course || courseName
    if (!course || !text) return
    setRulePosted((m) => ({ ...m, [msgId]: 'busy' }))
    api.addSkill(course, text)
      .then(() => {
        setRulePosted((m) => ({ ...m, [msgId]: 'done' }))
        refreshSkills()
      })
      .catch((e) => {
        setRulePosted((m) => { const n = { ...m }; delete n[msgId]; return n })
        handleGuidedError(e)
      })
  }

  // Splitting is deterministic and synchronous on the server — no model call, no polling.
  // It returns the whole updated view, because renumbering touches the later chunks too.
  function splitSlideIn(index, slideN) {
    if (!guidedId || !slideN) return
    setSplitErr(null); setBusyAction(true)
    api.guidedSplitSlide(guidedId, index, Number(slideN)).then((st) => {
      setGuided(st)
      setSplitFor(null); setSplitSlide('')
      // The split chunk's approval is dropped server-side; the later chunks were
      // renumbered, not rewritten, so theirs stand.
    }).catch((e) => setSplitErr(e.message)).finally(() => setBusyAction(false))
  }

  function finalizeGuided() {
    if (!guidedId) return
    setGenErr(null)
    setFinalizing(true)
    setBusyAction(true)
    api.guidedFinalize(guidedId).then(() => pollGuided(guidedId))  // watch assembling -> done
      .catch((e) => { setFinalizing(false); handleGuidedError(e) })
      .finally(() => setBusyAction(false))
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
    setGuidedId(null); setGuided(null); setRegenFor(null); setRegenReason('')
  }, [sel])

  useEffect(() => () => {
    syncPollRef.current && clearInterval(syncPollRef.current)
    guidedPollRef.current && clearInterval(guidedPollRef.current)
    evalPollRef.current && clearInterval(evalPollRef.current)
  }, [])

  const selSession = sessions.find((s) => s.number === sel)
  const gStatus = guided?.status
  // In a team workspace the course list narrows to that team's courses — that is what
  // "working in a team" means here, and it stops a doc being generated into a course
  // the team does not own.
  // `teams` is null until its fetch lands, so it MUST be guarded here: this runs on
  // the very first render and an unguarded .find() blanked the whole page.
  const activeTeam = (teams || []).find((x) => x.team.id === workspace.team_id)
  // THE INDIVIDUAL SHELF IS WHAT YOU MADE AND HAVE NOT SHARED. `courses` is the union of
  // both shelves, because the picker in a team workspace needs the team's entries too —
  // so the individual view narrows it by the shelf the SERVER assigned
  // (db.courses_for_user). Once a course is shared with a team you are on it belongs to
  // the team and is listed there only: it used to appear in both places at once, which is
  // not what "moved it to the team" means. An admin sees the lot.
  const visibleCourses = workspace.kind === 'team' && activeTeamInfo
    ? courses.filter((c) => (activeTeamInfo.courses || []).includes(c.name))
    : courses.filter((c) => c.shelf === 'individual')

  const guidedGenAll = gStatus === 'generating_all'
  const guidedReviewing = gStatus === 'reviewing' || gStatus === 'regenerating'
  const guidedAssembling = gStatus === 'assembling'
  const guidedActive = guided && gStatus !== 'done' && gStatus !== 'error'
  // The SERVER decides this: it is the condition for creating the document at all, and
  // finalize refuses without it, so the button must be reading the same answer.
  const allApproved = !!guided?.all_approved

  // --- Auth gate: block the whole app until a valid @nxtwave.co.in login ---
  if (!authCfg) return <div className="app"><p className="sub">Loading…</p></div>
  if (!user) return <LoginGate cfg={authCfg} onSignIn={onSignIn} err={authErr} />

  const courseCount = curRows.length
  const prereqCount = (prereqState?.prereqs || []).length
  const tabs = [
    { id: 'curriculum', icon: 'curriculum', label: 'Curriculum',
      badge: courseCount ? String(courseCount) : null },
    { id: 'generate', icon: 'generate', label: 'Generate' },
    { id: 'history', icon: 'history', label: 'History' },
    ...(workspace.kind === 'team' ? [{ id: 'team', icon: 'team', label: 'Team' }] : []),
    // WHAT THIS COURSE IS WRITTEN UNDER, and WHAT ITS LEARNERS ALREADY KNEW. Two
    // entries, not one: they were a single screen with the skills, the composer and the
    // whole prerequisites section stacked in one card, and it was the longest page in
    // the app. Separate from "Agent rules", which are the rules the agent INFERRED from
    // corrections across every course; these were authored for this one.
    { id: 'skills', icon: 'skills', label: 'Skills',
      badge: approvedSkills ? String(approvedSkills) : null },
    { id: 'prereqs', icon: 'curriculum', label: 'Prerequisites',
      badge: prereqCount ? String(prereqCount) : null },
    { id: 'rules', icon: 'brain', label: 'Agent rules' },
    { id: 'settings', icon: 'settings', label: 'Settings' },
  ]

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="bmark" aria-hidden="true"><Icon name="doc" size={17} /></span>
          <b>TR Doc Generator</b>
        </div>
        {/* No provider/model/version chips: which LLM runs the agent is an internal
            detail that changes over time, and the user has no decision to make on it.
            A missing API key is surfaced where it blocks you — next to Generate. */}
        <div className="userbox">
          {user.picture && <img className="avatar" src={user.picture} alt="" referrerPolicy="no-referrer" />}
          <span className="uemail">{user.email}{user.is_admin && <span className="pill admin">admin</span>}</span>
          <button className="link" onClick={signOut}>Sign out</button>
        </div>
      </header>

      {/* THE SHELL. Everything used to be one column of stacked cards — curriculum,
          generate, result, history, teams — so the page grew without end and finding
          anything meant scrolling past everything else. Now: a fixed left rail that
          answers "where am I working and on what", and one focused view at a time. */}
      <div className="shell">
        <aside className="nav">
          <div className="navsec">
            <div className="navlabel">Workspace</div>
            {/* Individual vs team. A team workspace is what makes a course and its whole
                history shared: everything generated in it belongs to the team, so a
                member added later opens the same curriculum and sees the work already
                done. */}
            <button className={`wsopt ${workspace.kind === 'individual' ? 'on' : ''}`}
                    onClick={() => switchWorkspace({ kind: 'individual' })}>
              <span className="wsicon"><Icon name="person" /></span>
              <span className="wsbody"><b>Individual</b><span>Just my own docs</span></span>
            </button>
            {myTeams.map((t) => (
              <button key={t.id}
                      className={`wsopt ${workspace.kind === 'team' && workspace.team_id === t.id ? 'on' : ''}`}
                      onClick={() => switchWorkspace({ kind: 'team', team_id: t.id })}>
                <span className="wsicon"><Icon name="team" /></span>
                <span className="wsbody"><b>{t.name}</b>
                  <span>{t.members.length} member{t.members.length === 1 ? '' : 's'} · {t.courses.length} course{t.courses.length === 1 ? '' : 's'}</span>
                </span>
              </button>
            ))}
            {myTeams.length === 0 && (
              <div className="navnote">
                You are not on a team yet. An admin can create one in <b>/admin</b> so a
                course and its history are shared.
              </div>
            )}
          </div>

          <div className="navsec">
            <div className="navlabel">Course</div>
            <select className="navselect" value={courseName}
                    onChange={(e) => switchCourse(e.target.value)}>
              {visibleCourses.length === 0 && <option value="">No course yet</option>}
              {/* The course currently LOADED, when it is not on this workspace's shelf —
                  a team course while you are in your individual workspace, say. It has to
                  be here or the select would render blank against a course that is
                  plainly open, but it is labelled so it reads as "you are working on
                  something that lives elsewhere" rather than as a second copy of it. */}
              {!visibleCourses.some((c) => c.name === courseName) && courseName && (
                <option value={courseName}>
                  {courseName} — open, {workspace.kind === 'team' ? 'not this team’s' : 'shared with a team'}
                </option>
              )}
              {visibleCourses.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name} ({c.sessions})
                </option>
              ))}
            </select>
            <button className="navlink" onClick={startNewCourse}>
              <Icon name="plus" size={14} />Create new course
            </button>
          </div>

          <nav className="navsec navtabs">
            <div className="navlabel">Sections</div>
            {tabs.map((t) => (
              <button key={t.id} className={`navtab ${tab === t.id ? 'on' : ''}`}
                      onClick={() => setTab(t.id)}>
                <Icon name={t.icon} className="tabicon" />
                <span className="tablabel">{t.label}</span>
                {t.badge && <span className="tabbadge">{t.badge}</span>}
              </button>
            ))}
          </nav>

          <div className="navsec">
            <button className="navlink" onClick={() => { setShowSkillHelp(false); loadGuide() }}>
              <Icon name="doc" size={14} />Sheet template
            </button>
            <button className="navlink"
                    onClick={() => { setShowGuide(false); setShowGaps(false)
                                     setShowSkillHelp((v) => !v) }}>
              <Icon name="skills" size={14} />How skills work
            </button>
            {syncOut?.extraction_warnings?.length > 0 && (
              <button className="navlink" onClick={() => { setShowGaps((v) => !v); setShowGuide(false) }}>
                <Icon name="search" size={14} />Extraction gaps ({syncOut.extraction_warnings.length})
              </button>
            )}
          </div>
        </aside>

        <main className="main">
      {/* WHERE YOU ARE, in one line, above whatever section is open. The rail holds the
          controls; this says what they currently add up to — which workspace, which
          course, and (in a team) who else is in it. In a team with more than one course
          the alternatives are one click away, so choosing the team and then the course
          reads as the two steps it actually is. */}
      <div className="context">
        <span className="ctxpart">
          <span className="ctxlabel">{workspace.kind === 'team' ? 'Team' : 'Working'}</span>
          <b>{workspace.kind === 'team' ? (activeTeamInfo?.name || 'Team') : 'Individual'}</b>
        </span>
        <span className="ctxsep">›</span>
        <span className="ctxpart">
          <span className="ctxlabel">Course</span>
          <b>{courseName || 'none yet'}</b>
        </span>
        {workspace.kind === 'team' && (activeTeamInfo?.courses?.length || 0) > 1 && (
          <span className="ctxswitch">
            {activeTeamInfo.courses.map((c) => (
              <button key={c} className={`coursechip ${c === courseName ? 'on' : ''}`}
                      onClick={() => switchCourse(c)}>{c}</button>
            ))}
          </span>
        )}
        {workspace.kind === 'team' && activeTeamInfo?.members?.length > 0 && (
          <span className="ctxpart right">
            <span className="ctxlabel">Shared with</span>
            <b>{activeTeamInfo.members.length} member{activeTeamInfo.members.length === 1 ? '' : 's'}</b>
          </span>
        )}
      </div>

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

      {showGuide && <TemplateSidePanel markdown={guide} onClose={() => setShowGuide(false)} />}
      {showSkillHelp && <SkillsHelpPanel onClose={() => setShowSkillHelp(false)} />}
      {showGaps && syncOut?.extraction_warnings?.length > 0 && (
        <GapsSidePanel warnings={syncOut.extraction_warnings} onClose={() => setShowGaps(false)} />
      )}

      {/* THE CURRICULUM the agent holds. Shown first because it IS the course now —
          the sheet below is only how a course gets in the first time. */}
      {/* The dashboard shows whenever a course is open — including an EMPTY one, so a
          course created by hand can have its first session added here rather than
          forcing a sheet import. */}
      {tab === 'curriculum' && (courseName || curRows.length > 0) && (
        <CurriculumDashboard
          course={courseName} rows={curRows} setRows={setCurRows}
          onSave={saveCurriculum} onDelete={deleteCurriculumRow} onIngest={ingestDecks}
          onInsert={insertCurriculumRow}
          saving={curSaving} ingesting={curIngesting} dirty={curDirty}
          pending={curPending} logs={curLogs}
          budget={budget} onBudget={saveBudget}
          teams={myTeams} sharing={sharing} onShare={shareCourseWithTeam}
        />
      )}

      {/* CREATE A NEW COURSE. Two ways in, and only two: pick a course the team already
          has from the picker above, or set one up here. Everything below — the
          curriculum, the generate panel — belongs to whichever course is selected, so
          this comes first and nothing else shows until a course exists.
          `newCourse` distinguishes the two jobs this card does: creating a course that
          did not exist, and re-importing the sheet into the one already open. */}
      {/* The create/import card is a deliberate act, not a greeting: it appears when
          asked for, or when the agent holds no course at all and there is nothing else
          the user could possibly do first. */}
      {tab === 'curriculum' && (showImport || courses.length === 0) && (
      <section className="card">
        <h2><span className="hicon"><Icon name={newCourse ? 'plus' : 'history'} /></span>{' '}
          {newCourse ? 'Create a new course' : `Re-import ${courseName || 'this course'} from its sheet`}</h2>
        <p className="hint">
          {newCourse
            ? 'Name the course and point it at its curriculum sheet. It is imported once, '
              + 'then edited here in the agent — no sheet needed again. A course you create '
              + 'is YOURS: it appears in your course picker and nobody else\'s until you '
              + 'share it with a team.'
            : 'Refreshes names, takeaways and links from the sheet. Rows you added in the '
              + 'agent are kept, and no already-extracted deck is downloaded again.'}
        </p>
        <div className="settingsrow">
          <div className="settingcol">
            <label>Course name</label>
            <input value={courseName} onChange={(e) => setCourseName(e.target.value)}
                   disabled={!newCourse}
                   placeholder="e.g. Computer Networks" />
            <span className="hint">
              {newCourse
                ? 'Pick the real course name. It is what you will see in the picker, and what '
                  + 'a team sees if you share it — a near-miss spelling makes a second, separate course.'
                : 'Locked: re-importing writes into the course you have open. Use “Create a new course” for a different one.'}
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
        {/* The sheet is an IMPORT FORMAT, not a dependency: it seeds the course and is
            then out of the loop. */}
        <label>Curriculum sheet link</label>
        <input value={courseLink} onChange={(e) => setCourseLink(e.target.value)}
               placeholder="https://docs.google.com/spreadsheets/d/.../edit" />
        <span className="hint">
          One sheet, shared as “Anyone with the link → Viewer”. Its <b>PPT Links</b> column
          holds each recorded session's Google Slides deck — leave that cell blank for a
          session not recorded yet.
        </span>
        <div className="curactions">
          <button className="primary" disabled={!courseLink || !courseName || syncing} onClick={doSync}>
            {syncing ? 'Importing…'
            : <><Icon name={newCourse ? 'plus' : 'history'} />{newCourse ? 'Create course' : 'Re-import'}</>}
          </button>
          {curRows.length > 0 && (
            <button className="ghostbtn" disabled={syncing}
                    onClick={() => {
                      // Put back whatever was open before the create form was raised.
                      setShowImport(false); setNewCourse(false)
                      loadCourses(); loadCurriculum()
                    }}>
              Cancel
            </button>
          )}
        </div>

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
                <Icon name="search" size={14} />{showGaps ? 'Hide' : 'Show'} deck extraction gaps ({syncOut.extraction_warnings.length})
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
      {tab === 'generate' && sessions.length === 0 && (
        <section className="card">
          <h2><span className="hicon"><Icon name="generate" /></span> Generate a TR doc</h2>
          <p className="hint">
            Every session in <b>{courseName || 'this course'}</b> already has a deck, or
            the course has no sessions yet. Add a session in <b>Curriculum</b> — a row
            with no PPT link is a session that still needs a TR doc.
          </p>
        </section>
      )}
      {tab === 'generate' && sessions.length > 0 && (
        <section className="card">
          <h2><span className="hicon"><Icon name="generate" /></span> Generate a TR doc</h2>
          <label>Session</label>
          {/* LOCKED while a run is in flight. A run's session is fixed the moment it
              starts, so leaving this editable let the picker drift away from the work
              actually happening: start a run for 31, change this to 32 while it
              generates, and the screen says 32 while every chunk — and the finished
              document — is 31's. That is the "I selected 32 and got 31" report. */}
          <select value={sel ?? ''} disabled={!!guidedActive}
                  onChange={(e) => setSel(Number(e.target.value))}>
            {sessions.map((s) => <option key={s.number} value={s.number}>{s.number} — {s.name}</option>)}
          </select>
          {guidedActive && (
            <span className="hint">
              Locked while this run is in progress — it is generating session{' '}
              <b>{guided?.session_no ?? sel}</b>. Finish or discard it to pick another.
            </span>
          )}
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
                  <Icon name="generate" /> Generate all chunks
                </button>
              )}
              {/* Unfinished runs. Every chunk is checkpointed as it is generated, so
                  nothing already paid for is lost when a run is abandoned, the browser
                  reloads, or the server restarts mid-review. The list comes from the
                  SERVER (this user's runs, any browser); the localStorage id is kept as
                  a fallback for a run the server list has not caught up with. */}
              {!guidedId && (serverResumable.length > 0 || resumableGid) && (
                <div className="resumebox">
                  <b><Icon name="history" size={14} /> Unfinished TR doc{serverResumable.length > 1 ? 's' : ''}</b>
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
                                onClick={() => resumeGuided(r.guided_id)}>
                          <Icon name="refresh" size={14} /> Resume</button>
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
                                onClick={() => resumeGuided(resumableGid)}>
                          <Icon name="refresh" size={14} /> Resume</button>
                        <button className="ghostbtn" onClick={() => discardGuided(resumableGid)}>
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

              {/* WHICH SESSION THIS RUN IS FOR, stated on the panel itself.
                  A run carries its own session — it is fixed when the run starts and a
                  resumed run brings its own — so the picker above and the work below can
                  legitimately disagree (resume an abandoned run for session 31 while the
                  picker sits on 32, and every chunk you review is 31's). Nothing said so,
                  which is exactly how a document comes out for a session you did not
                  think you asked for. Now the panel names its session, and says plainly
                  when it differs from the one selected. */}
              {guided && guided.session_no != null && (
                <div className={`runhead ${guided.session_no !== sel ? 'mismatch' : ''}`}>
                  <span>Generating <b>Session {guided.session_no}
                    {guided.session_title ? ` — ${guided.session_title}` : ''}</b></span>
                  {guided.session_no !== sel && (
                    <span className="runwarn">
                      The picker above shows session {sel}. This run was started for
                      session {guided.session_no} and will produce that document —
                      discard it and start again if that is not what you want.
                    </span>
                  )}
                </div>
              )}

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
                  {/* A standing instruction governs every later redraft, so it is shown
                      rather than left as invisible state. */}
                  {guided.standing_notes?.length > 0 && (
                    <div className="alert warn">
                      <b>Standing review instructions</b> — applied to every chunk after
                      the one they were given on, including any regenerated later:
                      <ul>{guided.standing_notes.map((n, k) => (
                        <li key={k}>from chunk {Number(n.from_index) + 1} onward: {n.reason}</li>
                      ))}</ul>
                    </div>
                  )}
                  {guidedReviewing && (
                    /* HOW FAR THROUGH THE REVIEW YOU ARE. This was a sentence with
                       "· 3/5 approved" appended, which is the one number a reviewer
                       checks constantly and the hardest shape to check it in. */
                    <div className="gprogress">
                      <div className="gprow">
                        <b>Review each chunk</b>
                        <span className="gcount">
                          {approvedSet.size} of {guided.chunks.length} approved
                        </span>
                      </div>
                      <div className="gbar" role="progressbar"
                           aria-valuenow={approvedSet.size} aria-valuemin={0}
                           aria-valuemax={guided.chunks.length}>
                        <span style={{ width: `${guided.chunks.length
                          ? Math.round(100 * approvedSet.size / guided.chunks.length) : 0}%` }} />
                      </div>
                      <span className="hint tight">
                        Approve what is right, or <b>Regenerate</b> with a reason — the
                        reason also teaches the agent for later sessions.
                      </span>
                    </div>
                  )}
                  {/* A question about the DOCUMENT, not a section. It sits above them
                      because that is what it is about, and because the questions it
                      answers — why is this topic here and not there, does the whole
                      thing hang together — are the ones you cannot ask from inside a
                      single section. Same read-only guarantee. */}
                  <ChunkChat
                    scope="document"
                    messages={(guided.chat || []).filter((m) => m.index === -1)}
                    pending={guided.chat_pending}
                    open={chatIsOpen(-1)}
                    text={askText[-1] || ''}
                    web={askWeb}
                    asking={asking}
                    onOpen={() => setChatOpen((m) => ({ ...m, [-1]: true }))}
                    onClose={() => setChatOpen((m) => ({ ...m, [-1]: false }))}
                    onText={(v) => setAskText((m) => ({ ...m, [-1]: v }))}
                    onWeb={setAskWeb}
                    onSend={() => askAboutChunk(-1)}
                    /* A document-level conclusion has no single section to regenerate,
                       so it offers the course-skill route only — the reviewer picks the
                       section themselves if a fix is what they want. */
                    canRegen={false}
                    onUseAsFeedback={() => {}}
                    rulePosted={rulePosted}
                    onMakeSkill={makeSkillFromChat}
                    stage={guided.chat_stage?.index === -1 ? guided.chat_stage : null} />
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
                    <div className="ok-note"><Icon name="check" /> Final doc created — see the result below. Chunks kept here for reference.</div>
                  )}
                  {guided.chunks.map((c, i) => {
                    const regenning = gStatus === 'regenerating' && guided.regen_index === i
                    const isOk = approvedSet.has(i)
                    return (
                      <details key={i} className={`review-chunk ${isOk ? 'ok' : ''}`} open={gStatus !== 'done'}>
                        <summary>{isOk ? <Icon name="check" className="okmark" /> : <span className="cnum">{i + 1}</span>} {c.label}</summary>
                        {regenning
                          ? <Busy label="Regenerating this chunk…" />
                          : <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{c.markdown}</ReactMarkdown></div>}
                        {/* ASK BEFORE YOU DECIDE. Regenerating is the lever for a
                            section you have decided is wrong; this is for the moment
                            before that, when you cannot yet tell whether it is. It is
                            read-only — it cannot edit, regenerate or approve — so a
                            question can never cost you work you had already accepted.
                            Available on a finished document too: that is when people
                            most often go back and ask. */}
                        {!regenning && (
                          <ChunkChat
                            messages={(guided.chat || []).filter((m) => m.index === i)}
                            pending={guided.chat_pending}
                            open={chatIsOpen(i)}
                            text={askText[i] || ''}
                            web={askWeb}
                            asking={asking}
                            onOpen={() => setChatOpen((m) => ({ ...m, [i]: true }))}
                            onClose={() => setChatOpen((m) => ({ ...m, [i]: false }))}
                            onText={(v) => setAskText((m) => ({ ...m, [i]: v }))}
                            onWeb={setAskWeb}
                            onSend={() => askAboutChunk(i)}
                            /* The one bridge to the existing lever: if the agent
                               concludes the document should change, its suggestion
                               fills the regenerate box rather than being applied. The
                               reviewer still reads it, edits it and presses the
                               button — nothing moves without them. */
                            canRegen={guidedReviewing}
                            onUseAsFeedback={(t, toFollowing) => {
                              setRegenFor(i); setRegenReason(t)
                              setRegenAll(Boolean(toFollowing))
                              setAskFor(null)
                            }}
                            rulePosted={rulePosted}
                            onMakeSkill={makeSkillFromChat}
                            stage={guided.chat_stage?.index === i ? guided.chat_stage : null} />
                        )}
                        {/* Shown BEFORE the Approve button, because this is the cheap
                            moment to fix it: regenerating one section costs a fraction
                            of a repair pass over the assembled document, and these same
                            bullets will fail the run at finalize otherwise. */}
                        {!regenning && c.repetition?.length > 0 && (
                          <div className="alert warn">
                            <b><Icon name="warn" /> {c.repetition.length} bullet(s) repeat the paragraph above them</b>
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
                                ? <>
                                    <span className="approved-badge"><Icon name="check" size={13} /> Approved</span>
                                    <button className="ghostbtn" disabled={busyAction}
                                            title="Un-approve this chunk"
                                            onClick={() => approveChunk(i, false)}>
                                      <Icon name="refresh" size={13} /> Undo</button>
                                  </>
                                : <button className="primary" disabled={busyAction} onClick={() => approveChunk(i)}><Icon name="check" /> Approve</button>}
                              {regenFor !== i && (
                                <button className="ghostbtn" disabled={busyAction || gStatus === 'regenerating'}
                                        onClick={() => { setRegenFor(i); setRegenReason(''); setRegenAll(false) }}>
                                          <Icon name="refresh" size={14} /> Regenerate…</button>
                              )}
                              {/* Splitting is a STRUCTURAL edit, not a rewrite: the
                                  slide's content is divided between two slides with no
                                  model call, so nothing the reviewer already accepted can
                                  drift. Offered only where there are slides to split. */}
                              {splitFor !== i && c.slides?.length > 0 && (
                                <button className="ghostbtn" disabled={busyAction || gStatus === 'regenerating'}
                                        onClick={() => { setSplitFor(i); setSplitSlide(''); setSplitErr(null) }}>
                                  <Icon name="scissors" size={14} /> Split a slide…
                                </button>
                              )}
                            </div>
                            {splitFor === i && (
                              <div className="regen">
                                <label>Which slide is carrying too much?
                                  <span className="req"> (it becomes two — content divided, not rewritten)</span>
                                </label>
                                <select value={splitSlide} disabled={busyAction}
                                        onChange={(e) => setSplitSlide(e.target.value)}>
                                  <option value="">choose a slide…</option>
                                  {c.slides.map((sl) => (
                                    <option key={sl.n} value={sl.n}>Slide {sl.n} — {sl.title}</option>
                                  ))}
                                </select>
                                <span className="hint">
                                  Every slide after it is renumbered automatically, in this
                                  chunk and in all the later ones. The second slide keeps
                                  the first one's heading, visual guidance and speaker
                                  notes — regenerate it afterwards if that wording does not
                                  fit.
                                </span>
                                {splitErr && <div className="alert error">{splitErr}</div>}
                                <div className="gactions">
                                  <button className="primary" disabled={busyAction || !splitSlide}
                                          onClick={() => splitSlideIn(i, splitSlide)}>
                                    {busyAction ? 'Splitting…' : 'Split into 2 slides'}
                                  </button>
                                  <button className="ghostbtn" disabled={busyAction}
                                          onClick={() => { setSplitFor(null); setSplitSlide(''); setSplitErr(null) }}>Cancel</button>
                                </div>
                              </div>
                            )}
                            {regenFor === i && (
                              <div className="regen">
                                <label>Why regenerate? <span className="req">(required — instructs the model & is remembered)</span></label>
                                <textarea rows={3} value={regenReason} onChange={(e) => setRegenReason(e.target.value)}
                                          placeholder="e.g. Make the analogy concrete, and shorten this to ~9 minutes." />
                                {/* Most reviewer notes are about the DOCUMENT, not this
                                    one chunk. Ticking this rewrites every chunk after
                                    this one with the same note, and keeps it as a
                                    standing instruction so a later redraft of any of
                                    them still obeys it. */}
                                {i < guided.chunks.length - 1 && (
                                  <label className="checkline">
                                    <input type="checkbox" checked={regenAll}
                                           disabled={busyAction}
                                           onChange={(e) => setRegenAll(e.target.checked)} />
                                    <span>Apply this to every chunk after this one too
                                      <span className="hint"> — rewrites the remaining
                                        {' '}{guided.chunks.length - 1 - i} chunk(s) with the
                                        same note, and keeps applying it if any of them is
                                        regenerated later. Their approvals are cleared.</span>
                                    </span>
                                  </label>
                                )}
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
                      {/* It says it is working from the moment it is pressed. The status
                          is still 'reviewing' until the next poll lands, and assembling
                          takes long enough that a button which merely greys out reads as
                          a click that did nothing. */}
                      <button className="primary bigfinal"
                              disabled={busyAction || finalizing || gStatus === 'regenerating' || !allApproved}
                              onClick={finalizeGuided}>
                        {finalizing
                          ? <><span className="spinner" aria-hidden="true" /> Creating the final doc…</>
                          : <><Icon name="doc" /> Create final TR Doc</>}
                      </button>
                      {finalizing && <div className="hint">Assembling, grading and rendering — this takes a minute or two.</div>}
                      {!allApproved && !finalizing && <div className="hint">Approve every chunk to enable creating the final doc.</div>}
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

      {tab === 'generate' && result && (
        <section className="card">
          <h2><span className="hicon"><Icon name="check" /></span> Result</h2>
          <div className="metrics">
            <Metric label="Accepted" value={result.accepted ? 'Yes' : 'Review'} />
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
            <button className="primary download" onClick={() => api.downloadDoc(result.session_no, result.run_id, result.docx_name).catch((e) => setDlErr(e.message))}><Icon name="download" /> Download Word (.docx)</button>
            <button className="ghostbtn" disabled={gdocBusy} onClick={() => createGoogleDoc(result.session_no, result.run_id, result.docx_name)}>
              {gdocBusy ? 'Creating Google Doc…' : <><Icon name="doc" /> Create Google Doc</>}
            </button>
            {/* Last-resort escape hatch. A reviewer once had BOTH the download and the
                Google Doc fail on a finished document and copied it out of the preview
                by hand. Both paths are fixed, but the copy button stays: no one should
                ever be one broken button away from losing an hour of review. */}
            <button className="ghostbtn" onClick={() => copyMarkdown(result)}>
              {copied ? <><Icon name="check" /> Copied</> : <><Icon name="doc" /> Copy full text</>}
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
              <Icon name="link" size={14} /> Open in Google Docs — you have edit access
            </a>
          )}

          {/* Teach the agent from the finished document: the
              note is distilled into a durable rule injected into every future doc for
              this course, and the distilled text is shown back so a bad distillation
              can be spotted and deleted rather than silently applied for months. */}
          <details className="panel feedback">
            <summary><Icon name="brain" /> Teach the agent — what should change in future docs?</summary>
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
                {evalRunning ? 'Running…' : <><Icon name="beaker" /> Run eval sets</>}
              </button>
            </div>
            {evalRunning && <Busy label="Scoring against the eval sets… (deterministic + LLM, ~1–2 min)" />}
            {evalErr && <div className="alert error"><pre>{evalErr}</pre></div>}
            {evalReport && <EvalReport report={evalReport} />}
          </div>

          {result.cost?.calls?.length > 0 && <CostBreakdown cost={result.cost} />}

          {/* The learned-rule list lives in its own "Agent rules" section now: it is
              about the AGENT across every future doc, not about the one just produced,
              and appended here it made an already-long result page longer. */}

          {result.markdown && (
            <details className="panel preview" open>
              <summary>Preview the TR doc</summary>
              <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{result.markdown}</ReactMarkdown></div>
            </details>
          )}
        </section>
      )}

      {/* HISTORY. In a TEAM workspace this is the team's shelf — every doc anyone on
          the team has produced for its courses, so someone added last week can see what
          was built last month. Individually it is just this user's own. */}
      {tab === 'history' && (
        workspace.kind === 'team'
          ? (activeTeam
              ? <MyTeams teams={(teams || []).filter((x) => x.team.id === workspace.team_id)} />
              : <section className="card"><p className="hint">That team is no longer available.</p></section>)
          : (history?.courses?.length > 0
              ? <MyHistory history={history} />
              : <section className="card"><h2><span className="hicon"><Icon name="history" /></span> History</h2>
                  <div className="emptystate">
                    <span className="eicon"><Icon name="history" size={20} /></span>
                    <b>No documents yet</b>
                    <p>Every TR doc you finish is kept here with its grade, its cost and
                       the file itself — including the ones your team finished.</p>
                  </div>
                </section>)
      )}

      {/* The team's own page. `activeTeam` carries the run history and arrives with
          /api/my/teams; `activeTeamInfo` is the lighter workspace record and is there
          immediately — so the panel renders from whichever has landed rather than
          showing nothing while the heavier call is still in flight. */}
      {tab === 'team' && (activeTeam || activeTeamInfo) && (
        <TeamPanel
          entry={activeTeam || { team: activeTeamInfo, summary: {}, contributors: [] }}
          courses={courses} course={courseName} onPick={switchCourse}
          onAddMember={addTeamMember} onRemoveMember={removeTeamMember}
          memberBusy={memberBusy} memberMsg={memberMsg} />
      )}

      {/* ONE COMPONENT, TWO VIEWS — the props are identical and every handler is
          shared, so splitting the screen in two did not mean splitting the state that
          drives it. */}
      {(tab === 'skills' || tab === 'prereqs') && (
        <CourseRules view={tab} onHelp={() => { setShowSkillHelp(true); setShowGuide(false); setShowGaps(false) }}
          course={courseName} skills={skillState} prereqs={prereqState}
          busy={skillBusy} job={prereqJob} msg={skillMsg}
          onClearMsg={() => setSkillMsg(null)}
          courses={courses} justCreated={justCreated}
          onDismissNew={() => setJustCreated(null)}
          onAdd={(text, where) => runSkillAction(
            () => api.addSkill(courseName, text, null, where),
            'Written up as a draft — check it against your own words below, edit it if it '
            + 'says more or less than you meant, and approve it when it is right. '
            + 'It does not affect anything until you do.')}
          onFromRequirements={(text, where) => runSkillAction(
            () => api.skillsFromRequirements(courseName, text, where),
            'Drafted from your requirements. Approve the ones you want — each shows the words it came from.')}
          onImport={(from) => runSkillAction(
            () => api.importSkills(courseName, from),
            `Imported from ${from} as drafts. They apply once approved here.`)}
          onApprove={(id) => runSkillAction(
            () => api.approveSkill(courseName, id),
            'Approved — it applies from the next generation.')}
          onEdit={(id, text, instructions) => runSkillAction(
            () => api.editSkill(courseName, id, text, instructions),
            'Edited. It is back to draft — an approval is of the words that were approved.')}
          onRetire={(id) => runSkillAction(
            () => api.retireSkill(courseName, id),
            'Retired. It is kept on the record, so an old doc can still be explained.')}
          onAddPrereq={(name) => runSkillAction(
            () => api.addPrereq(courseName, name),
            `${name} is now assumed knowledge — its decks are read in full, and nothing it taught will be re-taught.`)}
          onAddExternalPrereq={(name, links) => runSkillAction(
            () => api.addExternalPrereq(courseName, name, links).then((r) => {
              // Fetching the decks runs in the background, like a sync. Follow the job so
              // the count of indexed topics is real rather than optimistic.
              if (r?.job_id) pollJob(r.job_id, refreshPrereqs)
              return r
            }),
            `${name} added. Reading its decks now — its topics become assumed knowledge as they land.`)}
          onRemovePrereq={(name) => runSkillAction(
            () => api.removePrereq(courseName, name), `${name} is no longer a prerequisite.`)} />
      )}

      {tab === 'settings' && (
        <CourseSettings course={courseName} budget={budget} onChange={saveBudget}
          canDelete={!!courseName && (user?.is_admin
                     || courses.find((c) => c.name === courseName)?.mine)}
          sharedWith={(courses.find((c) => c.name === courseName)?.teams) || []}
          onAskDelete={askDeleteCourse} onDelete={doDeleteCourse}
          deleteAsk={deleteAsk} onCancelDelete={() => setDeleteAsk(null)}
          deleting={deleting}
                        rows={curRows} onSession={saveSessionBudget}
                        courseType={courseType}
                        onCourseType={(v) => { setCourseType(v); api.selectCourse(courseName, v).catch(() => {}) }} />
      )}

      {/* rules defaults to [] — LearnedRules filters the list on render, so passing the
          null it holds before the first fetch would throw. */}
      {tab === 'rules' && (
        <LearnedRules rules={learned || []} sessionNo={result?.session_no ?? sel}
                      course={learnedCourse} isAdmin={user.is_admin}
                      onChanged={refreshLearned} standalone />
      )}
        </main>
      </div>
    </div>
  )
}

// Per-course settings: how long its documents may be, and how the course is taught.
// Set once per course and then left alone, which is why it lives here rather than in
// the curriculum's action bar — where it crowded out the buttons you press every visit.
// WHAT THIS COURSE IS WRITTEN UNDER: instructions authored for it, and what its
// learners already knew when they arrived.
//
// Deliberately apart from "Agent rules", which are rules the agent INFERRED from
// corrections across every course. These were written for this one, on purpose, and
// nothing here affects a document until a person approves it — which is why a draft is
// visibly a draft rather than just an un-highlighted row.
// WHAT A SKILL GOVERNS, in the order a writer needs it — the same four the agent uses
// (src/skills.py CATEGORIES).
//
// The `hint` is NOT printed beside every group any more. Four category headers each
// carrying a line of explanation, above four empty slots each carrying another, meant a
// course that had written nothing showed eight sentences of instruction and nothing
// else — and the box you actually came to type in was below all of it. The hints live
// in the "How skills work" panel now, and on the chips in the composer where you are
// choosing between them and the difference matters.
const SKILL_CATEGORIES = [
  ['teaching_flow', 'Teaching flow',
   'the order concepts are taught in — how a session opens, where examples land, how it closes',
   'flow'],
  ['teaching_guidelines', 'Teaching guidelines',
   'how the content is explained — depth, pedagogy, what to emphasise, what to avoid',
   'book'],
  ['examples_visuals', 'Examples & visuals',
   'the kinds of example and diagram this course uses, and when not to use one',
   'image'],
  ['reviewer', 'Reviewer corrections',
   'a mistake review keeps sending back on this course — it outranks the rest',
   'flag'],
]
const SKILL_CATEGORY_LABEL = Object.fromEntries(
  SKILL_CATEGORIES.map(([id, label]) => [id, label]))
const SKILL_CATEGORY_ICON = Object.fromEntries(
  SKILL_CATEGORIES.map(([id, , , icon]) => [id, icon]))
// The groups the brief is laid out in: the four categories, then one slot for skills
// written before categories existed.
const SKILL_GROUPS = [
  ...SKILL_CATEGORIES.map(([id, label, hint, icon]) => ({ id, label, hint, icon })),
  { id: '', label: 'Other skills', icon: 'skills',
    hint: 'written before this course sorted its skills into the four above' },
]

/* HOW SKILLS WORK — the explanation, out of the way of the work.
 *
 * All of this used to be printed down the page: a paragraph under the title, a line
 * under every category header, a line inside every empty category, a sentence under the
 * composer. On a course with no skills yet that was the entire screen — instructions
 * with nothing to act on, and the one control that mattered pushed off the bottom.
 * It is one click away instead, in the same docked panel the sheet templates use.
 */
function SkillsHelpPanel({ onClose }) {
  return (
    <aside className="tmplside" aria-label="How skills work">
      <div className="tsidehead">
        <span className="tsidetitle"><Icon name="skills" size={14} /> How skills work</span>
        <button className="csideclose" onClick={onClose} title="Hide">×</button>
      </div>
      <div className="tsidebody helpbody">
        <p>
          <b>The curriculum decides what is taught.</b> The prerequisite courses decide
          what the learner already knows. <b>A skill decides how it is taught</b> — the
          sequence, the depth, the examples, the words.
        </p>
        <p>
          A skill never becomes content. Nothing you write here appears in the document
          as an agenda item, a takeaway or a bullet; it shapes how the document is
          written, and the agent fails its own run if any of it leaks onto a slide.
        </p>
        <h4>The four kinds</h4>
        <dl>
          {SKILL_CATEGORIES.map(([id, label, hint, icon]) => (
            <div key={id}>
              <dt><Icon name={icon} size={13} /> {label}</dt>
              <dd>{hint}</dd>
            </div>
          ))}
        </dl>
        <h4>One skill, several instructions</h4>
        <p>
          Four related lines under one heading are <b>one skill with four
          instructions</b>, in the order you wrote them — not four skills. The order is
          part of what you said, and for a teaching flow it <i>is</i> what you said.
        </p>
        <h4>Where a skill applies</h4>
        <dl>
          <div><dt>All of this course</dt><dd>the standing brief for every session.</dd></div>
          <div><dt>One session</dt><dd>that session and nowhere else.</dd></div>
        </dl>
        <p>
          There is no <b>every course</b> scope. A rule that applies to every course
          belongs in the repo — <code>harness/system_prompt.md</code> and
          <code>harness/style_guide.md</code> are read on every generation, for every
          course, and are reviewed and versioned like the code beside them.
        </p>
        <h4>Which one wins</h4>
        <p className="pcopy">
          Hard rules › Reviewer corrections › Session › Course. Narrower wins: a
          correction made about this course beats a rule written for one of its
          sessions, which beats the course's standing brief.
        </p>
        <h4>Nothing applies until you approve it</h4>
        <p>
          Every skill arrives as a <b>draft</b> and affects nothing until approved.
          Editing one sends it back to draft — an approval is of the words that were
          approved. Retiring keeps it on the record, so an old document can still be
          explained.
        </p>
      </div>
    </aside>
  )
}

/* A SKILL'S BODY, laid out the way it was written.
 *
 * A skill is a fragment of the prompt the writer works from, so an author writes it the
 * way they would write any instruction: a paragraph of context, the points it breaks
 * into, sometimes both. The store keeps that layout (db.skill_body) and the prompt
 * keeps it (skills._render) — this is the third place it has to survive, and it was the
 * one printing the whole thing as a single run-on paragraph.
 *
 * Deliberately NOT a markdown renderer. Markdown swallows a single newline, which is
 * exactly the break an author writing a three-line instruction meant to keep, and it
 * would also start interpreting stray underscores and asterisks in ordinary prose. This
 * understands what `skill_body` preserves and nothing else: paragraphs, blank-line
 * breaks, `- ` bullets and `1. ` numbers.
 */
/* Does this line look like code rather than a sentence?
 *
 * Authors paste the HTML their example starts from and the CSS the media query changes
 * — that is the example, and rendering it as prose paragraphs is the same as not
 * showing it. Prose does not start with `<`, `{` or `}`, does not end with `{`, `}` or
 * `;`, and (after db.skill_body, which now keeps indentation only where the author put
 * it) is not indented. Two consecutive such lines are needed before anything is treated
 * as code, so one odd sentence never becomes a code block on its own.
 */
const CODEISH = (l) => (
  /^\s+\S/.test(l)                      // indented — the author put it there
  || /^[<{}]/.test(l)                    // <div>, {, }
  || /[{};]\s*$/.test(l)                 // ends in a brace or semicolon
  || /^[.#@][\w-]/.test(l)               // .cards, #id, @media
)

/* `labelFirst` — for one instruction of a grouped skill, where the first line IS the
 * rule's name by construction (skills._instructions_from joins `title` and `text` with a
 * newline). Guessing at it instead let the short titles through as headings and left the
 * long ones as body text, so a list of seven rules had four bold names and three plain
 * ones for no reason a reader could see. */
function SkillBody({ text, className = 'skilltext', labelFirst = false }) {
  const blocks = []
  let para = []
  let list = null
  let code = null
  const flushPara = () => { if (para.length) { blocks.push({ t: 'p', lines: para }); para = [] } }
  const flushList = () => { if (list) { blocks.push(list); list = null } }
  const flushCode = () => {
    if (!code) return
    // One code-ish line on its own is far more likely to be a sentence that happens to
    // end in a semicolon than it is to be a snippet.
    if (code.lines.filter((l) => l.trim()).length > 1) blocks.push(code)
    else para.push(...code.lines.filter((l) => l.trim()))
    code = null
  }
  const raw = String(text || '').split('\n')
  let fenced = false
  for (const line of raw) {
    // An explicit ``` fence is unambiguous and always wins.
    if (/^\s*```/.test(line)) {
      if (fenced) { flushCode(); fenced = false } else { flushPara(); flushList(); fenced = true; code = { t: 'code', lines: [] } }
      continue
    }
    if (fenced) { code.lines.push(line); continue }
    if (!line.trim()) {
      // A blank line inside a snippet is part of the snippet, not the end of it.
      if (code) code.lines.push('')
      else { flushPara(); flushList() }
      continue
    }
    const heading = line.match(/^\s*#{1,4}\s+(.*)$/)
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/)
    const number = line.match(/^\s*(\d+)[.)]\s+(.*)$/)
    if (!heading && !bullet && !number && CODEISH(line)) {
      flushPara()
      if (!code) code = { t: 'code', lines: [] }
      code.lines.push(line)
      continue
    }
    flushCode()
    if (heading) {
      flushPara(); flushList()
      blocks.push({ t: 'h', text: heading[1] })
    } else if (bullet) {
      flushPara()
      if (list?.t !== 'ul') { flushList(); list = { t: 'ul', items: [] } }
      list.items.push({ head: bullet[1], body: [] })
    } else if (number) {
      flushPara()
      if (list?.t !== 'ol') { flushList(); list = { t: 'ol', items: [], start: Number(number[1]) } }
      list.items.push({ head: number[2], body: [] })
    } else if (list) {
      // A PLAIN LINE DIRECTLY UNDER A LIST ITEM BELONGS TO IT. This is how people
      // actually write a numbered brief:
      //
      //     1. Explain the Concept First
      //     Introduce and explain the concept clearly before showing any code.
      //
      // Treated as a separate paragraph — which is what this did — the pair came apart:
      // the heading rendered as a small grey list item and its own description rendered
      // underneath as a full-width bold paragraph, so the subordinate line looked like
      // the heading and the heading looked like a footnote. Seven of those in a row is
      // unreadable, and it is not a styling problem: they are one thing and were being
      // shown as fourteen.
      list.items[list.items.length - 1].body.push(line)
    } else {
      para.push(line)
    }
  }
  flushCode(); flushPara(); flushList()
  if (!blocks.length) return null
  // The first line is the label, said rather than guessed — see `labelFirst`.
  if (labelFirst && blocks.length && blocks[0].t === 'p' && blocks[0].lines.length > 1) {
    blocks[0].head = blocks[0].lines[0].trim()
    blocks[0].lines = blocks[0].lines.slice(1)
  } else if (labelFirst && blocks.length > 1 && blocks[0].t === 'p'
             && blocks[0].lines.length === 1) {
    blocks[0] = { t: 'h', text: blocks[0].lines[0].trim() }
  }
  // A SHORT UNPUNCTUATED FIRST LINE IS A HEADING. People write
  //
  //     Preferred Flow
  //     Concept → Syntax → Code → …
  //
  // and mean the first line as a title, without reaching for '#'. Three conditions
  // together, because any one of them alone is wrong too often: at most six words, no
  // terminal punctuation, and the line under it starts a new sentence with a capital.
  // That last one is what separates a title from a wrapped sentence — "Always show the
  // code / before explaining it." fails it and stays a paragraph, which is right.
  for (const b of blocks) {
    if (b.t !== 'p' || b.head || b.lines.length < 2) continue
    const first = b.lines[0].trim()
    if (first.split(/\s+/).length <= 6 && !/[.:;,!?—-]$/.test(first)
        && /^[A-Z0-9]/.test(b.lines[1].trim())) {
      b.head = first
      b.lines = b.lines.slice(1)
    }
  }
  const lines = (ls) => ls.map((l, j) => (
    <React.Fragment key={j}>{j > 0 && <br />}{l}</React.Fragment>))
  return (
    <div className={className}>
      {blocks.map((b, i) => {
        if (b.t === 'h') return <h4 key={i}>{b.text}</h4>
        if (b.t === 'code') {
          // Trimmed of the blank lines a paste leaves at either end, and de-indented by
          // its own smallest indent so a snippet that was nested in the note does not
          // arrive wearing that nesting.
          const ls = [...b.lines]
          while (ls.length && !ls[0].trim()) ls.shift()
          while (ls.length && !ls[ls.length - 1].trim()) ls.pop()
          const pad = Math.min(...ls.filter((l) => l.trim())
                                 .map((l) => l.length - l.trimStart().length))
          return <pre key={i} className="skillcode"><code>
            {ls.map((l) => l.slice(pad)).join('\n')}</code></pre>
        }
        // Every line the author typed is a line. Joining them with a space is what
        // made a laid-out instruction read as prose.
        if (b.t === 'p') {
          return (
            <React.Fragment key={i}>
              {b.head && <h4>{b.head}</h4>}
              <p>{lines(b.lines)}</p>
            </React.Fragment>
          )
        }
        const Tag = b.t
        // An item WITH a description underneath is a labelled step, so its first line
        // is the label and carries the weight; an item on its own is just a point and
        // is set like one. Deciding that per item rather than per list is what lets one
        // renderer handle both "1. Explain the Concept First / <what that means>" and a
        // plain list of three bullets.
        return (
          <Tag key={i} start={b.start}>
            {b.items.map((it, j) => (
              <li key={j} className={it.body.length ? 'labelled' : ''}>
                <span className="li-head">{it.head}</span>
                {it.body.length > 0 && <span className="li-body">{lines(it.body)}</span>}
              </li>
            ))}
          </Tag>
        )
      })}
    </div>
  )
}

/* ONE SKILL, AS A FILE.
 *
 * A skill is a document — a paragraph, its points, sometimes several blocks of both —
 * and a brief is a folder of them. Printed open, end to end, six of them are a wall of
 * text you have to read all of to find the one you came for. So each is a file: its
 * name and its state on one row, and its contents behind a click.
 *
 * The whole row is the control, not just the icon, because a 40px target inside a
 * 900px row is a target you have to aim at. The icon is what makes it read as a file;
 * the row is what makes it easy to open.
 */
function SkillCard({ s, canEdit, busy, editing, editText,
                     setEditText, editIns, setEditIns, onStartEdit, onCancelEdit,
                     onSave, onApprove, onRetire, open, onToggle }) {
  // OPEN BY DEFAULT ON A DRAFT. The agent is allowed to sharpen a skill and to give it
  // structure the author did not type, so approving one means checking it against what
  // you wrote — and that is exactly the moment a draft is being read. On an approved
  // skill the provenance is history and stays folded away.
  const [why, setWhy] = useState(s.status === 'draft')
  const lines = s.instructions || []
  const quotes = s.source_quotes?.length ? s.source_quotes
                 : (s.source_quote ? [s.source_quote] : [])
  // THE FILE NAME is the first line the author wrote, which is how anyone writing an
  // instruction starts it. What follows is the contents.
  const body = String(s.text || '').split('\n')
  const name = body[0] || ''
  const rest = body.slice(1).join('\n').trim()
  const hasMore = !!rest || lines.length > 0
  // The file's size, in the unit that means something here.
  const size = lines.length
    ? `${lines.length} instruction${lines.length === 1 ? '' : 's'}`
    : (rest ? `${rest.split('\n').filter((l) => l.trim()).length + 1} lines` : '1 line')
  // Editing always shows everything — you cannot edit what is folded away.
  const shown = open || editing
  return (
    <article className={`skill ${s.status} ${shown ? 'open' : ''}`}>
      <div className="filerow">
        <button className="filebtn" onClick={onToggle} disabled={editing || !hasMore}
                aria-expanded={shown} title={hasMore
                  ? (shown ? 'Close this skill' : 'Open this skill')
                  : 'This skill is one line — there is nothing folded away'}>
          <span className="fileicon">
            <Icon name="doc" size={22} />
            {hasMore && <span className="filechev"><Icon name="chevron" size={11} /></span>}
          </span>
          <span className="filemain">
            <span className="filename">{name}</span>
            <span className="filemeta">
              <span className={`dot ${s.status}`} title={s.status === 'approved'
                ? 'in force' : 'written, not yet approved — it affects nothing'} />
              <span className="mtext">{s.status === 'approved' ? 'In force' : 'Awaiting approval'}</span>
              <span className="msep">·</span>
              <span className="mtext">{size}</span>
              {s.scope === 'session' && (
                <span className="tag scope">Session {s.session_ref}</span>)}
              {s.check && <span className="tag" title={JSON.stringify(s.check)}>Checked automatically</span>}
              {s.source?.startsWith('imported:') && (
                <span className="tag">From {s.source.slice(9)}</span>)}
            </span>
          </span>
        </button>
        {canEdit && (
          <div className="skillacts">
            {editing ? (
              <>
                <button className="primary sm" disabled={busy || !editText.trim()}
                        onClick={onSave}>Save</button>
                <button className="ghostbtn sm" onClick={onCancelEdit}>Cancel</button>
              </>
            ) : (
              <>
                {s.status !== 'approved' && (
                  <button className="primary sm" disabled={busy} onClick={onApprove}>
                    <Icon name="check" size={13} />Approve</button>)}
                <button className="iconbtn" disabled={busy} title="Edit this skill"
                        onClick={onStartEdit}><Icon name="pencil" size={14} /></button>
                <button className="iconbtn danger" disabled={busy}
                        title="Retire it — it stops applying, and is kept on the record"
                        onClick={onRetire}><Icon name="trash" size={14} /></button>
              </>
            )}
          </div>
        )}
      </div>

      {shown && (
        <div className="filebody">
          {editing ? (
            <>
              {/* Auto-sized, because a skill can be a paragraph and its points and a
                  two-row box showed a third of it. */}
              <label>The skill</label>
              <AutoTextarea minRows={4} value={editText}
                            onChange={(e) => setEditText(e.target.value)} />
              {/* ONE BOX PER INSTRUCTION.
                  They used to be joined with newlines into a single box and split back
                  apart on every newline — so an instruction that spans lines (its title,
                  what it requires, the snippet under it) came back as one instruction
                  PER LINE, with `.trim()` taking the indentation off the code and
                  `.filter(Boolean)` removing the blank lines that separated it. Opening
                  a skill to fix a typo and pressing Save turned two instructions into
                  seven and destroyed the example inside them. An instruction is a
                  document; it gets its own box. */}
              {editIns.length > 0 && (
                <>
                  <label>Its instructions — in order</label>
                  {editIns.map((ins, k) => (
                    <div className="insedit" key={k}>
                      <span className="insnum">{k + 1}</span>
                      <AutoTextarea minRows={2} value={ins}
                                    onChange={(e) => setEditIns(
                                      (v) => v.map((x, j) => (j === k ? e.target.value : x)))} />
                      <button className="iconbtn danger" title="Remove this instruction"
                              onClick={() => setEditIns((v) => v.filter((_, j) => j !== k))}>
                        <Icon name="x" size={13} /></button>
                    </div>
                  ))}
                  <button className="linkbtn" onClick={() => setEditIns((v) => [...v, ''])}>
                    <Icon name="plus" size={13} /> Add an instruction
                  </button>
                </>
              )}
            </>
          ) : (
            <>
              {rest && <SkillBody text={rest} />}
              {lines.length > 1 && (
                <ol className="skillins">
                  {lines.map((line, i) => (
                    <li key={i}>
                      <SkillBody text={line} className="skillinsbody" labelFirst />
                    </li>))}
                </ol>
              )}
              {lines.length === 1 && <SkillBody text={lines[0]} />}
              {/* EVERY phrase it was drawn from, not just the first — the author says
                  the same thing twice in different words and those merge into one skill,
                  and the approval only means something if you can see all of what it was
                  built from. */}
              {quotes.length > 0 && (s.source === 'requirements' || s.source === 'user') && (
                <>
                  <button className="whybtn" onClick={() => setWhy((v) => !v)}>
                    {why ? 'Hide what you wrote'
                         : (s.status === 'draft' ? 'Compare with what you wrote'
                                                 : 'From your words')}
                  </button>
                  {why && (
                    <blockquote className="skillquote">
                      {quotes.map((q, i) => <span key={i}>“{q}”</span>)}
                    </blockquote>
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}
    </article>
  )
}

/* THE SKILLS PAGE and THE PREREQUISITES PAGE — one component, two views.
 *
 * They were one screen, and it was the longest in the app: a paragraph of explanation,
 * a precedence ladder, four category cards (empty on a new course, each repeating "add
 * one below"), the composer, and then the whole prerequisites section underneath. The
 * thing you came to do was three screens down. They are two rail entries now, and the
 * composer opens at the TOP of the skills page rather than after everything it could
 * possibly add to.
 *
 * `view` selects which one renders. They stay one component because they share every
 * handler, the busy flag and the one message strip.
 */
function CourseRules({ view = 'skills', course, skills, prereqs, busy, msg, onClearMsg,
                      courses = [], justCreated, onDismissNew, onHelp,
                      onAdd, onFromRequirements, onImport, onApprove, onEdit, onRetire,
                      onAddPrereq, onAddExternalPrereq, onRemovePrereq, job }) {
  const jobRan = useRef(false)
  const [extName, setExtName] = useState('')
  const [extLinks, setExtLinks] = useState('')
  const [extOpen, setExtOpen] = useState(false)
  const [text, setText] = useState('')
  const [reqs, setReqs] = useState('')
  const [mode, setMode] = useState('write')
  const [editing, setEditing] = useState(null)
  const [editText, setEditText] = useState('')
  // The skill's own lines, one per row, while it is being edited. Kept apart from its
  // sentence because they are different things: the sentence says what the skill is,
  // the lines are what a writer actually follows.
  // One entry per instruction, edited in its own box — see the note in SkillCard.
  const [editIns, setEditIns] = useState([])
  // WHAT a new skill governs and WHERE it applies. Both default to the answer that was
  // the only possible one before this existed — uncategorised, whole course.
  const [cat, setCat] = useState('')
  const [scope, setScope] = useState('course')
  const [sessionNo, setSessionNo] = useState('')
  // Which session's brief the list is showing. '' = all of them.
  const [seeSession, setSeeSession] = useState('')
  // WHICH FILES ARE OPEN. Held here rather than inside each card so that "open all" is
  // possible at all, and so a card re-rendering (an approval, an edit) cannot quietly
  // close itself.
  const [openIds, setOpenIds] = useState(() => new Set())
  const toggleOpen = (id) => setOpenIds((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
  // IS THE COMPOSER OPEN. Closed by default once the course has skills, because then
  // the page is for reading what the course is written under; open by default when it
  // has none, because then there is nothing to read and one thing to do.
  const [adding, setAdding] = useState(false)
  const composerRef = useRef(null)
  // Close the link form once the decks are ACTUALLY read — and only when it worked, so a
  // failed read leaves the links on screen to be corrected instead of retyped.
  useEffect(() => {
    if (job) { jobRan.current = true; return }
    if (jobRan.current) { jobRan.current = false; if (msg?.ok) setExtOpen(false) }
  }, [job, msg])
  const list = skills?.skills || []
  const canEdit = skills?.can_edit
  const approvedCount = list.filter((x) => x.status === 'approved').length
  const draftCount = list.filter((x) => x.status === 'draft').length
  // Which sessions this course has written anything for — the only ones worth offering
  // in the filter, since picking a session with no skills of its own shows the course
  // brief you were already looking at.
  const sessionsWithSkills = [...new Set(list.filter((x) => x.scope === 'session')
    .map((x) => x.session_ref).filter(Boolean))].sort((a, b) => Number(a) - Number(b))
  // WHAT ONE SESSION IS WRITTEN UNDER: its own skills plus everything course-wide, which
  // is exactly what src/skills.py resolves for a run. Unfiltered, every session's skills
  // are listed, because this is also the screen where they are managed.
  const shown = seeSession
    ? list.filter((x) => x.scope !== 'session' || x.session_ref === seeSession)
    : list
  const byGroup = SKILL_GROUPS.map((g) => ({
    ...g, items: shown.filter((x) => (x.category || '') === g.id) }))
  const filled = byGroup.filter((g) => g.items.length > 0)
  // The four a course CAN write and has not. Shown as one line of buttons, not as four
  // empty cards: the gap is worth knowing about, and it is worth exactly one line.
  const gaps = byGroup.filter((g) => g.id && g.items.length === 0)
  // The skills that HAVE something folded away — the only ones "open all" is about.
  const expandable = shown.filter((x) => String(x.text || '').includes('\n')
                                         || (x.instructions || []).length > 0)
                          .map((x) => x.id)
  const allOpen = expandable.length > 0 && expandable.every((id) => openIds.has(id))

  // What the pickers currently say, in the shape the API takes. The category is only
  // sent when the author chose one — left alone, the agent classifies what they wrote,
  // which is better than recording a guess as their decision.
  const whereNow = () => ({
    category: cat || undefined,
    scope,
    session: scope === 'session' ? Number(sessionNo) : undefined,
  })
  function openComposer(preset) {
    if (preset !== undefined) setCat(preset)
    setAdding(true)
    // The composer is at the top, so this only matters when the page is already
    // scrolled — but then it matters a lot.
    requestAnimationFrame(() => composerRef.current?.scrollIntoView(
      { behavior: 'smooth', block: 'nearest' }))
  }

  const report = prereqs?.report || {}
  const attached = (prereqs?.prereqs || []).map((p) => p.prereq)
  const available = (prereqs?.available || []).filter((c) => !attached.includes(c))

  if (!course) {
    return <section className="card">
      <h2><span className="hicon"><Icon name={view === 'skills' ? 'skills' : 'curriculum'} /></span>
        {view === 'skills' ? 'Skills' : 'Prerequisites'}</h2>
      <p className="hint">Open a course first — these belong to one course.</p>
    </section>
  }

  const alert = msg && (
    <div className={`alert ${msg.ok ? 'ok' : 'error'}`} onClick={onClearMsg}>{msg.text}</div>
  )

  // ── PREREQUISITES ───────────────────────────────────────────────────────────
  if (view === 'prereqs') {
    return (
      <section className="card">
        <header className="pagehead">
          <div>
            <h2><span className="hicon"><Icon name="curriculum" /></span>
              Prerequisites — {course}</h2>
            <p className="hint tight">
              Courses taught before this one. Their <b>whole decks are read</b> — not just
              the topic names but the slide content — so the writer knows how far each
              topic was taken and pitches above it. Those topics are <b>assumed</b>: never
              re-taught, and free to refer to. The opposite of the rule for earlier
              sessions of THIS course, where repeating a topic is a failure.
            </p>
          </div>
        </header>
        <label>Courses taught before this one</label>
        <div className="memberlist">
          {attached.length === 0 && (
            <span className="hint" style={{ margin: 0 }}>
              None yet — nothing is assumed, so this course teaches from the ground up.
            </span>)}
          {(prereqs?.prereqs || []).map(({ prereq: n, kind }) => (
            <span key={n} className="memberchip">
              {n}
              {kind === 'external' && <span className="mtag" title="taught elsewhere — this agent knows it through its slides">elsewhere</span>}
              {canEdit && <button className="mx" disabled={busy} title={`Remove ${n}`}
                                  onClick={() => onRemovePrereq(n)}>×</button>}
            </span>
          ))}
        </div>
        {canEdit && (
          <div className="gactions">
            {available.length > 0 && (
              <select defaultValue="" disabled={busy}
                      onChange={(e) => { if (e.target.value) { onAddPrereq(e.target.value); e.target.value = '' } }}>
                <option value="" disabled>a course in this agent…</option>
                {available.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            )}
            <button className={`ghostbtn ${extOpen ? 'on' : ''}`} disabled={busy}
                    onClick={() => setExtOpen((v) => !v)}>
              <Icon name="plus" size={14} /> A course not in this agent
            </button>
          </div>
        )}
        {canEdit && extOpen && (
          <div className="regen">
            <span className="hint">
              A course your learners did somewhere else. This agent knows it only through
              its slides, so paste one Google Slides link per session. The decks are read
              the same way this course's own are, and they belong to <b>{course}</b> — they
              go if it does.
            </span>
            <input value={extName} onChange={(e) => setExtName(e.target.value)}
                   placeholder="e.g. JavaScript Essentials (taught elsewhere)" />
            <textarea rows={4} value={extLinks} onChange={(e) => setExtLinks(e.target.value)}
                      placeholder={'https://docs.google.com/presentation/d/…/edit\nhttps://docs.google.com/presentation/d/…/edit'} />
            <div className="gactions">
              <button className="primary"
                      disabled={busy || !!job || !extName.trim() || !extLinks.trim()}
                      onClick={() => {
                        // The form used to close on click, which unmounted the only thing
                        // that could report progress — so pasting twelve links looked
                        // identical to the button doing nothing. It closes when the decks
                        // are actually read, not when the request is sent.
                        const n = extName.trim()
                        Promise.resolve(onAddExternalPrereq(n,
                          extLinks.split('\n').map((l) => l.trim()).filter(Boolean)))
                          .then((ok) => { if (ok === false) return
                                          setExtName(''); setExtLinks('') })
                      }}>
                {busy || job ? 'Reading its decks…' : 'Add and read its decks'}
              </button>
              <button className="ghostbtn" disabled={busy || !!job}
                      onClick={() => setExtOpen(false)}>
                {job ? 'Reading…' : 'Cancel'}
              </button>
            </div>
            {job && (
              <div className="joblive">
                <div className="jobbar">
                  <span style={{ width: `${job.total ? Math.round(100 * (job.done + job.failed) / job.total) : 8}%` }} />
                </div>
                <span className="hint">
                  {job.stage === 'done'
                    ? 'Finishing…'
                    : `${job.stage || 'working'} — ${job.done} of ${job.total || '?'} deck(s) read`}
                  {job.slides ? `, ${job.slides} slide(s) so far` : ''}
                  {job.failed ? `, ${job.failed} could not be read` : ''}.
                  {' '}Each deck is fetched from Google Slides, so this takes a few seconds
                  per link. You can leave this open.
                </span>
              </div>
            )}
          </div>
        )}
        {/* The report is computed server-side over the decks that are STORED, and it comes
            with the panel — so while a read is running it is a snapshot from before the
            read started. It sat directly under a progress line reading "8 of 29 deck(s)
            read, 343 slide(s) so far" and said "0 session(s), 0 slides": two true numbers
            that together look like a broken one. While a job is live the line says so
            instead of quoting a figure it knows is behind. */}
        {attached.length > 0 && (
          <span className="hint">
            {job ? (
              <>Read from {attached.length} course(s): counting once this read finishes —{' '}
              {job.done || 0} deck(s) and {(job.slides || 0).toLocaleString()} slide(s) so far.</>
            ) : (
            <>
            Read from {attached.length} course(s): {report.sessions_indexed || 0} session(s),{' '}
            {(report.slides_indexed || 0).toLocaleString()} slides,{' '}
            {report.topics_indexed || 0} distinct topics
            {report.content_chars
              ? ` and ${Math.round(report.content_chars / 1000)}k characters of slide content`
              : ''}.
            {report.overlaps?.length > 0 && (
              <> <b>{report.overlaps.length} of this course's takeaways name something a
                prerequisite already taught</b> — often right, if the session deepens it,
                but worth seeing: {report.overlaps.slice(0, 3).map((o) =>
                  `Session ${o.session_no} (“${(o.topics || [o.topic]).join('”, “')}”, from `
                  + `${(o.prereqs || [o.prereq]).filter(Boolean).join(' / ')})`).join('; ')}
                {report.overlaps.length > 3 ? `, and ${report.overlaps.length - 3} more` : ''}.</>
            )}
            </>
            )}
          </span>
        )}
        {alert}
      </section>
    )
  }

  // ── SKILLS ──────────────────────────────────────────────────────────────────
  const empty = list.length === 0
  return (
    <section className="card">
      <header className="pagehead">
        <div>
          <h2><span className="hicon"><Icon name="skills" /></span> Skills — {course}</h2>
          <p className="hint tight">
            How this course is taught — the sequence, the depth, the examples, the words.
            The curriculum decides <i>what</i> it covers; these decide <i>how</i>.{' '}
            <button className="link inline" onClick={onHelp}>How skills work</button>
          </p>
        </div>
        {canEdit && !adding && (
          <button className="primary" onClick={() => openComposer('')}>
            <Icon name="plus" size={14} /> Add a skill
          </button>
        )}
      </header>

      {justCreated === course && (
        <div className="alert ok">
          <b>“{course}” is created. Set what it is written under before you generate.</b>
          <p>The alternative is generating a document under rules nobody set, then
             correcting it a session at a time. Nothing here is locked — add, edit and
             retire any of it later.</p>
          <div className="gactions">
            <button className="ghostbtn" onClick={onDismissNew}>Skip for now</button>
          </div>
        </div>
      )}

      {!canEdit && (
        <div className="alert warn">
          Only {skills?.owner || 'an admin'} can change these — working on a course and
          deciding the rules every document it produces is written under are different
          things.
        </div>
      )}

      {/* THE COMPOSER, AT THE TOP. It used to sit under every category it could add to,
          which on an empty course meant four "nothing here yet" cards stood between the
          author and the only control on the page. */}
      <div ref={composerRef}>
        {canEdit && (adding || empty) && (
          <div className="composer">
            <div className="cmphead">
              <span className="eyebrow">Add a skill</span>
              <div className="cmpheadright">
                <div className="segmented">
                  {[['write', 'Write one'], ['requirements', 'From my notes'],
                    ['import', 'Import']].map(([m, l]) => (
                    <button key={m} className={mode === m ? 'on' : ''}
                            onClick={() => setMode(m)}>{l}</button>
                  ))}
                </div>
                {!empty && (
                  <button className="iconbtn" title="Close" onClick={() => setAdding(false)}>
                    <Icon name="x" size={14} /></button>)}
              </div>
            </div>

            {/* WHERE IT APPLIES is asked on both authoring paths; WHAT IT GOVERNS only
                on "Write one".
                "From my notes" produces ONE SKILL PER CATEGORY — that is the entire
                point of it — so there is no single category to pin it to, and
                skills_from_requirements does not read one. Showing the chips there was a
                control that silently did nothing: you could pick "Teaching flow", get
                three skills back under three different headings, and have no way to tell
                whether you had been ignored or had misunderstood the feature. */}
            {mode !== 'import' && (
              <div className="cmpwhere">
                {mode === 'write' ? (
                  <div className="cmpfield">
                    <label>Files under</label>
                    <div className="chiprow">
                      <button className={`chipbtn ${cat === '' ? 'on' : ''}`}
                              onClick={() => setCat('')}
                              title="write it, and the agent files it under whichever of the four it is">
                        Decide for me</button>
                      {SKILL_CATEGORIES.map(([id, label, hint, icon]) => (
                        <button key={id} className={`chipbtn ${cat === id ? 'on' : ''}`}
                                title={hint} onClick={() => setCat(id)}>
                          <Icon name={icon} size={13} />{label}</button>
                      ))}
                    </div>
                    <span className="hint tight">
                      {cat
                        ? (SKILL_CATEGORIES.find(([id]) => id === cat) || [])[2]
                        : 'The agent reads what you wrote and files it under one of the '
                          + 'four. Pick one yourself and your choice wins.'}
                    </span>
                  </div>
                ) : (
                  <div className="cmpfield">
                    <label>Files under</label>
                    <span className="hint tight" style={{ marginTop: 0 }}>
                      Whichever of the four each one turns out to be — this path writes
                      <b> one skill per category</b>, grouping everything you say about
                      sequence into one, everything about explaining into another, and so
                      on. You can re-file any of them afterwards.
                    </span>
                  </div>
                )}
                <div className="cmpfield">
                  <label>Applies to</label>
                  <div className="chiprow">
                    <button className={`chipbtn ${scope === 'course' ? 'on' : ''}`}
                            onClick={() => setScope('course')}>All of {course}</button>
                    <button className={`chipbtn ${scope === 'session' ? 'on' : ''}`}
                            onClick={() => setScope('session')}>One session</button>
                    {/* THERE IS NO "EVERY COURSE". A rule for every course belongs in
                        the repo's harness files, which are read on every generation for
                        every course and are versioned with the code. A second place to
                        write the same house rule only made the two disagree. */}
                    {scope === 'session' && (
                      <input className="tiny" type="number" min="1" value={sessionNo}
                             placeholder="no."
                             onChange={(e) => setSessionNo(e.target.value)} />)}
                  </div>
                  {scope === 'session' && <span className="hint tight">
                    That session and nowhere else. Where it disagrees with a skill for the
                    whole course, this one wins.</span>}
                </div>
              </div>
            )}

            {mode === 'write' && (
              <>
                <label>The skill</label>
                <AutoTextarea minRows={4} value={text}
                              onChange={(e) => setText(e.target.value)}
                              placeholder={'Write it the way you would say it. Lay it out '
                                + 'however it needs to be laid out — a paragraph, points, '
                                + 'or a paragraph and then its points:\n\n'
                                + 'Explain every snippet line by line.\n'
                                + '- name each variable before it is used\n'
                                + '- say what the line does, not what it says'} />
                <div className="cmpfoot">
                  <span className="hint tight">
                    Written up as the instruction a writer works from, with your own words
                    kept beside it. It affects nothing until you approve it.
                  </span>
                  <button className="primary sm"
                          disabled={busy || !text.trim()
                                    || (scope === 'session' && !sessionNo)}
                          onClick={() => Promise.resolve(onAdd(text, whereNow()))
                            .then((ok) => { if (ok !== false) setText('') })}>
                    {busy ? 'Writing it up…' : 'Add as draft'}
                  </button>
                </div>
              </>
            )}
            {mode === 'requirements' && (
              <>
                <label>Everything this course needs, in plain sentences</label>
                <AutoTextarea minRows={5} value={reqs}
                              onChange={(e) => setReqs(e.target.value)}
                              placeholder="e.g. start with the problem, then the concept, then how it works, then an example — explain intuition before the formal definition, use simple language first, and connect each concept to the last session" />
                <div className="cmpfoot">
                  <span className="hint tight">
                    Grouped, not scattered: everything about sequence becomes one
                    teaching-flow skill in the order you wrote it. Nothing it cannot trace
                    back to your words is kept.
                  </span>
                  <button className="primary sm"
                          disabled={busy || !reqs.trim()
                                    || (scope === 'session' && !sessionNo)}
                          onClick={() => Promise.resolve(onFromRequirements(reqs, whereNow()))
                            .then((ok) => { if (ok !== false) setReqs('') })}>
                    {busy ? 'Drafting…' : 'Draft skills from this'}
                  </button>
                </div>
              </>
            )}
            {mode === 'import' && (
              <>
                <label>Copy another course's approved skills</label>
                <div className="cmpfoot">
                  <span className="hint tight">
                    They arrive as drafts — a skill that was right for one course is a
                    proposal for the next, not a decision already taken.
                  </span>
                  <select defaultValue="" disabled={busy} className="cmpselect"
                          onChange={(e) => { if (e.target.value) { onImport(e.target.value); e.target.value = '' } }}>
                    <option value="" disabled>from a course…</option>
                    {courses.filter((c) => c.name !== course).map((c) => (
                      <option key={c.name} value={c.name}>{c.name}</option>))}
                  </select>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {empty && !canEdit && (
        <div className="emptystate">
          <span className="eicon"><Icon name="skills" size={20} /></span>
          <b>No skills yet</b>
          <p>This course is written to the house defaults. Its owner
             ({skills?.owner || 'an admin'}) decides what it needs beyond them.</p>
        </div>
      )}

      {!empty && (
        <>
          {/* ONE STRIP: how many are in force, how many are waiting, and whose brief you
              are looking at. Three separate lines of prose before. */}
          <div className="skillbar">
            <span className="sb"><b>{approvedCount}</b> in force</span>
            {draftCount > 0 && (
              <span className="sb warntext"><b>{draftCount}</b> awaiting approval</span>)}
            <span className="sbspacer" />
            {/* READ THE WHOLE BRIEF, or scan it. A reviewer checking one rule wants the
                list; someone about to generate wants to read all of it. */}
            {expandable.length > 0 && (
              <button className="linkbtn" onClick={() => setOpenIds(
                allOpen ? new Set() : new Set(expandable))}>
                <Icon name={allOpen ? 'skills' : 'expand'} size={13} />
                {allOpen ? 'Close all' : 'Open all'}
              </button>
            )}
            {sessionsWithSkills.length > 0 && (
              <label className="filterbox">
                <span>Showing</span>
                <select value={seeSession} onChange={(e) => setSeeSession(e.target.value)}>
                  <option value="">every session</option>
                  {sessionsWithSkills.map((n) => (
                    <option key={n} value={n}>session {n} only</option>))}
                </select>
              </label>
            )}
          </div>

          <div className="brief">
            {filled.map((g) => (
              <section key={g.id || 'other'} className="bgroup">
                <header className="bghead">
                  <span className="bgicon"><Icon name={g.icon} /></span>
                  <b className="bgtitle" title={g.hint}>{g.label}</b>
                  <span className="bgcount">{g.items.length}</span>
                  {canEdit && g.id && (
                    <button className="iconbtn" title={`Add a ${g.label.toLowerCase()} skill`}
                            onClick={() => openComposer(g.id)}>
                      <Icon name="plus" size={14} /></button>)}
                </header>
                {g.items.map((s) => (
                  <SkillCard key={s.id} s={s} canEdit={canEdit} busy={busy}
                             editing={editing === s.id}
                             onStartEdit={() => { setEditing(s.id); setEditText(s.text)
                                                  setEditIns([...(s.instructions || [])])
                                                  setOpenIds((v) => new Set(v).add(s.id)) }}
                             onCancelEdit={() => setEditing(null)}
                             editText={editText} setEditText={setEditText}
                             editIns={editIns} setEditIns={setEditIns}
                             onSave={() => {
                               // undefined, not [], when the skill never had any: the
                               // API reads "omitted" as leave-them-alone and an empty
                               // array as delete-them. Each box is ONE instruction and
                               // keeps its own line breaks and indentation; only
                               // entirely blank ones are dropped.
                               const ins = (s.instructions || []).length || editIns.length
                                 ? editIns.filter((x) => x.trim())
                                 : undefined
                               onEdit(s.id, editText, ins); setEditing(null)
                             }}
                             onApprove={() => onApprove(s.id)}
                             onRetire={() => onRetire(s.id)}
                             open={openIds.has(s.id)} onToggle={() => toggleOpen(s.id)} />
                ))}
              </section>
            ))}
          </div>
        </>
      )}

      {/* THE GAPS, in one line — in both states. Four empty cards each saying "nothing
          yet" said the same thing four times and buried the skills that did exist; on a
          course with none at all they WERE the whole page. Each one opens the composer
          filed under that category, so the line is a way in rather than a scolding. */}
      {canEdit && gaps.length > 0 && (
        <p className="gapline">
          Nothing said yet about{' '}
          {gaps.map((g, i) => (
            <span key={g.id}>
              {i > 0 && (i === gaps.length - 1 ? ' or ' : ', ')}
              <button className="link inline" onClick={() => openComposer(g.id)}>
                {g.label.toLowerCase()}</button>
            </span>
          ))}.
        </p>
      )}

      {alert}
    </section>
  )
}


function CourseSettings({ course, budget, onChange, courseType, onCourseType,
                         rows = [], onSession,
                         canDelete, sharedWith = [], onAskDelete, onDelete,
                         deleteAsk, onCancelDelete, deleting }) {
  const d = budget?.defaults || {}
  const eff = budget?.effective || {}
  const set = budget?.settings || {}
  const overrides = rows.filter((r) => r.max_pages != null || r.max_slides != null)
  return (
    <section className="card">
      <h2><span className="hicon"><Icon name="settings" /></span> Settings — {course || 'no course'}</h2>

      <label>Course type</label>
      <select value={courseType} onChange={(e) => onCourseType(e.target.value)}>
        <option value="semester">Semester — deep theoretical dive</option>
        <option value="interview">Interview-targeted</option>
      </select>
      <span className="hint">Both aim at clearing interviews; semester goes deeper on theory.</span>

      <label>Document length for every session in this course</label>
      <div className="setrowpair">
        {/* The label BELONGS ABOVE ITS BOX. It sat underneath, so on a row of two number
            fields each caption read as a note about the field below it rather than the
            one above — and the last caption had nothing under it at all. */}
        <label className="setfield">
          <span>Pages</span>
          <input type="number" value={set.max_pages ?? ''} placeholder={String(d.max_pages ?? '')}
                 onChange={(e) => onChange({ max_pages: e.target.value === '' ? null : Number(e.target.value) })} />
          <span className="fieldnote">blank = {d.max_pages}</span>
        </label>
        <label className="setfield">
          <span>Slides</span>
          <input type="number" value={set.max_slides ?? ''} placeholder={String(d.max_slides ?? '')}
                 onChange={(e) => onChange({ max_slides: e.target.value === '' ? null : Number(e.target.value) })} />
          <span className="fieldnote">blank = {d.max_slides}</span>
        </label>
      </div>
      <span className="hint">
        Currently applied: <b>{eff.max_pages} pages</b> and <b>{eff.max_slides} slides</b>
        {eff.source ? ` (${eff.source})` : ''}.
      </span>

      {/* PER-SESSION OVERRIDES. A rare adjustment, so it lives here rather than as two
          more columns in the curriculum table — where it took the row to nine cells
          against a seven-column grid and wrapped Deck onto a line of its own. */}
      <label>Sessions that need something different</label>
      {overrides.length === 0 && (
        <span className="hint">None — every session uses the course budget above.</span>
      )}
      {overrides.map((r) => (
        <div key={r.session_no} className="ovrow">
          <span className="ovname">{r.session_no} — {r.session_name}</span>
          <input type="number" value={r.max_pages ?? ''} placeholder={String(eff.max_pages ?? '')}
                 onChange={(e) => onSession(r.session_no, {
                   max_pages: e.target.value === '' ? null : Number(e.target.value),
                   max_slides: r.max_slides ?? null })} />
          <span className="hint">pages</span>
          <input type="number" value={r.max_slides ?? ''} placeholder={String(eff.max_slides ?? '')}
                 onChange={(e) => onSession(r.session_no, {
                   max_pages: r.max_pages ?? null,
                   max_slides: e.target.value === '' ? null : Number(e.target.value) })} />
          <span className="hint">slides</span>
          <button className="iconbtn" title="Back to the course budget"
                  onClick={() => onSession(r.session_no, { max_pages: null, max_slides: null })}>
            <Icon name="x" size={13} /></button>
        </div>
      ))}
      <div className="ovrow">
        <select value="" onChange={(e) => e.target.value &&
                  onSession(Number(e.target.value), { max_pages: eff.max_pages, max_slides: eff.max_slides })}>
          <option value="">+ give a session its own budget…</option>
          {rows.filter((r) => r.max_pages == null && r.max_slides == null).map((r) => (
            <option key={r.session_no} value={r.session_no}>
              {r.session_no} — {r.session_name}
            </option>
          ))}
        </select>
      </div>

      {/* DELETING THE COURSE. Offered to whoever created it (and to admins), because a
          course you imported and no longer need has, until now, had to stay on your shelf
          for ever. Two steps, never one click: the confirmation states exactly what goes
          and what stays, and if a team is working from this curriculum it names them. */}
      {canDelete && (
        <div className="dangerzone">
          <label>Delete this course</label>
          {!deleteAsk || deleteAsk.course !== course ? (
            <>
              <button className="ghostbtn danger" onClick={() => onAskDelete?.(course)}>
                <Icon name="trash" /> Delete “{course}”
              </button>
              <span className="hint">
                Removes its curriculum and its length settings.
                {sharedWith.length > 0 && <> It is shared with <b>{sharedWith.join(', ')}</b>,
                  who work from this curriculum — you will be asked to confirm that.</>}
              </span>
            </>
          ) : (
            <div className={`alert ${deleteAsk.error ? 'error' : 'warn'}`}>
              {deleteAsk.error ? <b>Could not delete it: {deleteAsk.error}</b> : (
                <>
                  <b>Delete “{course}” for good?</b>
                  {deleteAsk.teams?.length > 0 && (
                    <p>It is on the shelf of <b>{deleteAsk.teams.map((t) => t.name).join(', ')}</b>.
                       Deleting it removes the curriculum they work from.</p>
                  )}
                  <ul>
                    <li>Its {rows.length} curriculum row{rows.length === 1 ? '' : 's'} and its
                        page/slide settings are removed.</li>
                    <li><b>Documents already generated are kept</b> — they stay in History and
                        stay downloadable, along with their costs.</li>
                  </ul>
                </>
              )}
              <div className="curactions">
                <button className="ghostbtn danger" disabled={deleting}
                        onClick={() => onDelete?.(course, deleteAsk.teams?.length > 0)}>
                  {deleting ? 'Deleting…'
                    : (deleteAsk.teams?.length > 0
                        ? 'Yes — take it off those teams and delete it'
                        : 'Yes, delete it')}
                </button>
                <button className="ghostbtn" disabled={deleting} onClick={onCancelDelete}>
                  Keep it
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

// The team you are working inside: who is on it, which courses it owns, and what it
// has produced. Separate from History on purpose — this answers "who am I working
// with", History answers "what has been made".
function TeamPanel({ entry, courses, course, onPick, onAddMember, onRemoveMember,
                    memberBusy, memberMsg }) {
  const t = entry.team
  const [newMember, setNewMember] = useState('')
  const owned = t.courses || (t.course ? [t.course] : [])
  const known = new Set(courses.map((c) => c.name))
  const missing = owned.filter((c) => !known.has(c))
  const s = entry.summary || {}
  // MEMBERS AND CONTRIBUTORS ARE NOT THE SAME COUNT, and the panel used to show both
  // with no hint of it — 2 members beside 3 contributors reads as a bug. A member is
  // someone ON the team; a contributor is anyone who has generated a doc for one of the
  // team's COURSES, which is how the team's history is gathered (see db.team_runs). That
  // legitimately includes people who were never on the team, or who have since left.
  const members = t.members || []
  const outsiders = (entry.contributors || []).filter((c) => !members.includes(c))
  return (
    <section className="card">
      <h2><span className="hicon"><Icon name="team" /></span> {t.name}</h2>
      <div className="metrics">
        <Metric label="Members" value={members.length} />
        <Metric label="Courses" value={owned.length} />
        {/* `s.runs` — a key the server has never sent. It read undefined, fell through
            to 0, and reported "Docs built 0" against work the same payload was counting
            contributors from. `docs_built` counts the runs that produced a document;
            attempts that failed or were abandoned are shown beside it, not as docs. */}
        <Metric label="Docs built" value={s.docs_built ?? 0}
                sub={s.total_runs > (s.docs_built ?? 0)
                      ? `${s.total_runs} attempt${s.total_runs === 1 ? '' : 's'}` : null} />
        {entry.contributors?.length > 0 && (
          <Metric label="Contributors" value={entry.contributors.length}
                  sub={outsiders.length
                        ? `${outsiders.length} not on the team` : null} />
        )}
      </div>

      <label>Courses this team works on</label>
      <div className="teamcourses">
        {owned.length === 0 && <span className="hint">No course attached yet.</span>}
        {owned.map((c) => (
          <button key={c} className={`coursechip ${c === course ? 'on' : ''}`}
                  onClick={() => onPick(c)}>
            {c}{!known.has(c) && <Icon name="warn" size={12} className="cwarn" />}
          </button>
        ))}
      </div>
      {missing.length > 0 && (
        <div className="alert warn">
          <b><Icon name="warn" /> {missing.length === 1 ? 'A course name does not match' : 'Course names do not match'}
             any curriculum the agent holds:</b> {missing.join(', ')}.
          <div className="hint">
            A team's course is matched by <b>exact name</b>, so a near miss (for example
            “Operating System” against a curriculum called “Operating Systems”) leaves
            this team looking empty. Fix it in <b>/admin → Teams</b>, or import a course
            under that exact name.
          </div>
        </div>
      )}

      <label>Members</label>
      <div className="memberlist">
        {(t.members || []).map((m) => (
          <span key={m} className={`memberchip ${m === t.owner_email ? 'owner' : ''}`}>
            {m}
            {m === t.owner_email && <span className="mtag" title="course owner — can add and remove members">owner</span>}
            {entry.contributors?.includes(m) && <span className="mdot" title="has built docs for this team">●</span>}
            {/* The owner is deliberately not removable here: a team whose owner is not
                on it can still manage members but cannot open the workspace it is
                responsible for. Re-assigning the owner is the admin's call. */}
            {t.can_manage && m !== t.owner_email && (
              <button className="mx" disabled={memberBusy} title={`Remove ${m} from ${t.name}`}
                      onClick={() => onRemoveMember?.(t.id, m)}>×</button>
            )}
          </span>
        ))}
      </div>

      {/* Offered only to the team's course owner and to admins — `can_manage` is decided
          by the server, not here. Everyone else gets the pointer to /admin. */}
      {t.can_manage ? (
        <>
          <div className="curactions">
            <input value={newMember} onChange={(e) => setNewMember(e.target.value)}
                   placeholder="colleague@nxtwave.co.in"
                   onKeyDown={(e) => {
                     if (e.key === 'Enter') { onAddMember?.(t.id, newMember); setNewMember('') }
                   }} />
            <button className="primary" disabled={memberBusy || !newMember.trim()}
                    onClick={() => { onAddMember?.(t.id, newMember); setNewMember('') }}>
              {memberBusy ? 'Working…' : <><Icon name="plus" /> Add member</>}
            </button>
          </div>
          <span className="hint">
            You are this team's <b>course owner</b>, so you can add and remove its members
            yourself — no admin needed. Anyone you add opens the same curriculum and sees
            every doc built before they arrived. Only an admin can change the team's
            course or hand ownership to somebody else.
          </span>
          {outsiders.length > 0 && (
            <span className="hint">
              <b>{outsiders.join(', ')}</b> {outsiders.length === 1 ? 'has' : 'have'} built
              docs for this team's courses without being on the team — the history is
              gathered by COURSE, so their work shows here either way. Add them if they
              should see the rest of it.
            </span>
          )}
        </>
      ) : (
        <span className="hint">
          Everything generated in this workspace belongs to the team, so anyone added later
          opens the same curriculum and sees every doc built before they arrived. Members
          are managed by {t.owner_email
            ? <>this team's course owner, <b>{t.owner_email}</b>, or an admin</>
            : <>an admin in <b>/admin → Teams</b> (this team has no course owner yet)</>}.
        </span>
      )}
      {memberMsg && (
        <div className={`alert ${memberMsg.ok ? 'ok' : 'error'}`}>{memberMsg.text}</div>
      )}
    </section>
  )
}

function MyHistory({ history }) {
  const s = history.summary || {}
  return (
    <section className="card">
      <h2><span className="hicon"><Icon name="history" /></span> My TR Docs — History</h2>
      <div className="metrics">
        <Metric label="Docs generated" value={s.total_runs || 0} />
        {/* Two different verdicts, and showing only the second under the word
            "Approved" is what made this read 0 against seventeen finished documents.
            Approved = a person reviewed every chunk and pressed Create final TR Doc.
            Passed all gates = the graders had nothing left to flag, which is strict:
            most documents are signed off with something still noted. */}
        <Metric label="Approved" value={s.approved_docs || 0}
                sub="reviewed & finalised" />
        <Metric label="Passed all gates" value={s.gates_passed_docs || 0}
                sub="no grader flags left" />
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
        <CourseGroup key={i} icon="curriculum" title={c.course} defaultOpen={i === 0}
                     meta={`${c.summary.total_runs} doc(s) · $${(c.summary.total_cost || 0).toFixed(4)}`}>
          <RunTable runs={c.runs} />
        </CourseGroup>
      ))}
    </section>
  )
}

/* ONE COURSE'S RUNS, foldable.
 *
 * A course with thirty documents in it printed all thirty, and three courses printed
 * ninety — so the page you open to find one document is the longest in the app and the
 * course you want is somewhere below the fold. The head carries the count and the spend,
 * which is what most visits are actually after, and the table is a click away.
 *
 * The FIRST group is open: landing on a page of nothing but headers reads as a page that
 * failed to load.
 */
function CourseGroup({ icon, title, meta, defaultOpen, children }) {
  const [open, setOpen] = useState(!!defaultOpen)
  return (
    <div className={`coursegroup ${open ? 'open' : ''}`}>
      <button className="coursehead" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="chead-chev"><Icon name="chevron" size={12} /></span>
        <Icon name={icon} size={15} />
        <b>{title}</b>
        <span className="muted">{meta}</span>
      </button>
      {open && children}
    </div>
  )
}

function MyTeams({ teams }) {
  return (
    <section className="card">
      <h2><span className="hicon"><Icon name="team" /></span> My Teams</h2>
      {teams.map((t, i) => (
        <CourseGroup key={i} icon="team" title={t.team.name} defaultOpen={i === 0}
                     meta={`${t.team.course || 'no course'} · ${t.members.length} member(s): ${t.members.join(', ')}`}>
          {t.courses.length === 0
            ? <div className="just" style={{ padding: '4px 2px' }}>No docs built by the team yet.</div>
            : t.courses.map((c, j) => <RunTable key={j} runs={c.runs} />)}
        </CourseGroup>
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
          <span className="dashcell st">Status</span>
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
                <span className="dashtitle">
                  <span className="tw"><Icon name="chevron" size={11}
                        style={{ transform: isOpen ? 'rotate(90deg)' : 'none' }} /></span>
                  S{r.session_no}: {r.title}
                  {r.enforce_time === false && <span className="tag" style={{ marginLeft: 6 }}>depth</span>}
                  <span className="uref"> · {r.user_email || 'unknown'}</span>
                </span>
                {/* WHERE THE RUN HAS GOT TO. `stage` is the last log line, up to 120
                    characters, and it was being rendered inside a 66px status cell —
                    so every running document showed "⚠ The ch…" and the one thing the
                    column existed to say was the thing it cut off. The sentence belongs
                    in the wide column; the chip stays a chip. */}
                {r.status === 'running' && r.stage && (
                  <span className="dashstage" title={r.stage}>{r.stage}</span>)}
              </span>
              <span className="dashcell st">
                {/* A doc the reviewer approved but the graders still flag is the
                    NORMAL case, so a red "review" chip on it was misleading — it is
                    approved and shipped. The chip states the human decision; the
                    graders' verdict rides along as a note. */}
                {r.status === 'running' ? <span className="chip mid" title={r.stage || ''}>● running</span>
                  : r.status === 'error' ? <span className="chip bad">error</span>
                  : r.approved ? <span className="chip good"><Icon name="check" size={12} /> approved{r.gates_passed === false && <span className="ms"> · flagged</span>}</span>
                  : <span className="chip bad">not approved</span>}
              </span>
              <span className="dashcell">{r.rubric != null ? `${r.rubric}` : '—'}</span>
              <span className="dashcell">${((r.cost || {}).cost || 0).toFixed(4)}</span>
              <span className="dashcell">
                {done ? <a href="#" onClick={(e) => { e.preventDefault(); api.downloadDoc(r.session_no, r.id, r.docx_name).catch((err) => alert(err.message)) }}><Icon name="download" size={13} /> .docx</a> : '—'}
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
        <div className="brand big">
          <span className="bmark" aria-hidden="true"><Icon name="doc" size={22} /></span>
          <b>TR Doc Generator</b>
        </div>
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
        <span className="tsidetitle"><Icon name="doc" size={14} /> Sheet templates</span>
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
function CurriculumDashboard({ course, rows, setRows, onSave, onDelete, onInsert, onIngest,
                               saving, ingesting, dirty, pending,
                               logs, teams = [], sharing, onShare,
                               budget, onBudget }) {
  function edit(i, field, value) {
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, [field]: value, _dirty: true } : r)))
  }
  // Insert a blank session AT a position — end of the list, or between any two rows.
  // The only add button used to be at the top, so adding a session to a 34-row course
  // meant scrolling back up every time.
  //
  // THE NEW ROW TAKES THE POSITION'S NUMBER, and everything below it moves down one.
  // The first version handed it the next FREE number instead, to avoid disturbing the
  // rows below — which meant inserting at the top of a 34-session course produced
  // "Session 35" sitting above Session 1. A curriculum is an ordered list: the row you
  // put first IS session 1. The shift happens on the server, in one operation, because
  // it moves each row's extracted deck along with it (a deck is filed under its session
  // number, so left behind it would become the wrong session's "already taught"),
  // and because two rows must never briefly share a number.
  function addRowAt(index) {
    // Inserting ABOVE row `index` means taking that row's number; at the end, one past
    // the highest. Numbers need not be contiguous in a hand-edited course, so this
    // reads the neighbour rather than assuming index + 1.
    const at = index < rows.length
      ? Number(rows[index].session_no)
      : (rows.length ? Math.max(...rows.map((r) => Number(r.session_no))) + 1 : 1)
    onInsert?.(at)
  }
  // Two rows claiming the same number are the SAME row as far as the database is
  // concerned (course + session number is the key), so saving would silently overwrite
  // one with the other. Say so before that happens.
  const dupNumbers = (() => {
    const seen = new Map()
    rows.forEach((r) => seen.set(Number(r.session_no), (seen.get(Number(r.session_no)) || 0) + 1))
    return [...seen.entries()].filter(([, n]) => n > 1).map(([no]) => no)
  })()
  const deckChip = (r) => {
    if (!r.ppt_link) return <span className="chip">no deck</span>
    if (r.extracted) return <span className="chip good" title="Already extracted — syncing will not download it again">extracted</span>
    return <span className="chip mid" title="Will be fetched by 'Fetch new decks'">pending</span>
  }
  return (
    <section className="card">
      <h2><span className="hicon"><Icon name="curriculum" /></span> Curriculum — {course}</h2>
      <p className="hint">
        This is the agent's own copy of the course. Edit a session, add a new one, or
        paste a deck link and press <b>Save</b>. Decks are downloaded <b>once per link</b>,
        so saving an edit never re-fetches anything.
      </p>

      <div className="curactions">
        <button className="primary" disabled={!dirty || saving || dupNumbers.length > 0}
                onClick={onSave}
                title={dupNumbers.length ? `Two rows share session ${dupNumbers.join(', ')}` : ''}>
          {saving ? 'Saving…' : <><Icon name="save" /> Save changes</>}
        </button>
        <button className="ghostbtn" disabled={ingesting || !pending} onClick={() => onIngest(false)}
                title="Downloads only decks that are new or whose link changed">
          {ingesting ? 'Fetching…'
            : <><Icon name="download" /> Fetch new decks{pending ? ` (${pending})` : ''}</>}
        </button>
        {/* "Re-check all decks" (onIngest(true) — a forced re-download of EVERY deck)
            was removed from this row. The endpoint still takes `force`; nothing in the
            UI asks for it. "Fetch new decks" covers the case that actually comes up. */}
        {/* The course's length budget moved OUT of here into its own Settings section:
            it is set once per course, not something you touch while editing sessions,
            and as a labelled number box wedged between the action buttons it dominated
            a row meant for actions. The per-session override stays in the table, where
            a value that belongs to one row belongs. */}
        <span className="curspacer" />
        {/* Hand a solo course to a team. Offered only while a team exists that does not
            already own it — once shared there is nothing left to do here. */}
        {teams.filter((t) => !(t.courses || []).includes(course)).length > 0 && (
          <span className="sharebox">
            <span className="hint">Share with</span>
            <select disabled={sharing} defaultValue=""
                    onChange={(e) => { onShare(e.target.value); e.target.value = '' }}>
              <option value="" disabled>a team…</option>
              {teams.filter((t) => !(t.courses || []).includes(course)).map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </span>
        )}
        {/* Re-import-from-sheet was removed from this row too. The sheet is an import
            FORMAT, not a live dependency: it seeds a course once and the curriculum is
            edited here afterwards, so re-reading it belongs on the create path, not on
            the toolbar of the table you are editing. */}
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
          <React.Fragment key={r._key ?? `${r.session_no}-${i}`}>
          {/* Insert ABOVE this row. A thin strip rather than a button per row, so it is
              there when wanted and invisible when not. */}
          <button className="insertbar" onClick={() => addRowAt(i)}
                  title={`Insert a session above ${r.session_name || `#${r.session_no}`}`}>
            <span><Icon name="plus" size={11} /> insert here</span>
          </button>
          <div className={`currow ${r._dirty ? 'dirty' : ''} ${dupNumbers.includes(Number(r.session_no)) ? 'dupe' : ''}`}>
            <input className="c-no" type="number" value={r.session_no}
                   onChange={(e) => edit(i, 'session_no', Number(e.target.value))} />
            <input className="c-topic" value={r.topic || ''} placeholder="Topic"
                   onChange={(e) => edit(i, 'topic', e.target.value)} />
            <input className="c-name" value={r.session_name || ''} placeholder="Session name"
                   onChange={(e) => edit(i, 'session_name', e.target.value)} />
            <AutoTextarea className="c-kt" minRows={3}
                      value={(r.key_takeaways || []).join('\n')}
                      placeholder={'1. Topic: sub-topic; sub-topic\n2. Topic: sub-topic'}
                      title="Each line becomes an agenda item, a section and a Key Takeaway verbatim. Everything after the colon is a promise the session must teach."
                      onChange={(e) => edit(i, 'key_takeaways', e.target.value.split('\n'))} />
            <textarea className="c-ppt" rows={2} value={r.ppt_link || ''}
                      placeholder="https://docs.google.com/presentation/d/…"
                      title="The deck for this session, if it has been recorded. Blank means the session still needs a TR doc. Changing this link is the only thing that makes the agent download a deck again."
                      onChange={(e) => edit(i, 'ppt_link', e.target.value)} />
            {/* Per-session budget overrides are NOT here. Two more columns took the
                row to nine cells against a seven-column grid, so Deck and ✕ wrapped onto
                a line of their own and the sheet stopped reading as a sheet. They are a
                rare adjustment, so they live in Settings; this table stays the course. */}
            <span className="c-deck">{deckChip(r)}</span>
            <span className="c-act">
              <button className="ghostbtn tiny" title="Remove this session"
                      onClick={() => onDelete(r, i)} title="remove this session">
                        <Icon name="x" size={13} /></button>
            </span>
          </div>
          </React.Fragment>
        ))}
        {/* …and at the END, where you are once you have read to the bottom. */}
        <button className="insertbar last" onClick={() => addRowAt(rows.length)}>
          <span><Icon name="plus" size={13} /> Add a session at the end</span>
        </button>
        {rows.length === 0 && (
          <div className="hint curempty">
            No curriculum yet — import one from a sheet, or add sessions by hand.
          </div>
        )}
      </div>
      {dupNumbers.length > 0 && (
        <div className="alert warn">
          <b>Two rows share session number {dupNumbers.join(', ')}.</b> A session is
          identified by its number, so saving would overwrite one with the other. Give
          each row its own number first.
        </div>
      )}
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
        <span className="tsidetitle"><Icon name="search" size={14} /> Extraction gaps ({warnings.length})</span>
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
        <span className="csidetitle"><Icon name="coin" size={14} /> This TR Doc</span>
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
      <summary><Icon name="coin" /> Cost breakdown
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
              <Icon name="warn" /> Below the per-dimension bar, which fails the run on its own:{' '}
              <b>{belowBar.map((r) => pretty(r.dim)).join(', ')}</b>
            </div>
          )}
        </div>
      )}
      {/* WHEN THE JUDGE MISCOUNTED. A claim about something the code already measured —
          how many items a bullet list has, how many sentences a speaker note is — is
          true or false, not a matter of taste. Session 33 was failed for a "one-item
          bullet list" on a slide holding four, and nothing said so: the score just came
          back low. Now the contradiction is checked, corrected, and shown, because a
          reviewer reading a low score deserves to know part of it was arithmetic. */}
      {(judge.contradicted_claims || []).length > 0 && (
        <div className="rubricblock judgefix">
          <Icon name="warn" /> The judge contradicted a check the code had already run and passed
          {' '}— re-graded, and the claims below were not counted against this document:
          <ul>
            {judge.contradicted_claims.map((c, i) => (
              <li key={i}>
                <span className="muted">{c.where}</span> — {c.contradicts.join('; ')}
                {c.claim && <div className="judgeclaim">“{c.claim}”</div>}
                <span className="muted"> [{c.action}]</span>
              </li>
            ))}
          </ul>
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

/* `standalone` is the Agent-rules TAB; without it this is the collapsible panel under a
   finished document. Same content, two very different jobs: as a tab it is the whole
   page and must not arrive collapsed behind a summary line, and as a panel under a
   result it must stay out of the way until asked for. It used to render the collapsed
   form in both places, so the tab was a single grey strip with the page empty below. */
/* A textarea that is as tall as what is in it.
 *
 * The curriculum's takeaway cell sized itself with `rows={number of takeaways}`, which
 * counts LOGICAL lines. In a 280px column a takeaway wraps to two or three visual ones,
 * so a three-takeaway session got three rows and showed two and a half of them — the
 * third clipped mid-word, with an inner scrollbar as the only clue that anything was
 * missing. The one thing a curriculum table has to do is show the curriculum.
 */
function AutoTextarea({ value, minRows = 2, ...rest }) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'                       // shrink first, or it only ever grows
    el.style.height = `${el.scrollHeight}px`
  }, [value])
  return <textarea ref={ref} rows={minRows} value={value} {...rest} />
}

function LearnedRules({ rules, sessionNo, course, isAdmin, onChanged, standalone }) {
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
  // Flip a rule between house style and subject matter. The rule itself — its wording,
  // your original note, its hit count — is untouched; only where it applies moves.
  function rescope(i, r) {
    const to = r.scope === 'course' ? 'global' : 'course'
    const target = r.course || course
    if (to === 'course' && !target) {
      alert('This rule does not record which course it was learned on, so it cannot be '
            + 'narrowed to one.')
      return
    }
    const q = to === 'global'
      ? `Apply this rule to EVERY course, including ones created later?\n\n${r.text}`
      : `Apply this rule only to “${target}”, and to no other course?\n\n${r.text}`
    if (!window.confirm(q)) return
    setBusy(i)
    api.setLearnedRuleScope(i, to, target).then(() => onChanged && onChanged())
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
  const Shell = standalone ? 'section' : 'details'
  const Head = standalone ? 'header' : 'summary'
  return (
    <Shell className={standalone ? 'card learned' : 'panel learned'}
           {...(standalone ? {} : { open: newCount > 0 })}>
      <Head className={standalone ? 'learnhead' : undefined}>
        {standalone
          ? <h2><span className="hicon"><Icon name="brain" /></span> Agent rules</h2>
          : <><Icon name="brain" /> What the agent has learned</>}
        <span className="muted">
          {standalone ? '' : ' — '}{applied} of {rules.length} rule{rules.length === 1 ? '' : 's'} applied to <b>{course || 'this course'}</b>
        </span>
        {newCount > 0 && <span className="chip good" style={{ marginLeft: 8 }}>+{newCount} this run</span>}
      </Head>
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
                    {migrating ? 'Migrating…' : <><Icon name="wrench" /> Distil &amp; scope them</>}
                  </button>
                : <div className="hint">An admin needs to run this.</div>}
            </div>
          )}
          {rules.map((r, i) => (
            <div key={i} className={`setrow ${r.session_no === sessionNo ? 'pass' : ''}`}
                 style={r.applies === false ? { opacity: 0.45 } : undefined}>
              <div className="setmain">
                <span className="tag">{srcLabel[r.source] || r.source || 'rule'}</span>
                {/* CLICKABLE. The house/course split is decided by a model at distil
                    time and it misjudges: a note about one topic ("working examples are
                    not needed for this topic") became a house rule binding every course
                    on the instance. Deleting was the only lever, and it is the wrong one
                    — it destroys the rule for the course that did ask for it. */}
                <button className="tag" disabled={busy === i}
                        title={r.scope === 'course'
                          ? `Subject-matter rule — applies only to ${r.course || 'its course'}. `
                            + 'Click to make it house style, applying to every course.'
                          : 'House-style rule — applies to every course. Click to narrow it '
                            + `to ${r.course || 'the course it was learned on'} alone.`}
                        onClick={() => rescope(i, r)}>
                  {r.scope === 'course' ? `course · ${r.course || '?'}` : 'house'}
                </button>
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
                        onClick={() => remove(i, r.text)} title="remove this rule">
                        <Icon name="x" size={13} /></button>
              </div>
              {r.gated && <div className="just">Now enforced by {r.gated} — a hard gate rather than a prompt instruction.</div>}
              {r.raw && <div className="just">you wrote: “{r.raw}”</div>}
              {r.session_no != null && <div className="just">learned at Session {r.session_no}</div>}
            </div>
          ))}
        </div>
      )}
    </Shell>
  )
}

// The reviewer's conversation with the agent about ONE section.
//
// Deliberately not a general chatbot. It is anchored to the section on screen, it holds
// no power over the document, and it says so: the whole value is that asking is free.
// A reviewer who cannot ask has only one move when something looks off — reject and
// re-roll — and a disagreement that was never a disagreement costs a full regeneration.
function ChunkChat({ messages, pending, open, text, web, asking, onOpen, onClose,
                     onText, onWeb, onSend, canRegen, onUseAsFeedback,
                     onMakeSkill, rulePosted = {}, scope = 'section', stage = null }) {
  const whole = scope === 'document'
  const has = messages.length > 0
  // The answer being written is always for the LAST question, so the spinner belongs
  // under it — not floating at the bottom of a panel that may be scrolled away.
  const awaiting = pending && messages.length > 0
    && messages[messages.length - 1].role === 'user'
  // COLLAPSED whenever `open` is false — including when there is a conversation to
  // collapse. The condition here used to be `!has && !open`, so as soon as a section had
  // one exchange the expanded panel was the only thing that could render: Close set the
  // flag, the flag was ignored, and the button did nothing at all. A control that does
  // nothing is worse than no control, because you go looking for what you broke.
  if (!open) {
    const n = messages.length
    return (
      <div className={`chunkchat-cta${whole ? ' doclevel' : ''}`}>
        <button className="ghostbtn tiny" onClick={onOpen}>
          <Icon name="chat" /> {has
            ? `${n} message${n === 1 ? '' : 's'} about ${whole ? 'this document' : 'this section'} — reopen`
            : whole ? 'Ask about the whole document' : 'Ask about this section'}
        </button>
        {/* The pitch is for someone who has not asked yet. Once they have, the button
            says what is behind it and the sales copy would just be noise. */}
        {!has && (
          <span className="hint">
            {whole
              ? `Why a topic sits in one section and not another, whether the document `
                + `hangs together, what it covers as a whole. The division follows the `
                + `curriculum's own key takeaways, so this is usually a question about `
                + `which line owns what.`
              : `Why it says what it says, where something came from, whether it matches `
                + `how the topic is normally taught. It answers from the material this `
                + `section was written from — it cannot change anything.`}
          </span>
        )}
        {/* Collapsing must never look like deleting. The conversation is checkpointed
            with the run and comes back exactly as it was. */}
        {has && <span className="hint">Kept — nothing is lost by closing it.</span>}
      </div>
    )
  }
  return (
    <div className="chunkchat">
      {has && (
        <div className="chatlog">
          {messages.map((m) => (
            <div key={m.id} className={`chatmsg ${m.role}${m.failed ? ' failed' : ''}`}>
              <span className="who">{m.role === 'user' ? 'You' : 'Agent'}</span>
              <div className="body">
                {m.role === 'agent'
                  ? <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown></div>
                  : m.text}
                {/* Marked, because an answer that checked live sources and one that
                    reasoned from the document are different kinds of claim and the
                    reviewer is entitled to tell them apart. */}
                {m.role === 'agent' && m.web && !m.failed && (
                  <span className="chip" title="This answer used a live web search alongside the section's own source material.">web-checked</span>
                )}
                {/* THE PAGES IT READ. Parsed from the answer's own citations, so this is
                    the model's claim about where a fact came from — checkable by
                    clicking, which is the point of listing them rather than leaving them
                    buried in the prose. */}
                {m.sources?.length > 0 && (
                  <div className="chatsources">
                    <b>Read on the web</b>
                    <ul>
                      {m.sources.map((sc, k) => (
                        <li key={k}>
                          <a href={sc.url} target="_blank" rel="noreferrer noopener">
                            {sc.title || sc.url}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {/* WHAT IT LOOKED AT. Assembled by the code that built the question's
                    context — not the model's account of its own reasoning, which is the
                    last thing to trust here. Every row is checkable: a deck slide is
                    named by session and number, the brief either was in force or wasn't. */}
                {m.consulted?.length > 0 && (
                  <details className="chatconsulted">
                    <summary>What it looked at ({m.consulted.length})</summary>
                    <ul>
                      {m.consulted.map((c, k) => (
                        <li key={k}><span className="tag">{c.kind}</span> {c.label}</li>
                      ))}
                    </ul>
                  </details>
                )}
                {m.suggested_feedback && (
                  <div className="chatsuggest">
                    <b>It thinks this one should change:</b>
                    <div className="sf">“{m.suggested_feedback}”</div>
                    {canRegen
                      ? <div className="gactions">
                          <button className="btn sm primary"
                                  onClick={() => onUseAsFeedback(m.suggested_feedback, false)}>
                            Put this in the regenerate box
                          </button>
                          {/* The same note, but marked to govern every later section
                              too. It is the existing standing-note mechanism, reached
                              from the conversation that produced the note instead of
                              being retyped into the box by hand. */}
                          <button className="ghostbtn tiny"
                                  onClick={() => onUseAsFeedback(m.suggested_feedback, true)}>
                            …and apply it to every later section
                          </button>
                        </div>
                      : <span className="hint">{whole
                          ? 'This is about the document as a whole, so there is no one '
                            + 'section to regenerate — open the section it concerns and '
                            + 'regenerate it there with this as the reason.'
                          : 'This run is finished, so it is a note for next time rather '
                            + 'than something to act on here.'}</span>}
                    <span className="hint">You can edit it before regenerating — and
                      ignore it entirely if you disagree.</span>
                  </div>
                )}
                {/* A STANDING preference, not a fix. Kept visually apart from the one
                    above because they do opposite things: that changes this document,
                    this changes every future one. It becomes a DRAFT — the same
                    approval step every other skill goes through — so a conversation can
                    never quietly rewrite what the course is written under. */}
                {m.suggested_rule && (
                  <div className="chatsuggest rule">
                    <b>This sounds like a rule for the whole course:</b>
                    <div className="sf">“{m.suggested_rule}”</div>
                    <button className="btn sm" disabled={rulePosted[m.id] === 'busy'}
                            onClick={() => onMakeSkill(m.id, m.suggested_rule)}>
                      {rulePosted[m.id] === 'done'
                        ? 'Added as a draft skill'
                        : rulePosted[m.id] === 'busy' ? 'Adding…'
                        : 'Propose it as a course skill'}
                    </button>
                    <span className="hint">
                      It arrives as a draft under <b>Skills</b> and changes nothing
                      until you approve it there — where you can also reword it.
                    </span>
                  </div>
                )}
              </div>
            </div>
          ))}
          {awaiting && <ChatProgress stage={stage} web={web} />}
        </div>
      )}
      <div className="chatbox">
        <textarea rows={2} value={text} disabled={asking || pending}
                  onChange={(e) => onText(e.target.value)}
                  onKeyDown={(e) => {
                    // Enter sends, Shift+Enter for a new line — the convention everywhere
                    // else people type a question.
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend() }
                  }}
                  placeholder={whole
                    ? 'Ask anything about the document as a whole — why a topic is here and not there, what it covers, whether it holds together…'
                    : 'Ask anything about this section — why it says this, where it came from, what it left out…'} />
        <div className="gactions">
          <button className="primary" disabled={asking || pending || !text.trim()}
                  onClick={onSend}>{pending ? 'Answering…' : 'Ask'}</button>
          <label className="checkline" title="Checks the answer against live sources. Turn it off for questions about this document's own choices, where the web has nothing to add.">
            <input type="checkbox" checked={web} disabled={asking || pending}
                   onChange={(e) => onWeb(e.target.checked)} />
            <span>Check the web too</span>
          </label>
          <button className="ghostbtn tiny" onClick={onClose}>Close</button>
        </div>
        <span className="hint">
          Read-only. Asking never edits, regenerates or approves anything — if the answer
          doesn't convince you, regenerate with a reason exactly as before.
        </span>
      </div>
    </div>
  )
}

// What the answer in flight is doing, from the stages the server actually reports.
//
// The sequence is fixed and known, so the steps before the current one are genuinely
// done and the ones after genuinely have not started — which is why they can be drawn as
// a list rather than as a spinner. Nothing here is on a timer: an invented
// "reading geeksforgeeks.org…" would be theatre, and a panel whose whole value is that
// it does not overclaim cannot afford to start there.
const CHAT_STEPS = [
  { name: 'reading',  label: 'Reading what this section was written from' },
  { name: 'gathered', label: 'Gathering the evidence' },
  { name: 'asking',   label: 'Working out the answer' },
]
function ChatProgress({ stage, web }) {
  const at = CHAT_STEPS.findIndex((s) => s.name === (stage?.name || 'reading'))
  const cur = at < 0 ? 0 : at
  return (
    <div className="chatprogress">
      {CHAT_STEPS.map((s, i) => (
        <div key={s.name} className={`cpstep ${i < cur ? 'done' : i === cur ? 'now' : 'todo'}`}>
          <span className="cpmark">{i < cur ? '✓' : i === cur ? '•' : '·'}</span>
          <span className="cplabel">
            {/* The web half only exists when the box is ticked, so say so rather than
                implying a search that is not happening. */}
            {s.name === 'asking' && web ? 'Searching the web and weighing it against the document' : s.label}
          </span>
          {i === cur && stage?.detail && <span className="cpdetail">{stage.detail}</span>}
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
