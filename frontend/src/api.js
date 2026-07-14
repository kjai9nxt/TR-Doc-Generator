// Thin fetch wrapper around the FastAPI backend (proxied at /api).

async function req(path, opts = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
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
    const err = new Error(msg)
    err.kind = detail.kind
    err.status = res.status
    throw err
  }
  return data
}

export const api = {
  status: () => req('/status'),
  templateGuide: () => req('/template-guide'),
  sync: (course_link, details_link) =>
    req('/sync', { method: 'POST', body: JSON.stringify({ course_link, details_link }) }),
  sessions: () => req('/sessions'),
  generate: (session_no, use_judge, enforce_time) =>
    req('/generate', { method: 'POST', body: JSON.stringify({ session_no, use_judge, enforce_time }) }),
  job: (id) => req(`/jobs/${id}`),
  downloadUrl: (session_no) => `/api/download/${session_no}`,

  // Guided mode: generate all chunks -> review each -> finalize
  guidedStart: (session_no, use_judge) =>
    req('/guided/start', { method: 'POST', body: JSON.stringify({ session_no, use_judge }) }),
  guidedState: (id) => req(`/guided/${id}`),
  guidedRegenerate: (id, index, reason) =>
    req(`/guided/${id}/regenerate`, { method: 'POST', body: JSON.stringify({ index, reason }) }),
  guidedFinalize: (id) => req(`/guided/${id}/finalize`, { method: 'POST' }),

  learnedRules: () => req('/learned-rules'),

  evalSets: (session_no, use_llm, enforce_time) =>
    req('/eval-sets', { method: 'POST', body: JSON.stringify({ session_no, use_llm, enforce_time }) }),
}
