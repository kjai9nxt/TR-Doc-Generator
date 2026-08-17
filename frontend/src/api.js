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
    const msg = typeof detail === 'string'
      ? detail
      : detail.message
        || `Request failed (HTTP ${res.status}) on ${path}. Is the backend (server.py) running?`
    if (res.status === 401) onUnauthorized()
    const err = new Error(msg)
    err.kind = detail.kind
    err.status = res.status
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
  courses: () => req('/courses'),
  // Where this person can work: alone, or inside each of their teams.
  workspaces: () => req('/workspaces'),
  // Attach a course to a team, so a course created inside a team workspace is the
  // team's from the moment it exists rather than the creator's alone.
  teamAddCourse: (team_id, course) =>
    req(`/teams/${team_id}/courses`, { method: 'POST', body: JSON.stringify({ course }) }),
  selectCourse: (course, course_type) =>
    req('/courses/select', { method: 'POST', body: JSON.stringify({ course, course_type }) }),

  // The agent's own curriculum — the source of truth once a course has been imported.
  // The sheet is an import format; everything after that happens here. The course is
  // always explicit, so two people on different courses never write into each other's.
  curriculum: (course) => req(`/curriculum${qs({ course })}`),
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
      if (res.status === 401) onUnauthorized()
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
  guidedRegenerate: (id, index, reason) =>
    req(`/guided/${id}/regenerate`, { method: 'POST', body: JSON.stringify({ index, reason }) }),
  guidedFinalize: (id) => req(`/guided/${id}/finalize`, { method: 'POST' }),
  // Discarding is a decision about the RUN, recorded on the server — forgetting it only
  // in this browser meant the next page load fetched it straight back.
  guidedDiscard: (id) => req(`/guided/${id}/discard`, { method: 'POST' }),

  // Teach the agent from a FINISHED doc — a correction spotted after assembly, which
  // a per-chunk regeneration reason can no longer capture.
  submitFeedback: (session_no, reason) =>
    req('/feedback', { method: 'POST', body: JSON.stringify({ session_no, reason }) }),
  learnedRules: () => req('/learned-rules'),
  deleteLearnedRule: (index) => req(`/learned-rules/${index}`, { method: 'DELETE' }),
  migrateLearnedRules: () => req('/learned-rules/migrate', { method: 'POST' }),

  dashboard: () => req('/dashboard'),

  evalSets: (session_no, use_llm, enforce_time) =>
    req('/eval-sets', { method: 'POST', body: JSON.stringify({ session_no, use_llm, enforce_time }) }),
}
