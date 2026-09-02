// Thin fetch wrapper around the FastAPI backend (proxied at /api).

// Google ID token (JWT) for the signed-in user, attached as a Bearer header on
// every request. Set by App after sign-in; restored from localStorage on load.
let authToken = localStorage.getItem('tr_auth_token') || ''
export function setAuthToken(t) {
  authToken = t || ''
  if (t) localStorage.setItem('tr_auth_token', t)
  else localStorage.removeItem('tr_auth_token')
}
// Called on a 401 so the app can bounce back to the login screen.
let onUnauthorized = () => {}
export function setOnUnauthorized(fn) { onUnauthorized = fn || (() => {}) }

// RENEWING THE SIGN-IN BEFORE IT LAPSES.
//
// A Google ID token is valid for one hour and nothing here ever refreshed it. After an
// hour the next request — whichever it happened to be — came back 401, the handler above
// wiped the session, and the login screen replaced the page along with anything typed
// into it. It bit the drafting button hardest because that is the longest request in the
// app, so it was the one most likely to be the first across the line.
//
// The fix is to renew BEFORE the token expires rather than react after it has. The
// callback is supplied by App (Google Identity Services can hand back a fresh credential
// without any user interaction when the session is still good).
let renew = null
export function setRenewCredential(fn) { renew = fn || null }

function tokenExpiry(jwt) {
  try {
    const payload = JSON.parse(atob(jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return Number(payload.exp) * 1000 || 0
  } catch { return 0 }
}

// Renew when under two minutes remain. Long enough to cover a slow request that starts
// just before the boundary, short enough that we are not asking Google constantly.
const RENEW_MARGIN_MS = 120000
let renewing = null

async function freshToken() {
  if (!authToken || !renew) return authToken
  const exp = tokenExpiry(authToken)
  if (!exp || exp - Date.now() > RENEW_MARGIN_MS) return authToken
  // One renewal at a time — several requests firing at once must not each start one.
  if (!renewing) {
    renewing = Promise.resolve()
      .then(() => renew())
      .then((t) => { if (t) setAuthToken(t); return t })
      .catch(() => null)
      .finally(() => { renewing = null })
  }
  await renewing
  return authToken
}

// Build a query string from the defined values only, so `?run_id=undefined` never
// reaches the server (it would be treated as a real run id and resolve nothing).
function qs(params) {
  const q = Object.entries(params || {})
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
  return q.length ? `?${q.join('&')}` : ''
}

async function req(path, opts = {}) {
  let res
  await freshToken()
  try {
    res = await fetch(`/api${path}`, {
      headers: {
        'Content-Type': 'application/json',
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      ...opts,
    })
  } catch (e) {
    // fetch itself failed -> the backend isn't reachable
    const err = new Error(
      'Cannot reach the backend API. Start it first:  python3 server.py  (it must be running on port 8000).')
    err.kind = 'backend'
    throw err
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = data.detail || data
    // Name the request. "Request failed (HTTP 500)" told nobody which call broke, so
    // a server error could only be guessed at from a screenshot.
    // A status with no `detail.message` did not come from the app — it came from the
    // platform in front of it, and "is the backend running?" is the wrong thing to tell
    // someone whose page the backend just served. 502/503/504 mean the request never got
    // an answer: the instance is waking up, or the request took longer than the proxy
    // allows. Say that instead of sending them to check a server that is plainly up.
    const gateway = res.status === 502 || res.status === 503 || res.status === 504
    // 429 is the HOST rate-limiting this browser, not a fault in the app and certainly
    // not the backend being down — it is plainly up, it just declined to answer this
    // one. Sending someone to check whether server.py is running (which is what the
    // generic message did) is the most misleading thing we could say: it arrives during
    // a long job, while the work behind it is still going, and it reads as "everything
    // is broken" when the correct reading is "ask again in a moment".
    const throttled = res.status === 429
    const msg = typeof detail === 'string'
      ? detail
      : detail.message
        || (throttled
          ? `The server is rate-limiting this page (HTTP 429) on ${path} — too many `
            + `requests in a short window. It is running, and anything already in `
            + `progress is unaffected; it just stopped answering status checks for a `
            + `moment. Wait a few seconds and try again.`
          : gateway
          ? `The server did not answer in time (HTTP ${res.status}) on ${path}. It may be `
            + `waking up, or that request took too long. Nothing was necessarily left `
            + `half-done — reload and check before trying again.`
          : `Request failed (HTTP ${res.status}) on ${path}. Is the backend (server.py) running?`)
    // A 401 means this credential is finished. Try ONE silent renewal and replay the
    // request before throwing the session away — the token may simply have lapsed while
    // the page sat open, and the person is still signed in to Google.
    if (res.status === 401) {
      if (renew && !opts._retried) {
        const t = await Promise.resolve(renew()).catch(() => null)
        if (t) { setAuthToken(t); return req(path, { ...opts, _retried: true }) }
      }
      onUnauthorized()
    }
    const err = new Error(msg)
    err.kind = detail.kind
    err.status = res.status
    // The whole detail object, not just its message: a refusal can carry the facts the
    // caller needs to act on it — the teams a shared course is on, for instance — and
    // those were being thrown away with the response.
    err.detail = detail
    throw err
  }
  return data
}

export const api = {
  authConfig: () => req('/auth/config'),
  login: (credential) => req('/auth/login', { method: 'POST', body: JSON.stringify({ credential }) }),
  me: () => req('/auth/me'),
  myHistory: () => req('/my/history'),
  myTeams: () => req('/my/teams'),
  // run_id / name identify the OUTPUT exactly. Without them the server has to guess
  // the filename from whatever course is synced right now, which is what made both
  // this and the download fail on finished documents.
  createGdoc: (session_no, access_token, run_id, name) =>
    req(`/gdoc/${session_no}`, { method: 'POST', body: JSON.stringify({ access_token, run_id, name }) }),
  preview: (session_no, run_id, name) => req(`/preview/${session_no}${qs({ run_id, name })}`),
  status: () => req('/status'),
  templateGuide: () => req('/template-guide'),
  // ONE sheet: the curriculum, whose "PPT Links" column carries each session's deck.
  sync: (course_link, course_type, course_name) =>
    req('/sync', { method: 'POST', body: JSON.stringify({ course_link, course_type, course_name }) }),
  sessions: (course) => req(`/sessions${qs({ course })}`),

  // Courses this person may work on — their teams' courses. A course one member
  // imports is the course everyone on that team opens.
  // ONE request for everything the page needs to draw itself. Opening the app used to
  // fire eight, each re-reading tables the others had just read.
  bootstrap: (course) => req(`/bootstrap${qs({ course })}`),
  courses: () => req('/courses'),
  // Where this person can work: alone, or inside each of their teams.
  workspaces: () => req('/workspaces'),
  // Attach a course to a team, so a course created inside a team workspace is the
  // team's from the moment it exists rather than the creator's alone.
  teamAddCourse: (team_id, course) =>
    req(`/teams/${team_id}/courses`, { method: 'POST', body: JSON.stringify({ course }) }),
  // MEMBERSHIP, delegated to the team's course owner. Every add and remove used to go
  // through the admin account, which made one person the bottleneck for a routine act.
  // The server allows these for an admin or the team's owner and nobody else.
  teamAddMember: (team_id, email) =>
    req(`/teams/${team_id}/members`, { method: 'POST', body: JSON.stringify({ email }) }),
  teamRemoveMember: (team_id, email) =>
    req(`/teams/${team_id}/members/${encodeURIComponent(email)}`, { method: 'DELETE' }),
  // Stop sharing a course with a team, WITHOUT deleting it. Admin or the team's owner.
  teamRemoveCourse: (team_id, course) =>
    req(`/teams/${team_id}/courses${qs({ course })}`, { method: 'DELETE' }),

  // Delete a course you own. The first call answers 409 with the teams it is shared
  // with, so the confirmation the user sees names them; detach_teams: true goes ahead.
  // Finished documents and their costs are KEPT either way — deleting the record would
  // not un-generate the docs, only make the history lie.
  deleteCourse: (course, detach_teams = false) =>
    req(`/courses${qs({ course, detach_teams: detach_teams ? 'true' : undefined })}`,
        { method: 'DELETE' }),
  selectCourse: (course, course_type) =>
    req('/courses/select', { method: 'POST', body: JSON.stringify({ course, course_type }) }),

  // The agent's own curriculum — the source of truth once a course has been imported.
  // The sheet is an import format; everything after that happens here. The course is
  // always explicit, so two people on different courses never write into each other's.
  curriculum: (course) => req(`/curriculum${qs({ course })}`),
  // WHAT THIS COURSE IS WRITTEN UNDER. Skills are authored instructions approved before
  // they take effect; prerequisites are what the learner already knew at session 1.
  skills: (course, include_retired = false) =>
    req(`/skills${qs({ course, include_retired: include_retired ? 'true' : undefined })}`),
  // `where` is {category, scope, session} — what the skill governs and where it applies.
  // Optional in full: a skill written with none of it is a course-wide, uncategorised
  // one, which is exactly what every skill was before scopes existed.
  addSkill: (course, text, check, where = {}) =>
    req('/skills', { method: 'POST',
        body: JSON.stringify({ course, text, check, ...where }) }),
  skillsFromRequirements: (course, requirements, where = {}) =>
    req('/skills/from-requirements', { method: 'POST',
        body: JSON.stringify({ course, requirements, ...where }) }),
  importSkills: (course, from_course) =>
    req('/skills/import', { method: 'POST',
        body: JSON.stringify({ course, from_course }) }),
  approveSkill: (course, id) =>
    req(`/skills/${id}/approve${qs({ course })}`, { method: 'POST' }),
  // `instructions` omitted means LEAVE THEM AS THEY ARE — editing a skill's own
  // sentence is not a decision to throw away the lines grouped under it.
  editSkill: (course, id, text, instructions) =>
    req(`/skills/${id}/edit`, { method: 'POST',
        body: JSON.stringify({ course, text, instructions }) }),
  retireSkill: (course, id) => req(`/skills/${id}${qs({ course })}`, { method: 'DELETE' }),
  prereqs: (course) => req(`/prereqs${qs({ course })}`),
  addPrereq: (course, prereq) =>
    req('/prereqs', { method: 'POST', body: JSON.stringify({ course, prereq }) }),
  // A prerequisite taught SOMEWHERE ELSE: a name and its deck links. Returns a job,
  // because fetching the decks takes about as long as a sync.
  addExternalPrereq: (course, name, links) =>
    req('/prereqs/external', { method: 'POST',
        body: JSON.stringify({ course, name, links }) }),
  removePrereq: (course, prereq) =>
    req(`/prereqs${qs({ course, prereq })}`, { method: 'DELETE' }),

  // A course's length budget (pages/slides), and what it inherits when unset.
  courseSettings: (course) => req(`/course-settings${qs({ course })}`),
  saveCourseSettings: (course, settings) =>
    req('/course-settings', { method: 'POST', body: JSON.stringify({ course, ...settings }) }),
  // One session's budget override, on its own — folding it into the curriculum save
  // would upsert the whole row and blank the session's name and takeaways.
  saveSessionSettings: (course, session_no, settings) =>
    req('/session-settings', { method: 'POST', body: JSON.stringify({ course, session_no, ...settings }) }),
  saveCurriculum: (rows, course) =>
    req('/curriculum', { method: 'POST', body: JSON.stringify({ rows, course }) }),
  // Insert a session AT a position: everything from there on moves down one, and each
  // row's extracted deck moves with it. A curriculum is an ordered list, so a row added
  // at the top is session 1 — not "the next free number", which put 35 above 1.
  insertCurriculumRow: (at_session_no, course) =>
    req('/curriculum/insert', { method: 'POST',
                                body: JSON.stringify({ at_session_no, course }) }),
  deleteCurriculumRow: (session_no, course) =>
    req(`/curriculum/${session_no}${qs({ course })}`, { method: 'DELETE' }),
  // Fetches ONLY decks that are new or whose link changed. force=true re-checks decks
  // whose link is unchanged — the only way to pick up an edit to the slides themselves.
  ingestDecks: (force = false, sessions = null, course = undefined) =>
    req('/curriculum/ingest', { method: 'POST', body: JSON.stringify({ force, sessions, course }) }),
  job: (id) => req(`/jobs/${id}`),
  downloadUrl: (session_no, run_id, name) => `/api/download/${session_no}${qs({ run_id, name })}`,

  // Download the .docx via fetch so the auth token is sent (a plain <a href>
  // navigation can't carry the Authorization header, so it would 401).
  // Always pass run_id/name when known: they pin the exact output file.
  downloadDoc: async (session_no, run_id, name) => {
    const res = await fetch(`/api/download/${session_no}${qs({ run_id, name })}`, {
      headers: { ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}) },
    })
    if (!res.ok) {
      // A 401 means this credential is finished. Try ONE silent renewal and replay the
    // request before throwing the session away — the token may simply have lapsed while
    // the page sat open, and the person is still signed in to Google.
    if (res.status === 401) {
      if (renew && !opts._retried) {
        const t = await Promise.resolve(renew()).catch(() => null)
        if (t) { setAuthToken(t); return req(path, { ...opts, _retried: true }) }
      }
      onUnauthorized()
    }
      const d = await res.json().catch(() => ({}))
      throw new Error((d.detail && (d.detail.message || d.detail)) || `Download failed (HTTP ${res.status})`)
    }
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const m = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(cd)
    // Prefer the name the server actually served, then the one we asked for.
    const saveAs = m ? decodeURIComponent(m[1].replace(/"$/, '')) : (name || `Session_${session_no}.docx`)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = saveAs
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  },

  // Guided generation — the only way a TR doc is written: generate all chunks ->
  // review each -> finalize.
  // team_id/course say which WORKSPACE the doc belongs to, so a doc made in a team is
  // stamped with that team and visible to every member — including one added later.
  guidedStart: (session_no, use_judge, enforce_time, team_id, course) =>
    req('/guided/start', { method: 'POST', body: JSON.stringify({ session_no, use_judge, enforce_time, team_id, course }) }),
  guidedState: (id) => req(`/guided/${id}`),
  // Unfinished runs for the signed-in USER, from the server's checkpoints — so the
  // resume offer survives a different browser, cleared site data or a new machine.
  guidedResumable: () => req('/guided/resumable'),
  // Ask the agent about one section — READ-ONLY. It answers from the inputs that
  // section was generated from and (optionally) the web; it cannot change anything.
  // Regeneration is still the only way the document moves, which is what keeps a
  // question free: asking one can never cost work already accepted.
  guidedAsk: (id, index, question, use_web = true) =>
    req(`/guided/${id}/ask`, { method: 'POST',
        body: JSON.stringify({ index, question, use_web }) }),
  // apply_to_following carries the note into every chunk AFTER this one as well, and
  // keeps it as a STANDING instruction so a later redraft of any of them still obeys it.
  guidedRegenerate: (id, index, reason, apply_to_following = false) =>
    req(`/guided/${id}/regenerate`, { method: 'POST',
        body: JSON.stringify({ index, reason, apply_to_following }) }),
  // Split one slide in two. Deterministic, no model call: the content is divided, not
  // rewritten, and every slide after it — in this chunk and the later ones — is
  // renumbered. Returns the fresh guided view.
  // Ticking a chunk as reviewed. The ticks used to live only in this browser, so a
  // reload lost the whole review — and the client was the only judge of whether every
  // chunk had been approved, which is the one condition for creating the document.
  guidedApproveChunk: (id, index, approved = true) =>
    req(`/guided/${id}/approve`, { method: 'POST',
        body: JSON.stringify({ index, approved }) }),
  guidedSplitSlide: (id, index, slide_n) =>
    req(`/guided/${id}/split`, { method: 'POST',
        body: JSON.stringify({ index, slide_n }) }),
  guidedFinalize: (id) => req(`/guided/${id}/finalize`, { method: 'POST' }),
  // Discarding is a decision about the RUN, recorded on the server — forgetting it only
  // in this browser meant the next page load fetched it straight back.
  guidedDiscard: (id) => req(`/guided/${id}/discard`, { method: 'POST' }),

  // Teach the agent from a FINISHED doc — a correction spotted after assembly, which
  // a per-chunk regeneration reason can no longer capture.
  // `course` is what the correction is ABOUT. Without it the server files the rule
  // against the instance-wide active course — whoever selected one last — so a
  // correction could govern a course the reviewer has never worked on and never reach
  // the one they were correcting.
  submitFeedback: (session_no, reason, course) =>
    req('/feedback', { method: 'POST',
        body: JSON.stringify({ session_no, reason, course }) }),
  learnedRules: () => req('/learned-rules'),
  deleteLearnedRule: (index) => req(`/learned-rules/${index}`, { method: 'DELETE' }),
  // House style vs subject matter is classified by a model and it gets it wrong. Moving
  // a rule is not the same as deleting it: demoting a wrongly-global rule used to mean
  // destroying it for the course that did ask for it.
  setLearnedRuleScope: (index, scope, course) =>
    req(`/learned-rules/${index}/scope`,
        { method: 'POST', body: JSON.stringify({ scope, course }) }),
  migrateLearnedRules: () => req('/learned-rules/migrate', { method: 'POST' }),

  dashboard: () => req('/dashboard'),

  // `course` is the curriculum the DOCUMENT was written from. Without it the server
  // falls back to the instance-wide active course — one global shared by everyone
  // signed in — so a colleague opening their course mid-review repointed the grading at
  // their brief, and the document was scored against rules its author never wrote.
  evalSets: (session_no, use_llm, enforce_time, course) =>
    req('/eval-sets', { method: 'POST', body: JSON.stringify({ session_no, use_llm, enforce_time, course }) }),
}
