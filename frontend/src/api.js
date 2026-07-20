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
    const msg = typeof detail === 'string'
      ? detail
      : detail.message || `Request failed (HTTP ${res.status}). Is the backend (server.py) running?`
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
  createGdoc: (session_no, access_token) =>
    req(`/gdoc/${session_no}`, { method: 'POST', body: JSON.stringify({ access_token }) }),
  status: () => req('/status'),
  templateGuide: () => req('/template-guide'),
  sync: (course_link, details_link, reference_date, course_type, course_name) =>
    req('/sync', { method: 'POST', body: JSON.stringify({ course_link, details_link, reference_date, course_type, course_name }) }),
  sessions: () => req('/sessions'),
  generate: (session_no, use_judge, enforce_time) =>
    req('/generate', { method: 'POST', body: JSON.stringify({ session_no, use_judge, enforce_time }) }),
  job: (id) => req(`/jobs/${id}`),
  downloadUrl: (session_no) => `/api/download/${session_no}`,

  // Download the .docx via fetch so the auth token is sent (a plain <a href>
  // navigation can't carry the Authorization header, so it would 401).
  downloadDoc: async (session_no) => {
    const res = await fetch(`/api/download/${session_no}`, {
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
    const name = m ? decodeURIComponent(m[1].replace(/"$/, '')) : `Session_${session_no}.docx`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = name
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  },

  // Guided mode: generate all chunks -> review each -> finalize
  guidedStart: (session_no, use_judge) =>
    req('/guided/start', { method: 'POST', body: JSON.stringify({ session_no, use_judge }) }),
  guidedState: (id) => req(`/guided/${id}`),
  guidedRegenerate: (id, index, reason) =>
    req(`/guided/${id}/regenerate`, { method: 'POST', body: JSON.stringify({ index, reason }) }),
  guidedFinalize: (id) => req(`/guided/${id}/finalize`, { method: 'POST' }),

  learnedRules: () => req('/learned-rules'),

  dashboard: () => req('/dashboard'),

  evalSets: (session_no, use_llm, enforce_time) =>
    req('/eval-sets', { method: 'POST', body: JSON.stringify({ session_no, use_llm, enforce_time }) }),
}
