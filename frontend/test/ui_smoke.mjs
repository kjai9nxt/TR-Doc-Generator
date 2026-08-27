// UI SMOKE TEST — mounts the REAL App.jsx in a headless DOM (jsdom), stubs the API to
// look like a signed-in user on a team, and drives the app the way a person would:
// switch tabs, switch workspace, open the create-course form.
//
//     cd frontend && npm run test:ui
//
// It exists because `vite build` proves the JSX PARSES and nothing more. The first run
// of this harness caught a crash that blanked the entire page on load (`teams.find()`
// on a list that is null until its fetch lands), plus an Agent-rules section that was
// always empty and a Create-new-course button that did nothing from other tabs — none
// of which a successful build would ever have revealed.
import { JSDOM } from 'jsdom'
import * as esbuild from 'esbuild'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// Resolve the frontend from this file, so the harness works wherever the repo lives.
// fileURLToPath, not URL.pathname — the latter percent-encodes, and this repo's path
// contains a space ("TR Doc Generator"), which esbuild then cannot resolve.
const FRONTEND = path.resolve(fileURLToPath(new URL('..', import.meta.url)))

// ---- the fake backend ------------------------------------------------------
// `mine` is the individual shelf (this user CREATED it); `shared` is a course reaching
// them through a team. Both are offered inside the team workspace; only `mine` belongs
// in the individual one, which is the distinction the app used to lack entirely.
const COURSES = [
  // `shelf` is what the individual picker filters on — assigned by the server
  // (db.courses_for_user). A course shared with a team you are on sits on the TEAM shelf
  // and must NOT also appear in the individual workspace.
  //
  // 'Own Draft' is the case that distinguishes the rule from the old one: created by this
  // user and shared with nobody, so it is the only thing on their individual shelf.
  { name: 'Own Draft', sessions: 4, teams: [], members: [],
    created_by: 'dev@nxtwave.co.in',
    mine: true, shared: false, unclaimed: false, shelf: 'individual' },
  { name: 'Operating Systems', sessions: 34, teams: ['OS Curriculum Team'],
    members: ['dev@nxtwave.co.in'], created_by: 'dev@nxtwave.co.in',
    mine: true, shared: true, unclaimed: false, shelf: 'team' },
  { name: 'Computer Networks', sessions: 31, teams: ['OS Curriculum Team', 'Networks Team'],
    members: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in'],
    created_by: 'colleague@nxtwave.co.in',
    mine: false, shared: true, unclaimed: false, shelf: 'team' },
]
const ROWS = [
  { session_no: 1, topic: 'Foundations', session_name: 'Understanding Binary System',
    key_takeaways: ['1. Data Representation: bits', '2. Number Systems: binary'],
    ppt_link: 'https://docs.google.com/presentation/d/AAA/edit',
    deck_status: 'extracted', extracted: true },
  { session_no: 31, topic: '', session_name: 'Spooling, Buffering & Disk Structure',
    key_takeaways: ['1. Buffering: single, double & circular buffers'],
    ppt_link: '', deck_status: 'none', extracted: false },
]
const SESSIONS = [{ number: 31, name: 'Spooling, Buffering & Disk Structure',
                    takeaways: ['1. Buffering: single, double & circular buffers'] }]
const ROUTES = {
  '/auth/config': { client_id: null, allowed_domain: 'nxtwave.co.in', configured: false, auth_disabled: true },
  '/status': { key_ok: true, saved_links: { course: 'https://docs.google.com/spreadsheets/d/X/edit' },
               settings: { course_type: 'semester', course_name: 'Operating Systems' },
               policy: { judge_always_on: true, time_always_enforced: true, max_minutes: 40, max_pages: 26, target_pages: 23 } },
  '/courses': { courses: COURSES, active: 'Operating Systems' },
  // The single call the app actually opens with; the per-concern endpoints below stay
  // stubbed because targeted refreshes (after a save, an ingest) still use them.
  '/bootstrap': {
    user: { email: 'dev@nxtwave.co.in', is_admin: false },
    status: { key_ok: true, saved_links: { course: 'https://docs.google.com/spreadsheets/d/X/edit' },
              settings: { course_type: 'semester', course_name: 'Operating Systems' },
              policy: { judge_always_on: true, time_always_enforced: true,
                        max_minutes: 40, max_pages: 26, target_pages: 23 } },
    course: 'Operating Systems',
    courses: COURSES,
    workspaces: { individual: { courses: ['Own Draft'] }, teams: [
      { id: 4, name: 'OS Curriculum Team', courses: ['Operating Systems', 'Computer Networks'],
        owner_email: 'dev@nxtwave.co.in', can_manage: true,
        members: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in'], unknown_courses: [] },
      { id: 5, name: 'Networks Team', courses: ['Computer Networks'],
        owner_email: 'someone.else@nxtwave.co.in', can_manage: false,
        members: ['dev@nxtwave.co.in'], unknown_courses: [] },
    ] },
    curriculum: { rows: ROWS, imported_from: 'https://docs.google.com/spreadsheets/d/X/edit', pending: 0 },
    sessions: SESSIONS,
    budget: { settings: {}, effective: { max_pages: 26, max_slides: 26, target_pages: 23,
                                         source: 'harness default' },
              defaults: { max_pages: 26, max_slides: 26, target_pages: 23 } },
    resumable: [{ guided_id: 'g31', session_no: 31,
                  title: 'Spooling, Buffering & Disk Structure',
                  status: 'reviewing', chunks_done: 2, total: 6,
                  updated: '2026-08-17T09:00:00Z' }],
  },
  '/workspaces': { individual: { courses: ['Own Draft'] },
                   teams: [
                     { id: 4, name: 'OS Curriculum Team', courses: ['Operating Systems', 'Computer Networks'],
                       owner_email: 'dev@nxtwave.co.in', can_manage: true,
                       members: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in'], unknown_courses: [] },
                     // A second team that does NOT own the open course — that is what
                     // makes the "share this course with a team" control applicable.
                     { id: 5, name: 'Networks Team', courses: ['Computer Networks'],
                       members: ['dev@nxtwave.co.in'], unknown_courses: [] },
                   ] },
  '/curriculum': { course: 'Operating Systems', rows: ROWS, imported_from: 'https://docs.google.com/spreadsheets/d/X/edit', pending: 0 },
  '/sessions': { sessions: SESSIONS },
  // Two runs on purpose: one the reviewer approved AND the graders passed, and one
  // approved with a grader flag still on it. The second is the normal case, and the
  // one the dashboard used to count as un-approved.
  '/my/history': { courses: [{ course: 'Operating Systems', runs: [
      { id: 'r1', session_no: 30, title: 'I/O Systems', user_email: 'dev@nxtwave.co.in',
        status: 'done', accepted: true, approved: true, gates_passed: true,
        rubric: 100, cost: {}, calls: [], ts: '2026-08-16T10:00:00Z' },
      { id: 'r3', session_no: 32, title: 'Disk Scheduling', user_email: 'dev@nxtwave.co.in',
        status: 'done', accepted: false, approved: true, gates_passed: false,
        rubric: 86, cost: {}, calls: [], ts: '2026-08-17T10:00:00Z' }],
      summary: { total_runs: 2, docs_built: 2, approved_docs: 2, gates_passed_docs: 1 } }],
      summary: { total_runs: 2, approved_docs: 2, gates_passed_docs: 1,
                 total_cost: 1.2, total_tokens: 400000 } },
  // can_manage / owner_email are decided by the SERVER — the team page offers the
  // add-and-remove-member controls off can_manage, and a client must never be the one
  // deciding what it is allowed to do. Here the signed-in user IS the owner, which is
  // the case those controls exist for.
  '/my/teams': { teams: [{ team: { id: 4, name: 'OS Curriculum Team', course: 'Operating Systems',
                                  courses: ['Operating Systems', 'Computer Networks'],
                                  owner_email: 'dev@nxtwave.co.in', can_manage: true,
                                  members: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in'] },
                           members: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in'],
                           // Three contributors against two members — legitimate, and
                           // the exact reading that looked like a bug on screen.
                           contributors: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in',
                                          'former.member@nxtwave.co.in'],
                           // EXACTLY the keys server._rollup emits — no more. The old
                           // stub invented `runs`, which is what let the panel read a
                           // field the server has never sent and show 0 for ever.
                           summary: { total_runs: 3, docs_built: 2, approved_docs: 2,
                                      gates_passed_docs: 1, total_cost: 1.2,
                                      total_tokens: 400000 },
                           courses: [{ course: 'Operating Systems', runs: [
                             { id: 'r1', session_no: 30, title: 'I/O Systems', user_email: 'dev@nxtwave.co.in', status: 'done', accepted: true, cost: {}, calls: [] },
                             { id: 'r2', session_no: 29, title: 'File Systems', user_email: 'colleague@nxtwave.co.in', status: 'done', accepted: true, cost: {}, calls: [] }],
                             summary: { total_runs: 3, docs_built: 2, approved_docs: 2,
                                        gates_passed_docs: 1 } }] }] },
  // COURSE RULES — what THIS course is written under, and what its learners already knew.
  // A draft and an approved skill must be visibly different, or the approval step nobody
  // can see is a step nobody takes.
  '/skills': { course: 'Operating Systems', approved: 1, can_edit: true,
               owner: 'dev@nxtwave.co.in', skills: [
    { id: 1, text: 'Show the snippet before explaining it.', status: 'approved',
      source: 'user', check: null },
    { id: 2, text: 'Explain each snippet line by line.', status: 'draft',
      source: 'requirements', source_quote: 'explain the code line by line',
      check: { assert: 'field_present', field: 'walkthrough', when_block: 'code' } },
  ] },
  '/prereqs': { course: 'Operating Systems', can_edit: true,
                prereqs: [{ prereq: 'Computer Networks', kind: 'course' },
                          { prereq: 'JS Elsewhere', kind: 'external' }],
                available: ['Computer Networks', 'Own Draft'],
                report: { topics_indexed: 42, prereqs: ['Computer Networks'],
                          overlaps: [{ session_no: 4, topic: 'Sockets',
                                       prereq: 'Computer Networks',
                                       takeaway: 'Sockets: the API' }] } },
  '/learned-rules': { rules: [{ text: 'Do not restate the paragraph in the bullets', scope: 'course', session_no: 30, source: 'judge', hits: 2, applies: true }], course: 'Operating Systems' },
  // An abandoned run for a DIFFERENT session than the one selected — the situation
  // that produces a document for a session you did not think you asked for.
  '/course-settings': { course: 'Operating Systems', settings: {},
                        effective: { max_pages: 26, max_slides: 26, target_pages: 23,
                                     source: 'harness default' },
                        defaults: { max_pages: 26, max_slides: 26, target_pages: 23 } },
  '/guided/resumable': { runs: [{ guided_id: 'g31', session_no: 31,
                                  title: 'Spooling, Buffering & Disk Structure',
                                  status: 'reviewing', chunks_done: 2, total: 6,
                                  updated: '2026-08-17T09:00:00Z' }] },
  // A run in REVIEW, with real chunks — the three controls the reviewer drives from here
  // (split a slide, make a note stick to the following chunks, create the final doc) are
  // otherwise unreachable from this harness. `slides` is what the server sends so the
  // split picker can name a slide without parsing it back out of the markdown.
  '/guided/g31': { status: 'reviewing', session_no: 31,
                   session_title: 'Spooling, Buffering & Disk Structure',
                   total: 3, index: 3,
                   labels: ['Opening', 'Takeaway 1', 'Takeaway 2'],
                   // The ticks live on the SERVER now, so the stub holds them (see
                   // APPROVED below) — they used to be React state, which is why a reload
                   // wiped the review.
                   approved_chunks: [], all_approved: false,
                   standing_notes: [], logs: [],
                   chunks: [
                     { label: 'Opening (recap + agenda)', markdown: '## RECAP', repetition: [], slides: [] },
                     { label: 'Key takeaway 1: Buffering', repetition: [],
                       markdown: '### Slide 1: Buffering Basics\n\nText.',
                       slides: [{ n: 1, title: 'Buffering Basics' },
                                { n: 2, title: 'Double Buffering' }] },
                     { label: 'Key takeaway 2: Spooling', repetition: [],
                       markdown: '### Slide 3: Spooling\n\nText.',
                       slides: [{ n: 3, title: 'Spooling' }] },
                   ] },
  '/dashboard': { courses: [], summary: {} },
  '/template-guide': { markdown: '# Sheet template\n\nColumns…' },
}

const calls = []
// Rows the fake server holds, so an insert can be answered the way the real one does:
// shift everything at or after the position, then put the new row in. Without this the
// stub would return the same list forever and the numbering — the whole point of the
// insert — would be untestable from the UI.
let SERVER_ROWS = ROWS.map((r) => ({ ...r }))
const SAVED = []      // rows the fake server was asked to persist
const DELETED = []    // courses the fake server was asked to delete, in order
const REGENS = []     // {index, reason, apply_to_following} the review panel posted
const SPLITS = []     // {index, slide_n} the review panel posted
const FINALIZED = []  // one entry per create-final-doc request
let APPROVED = []     // chunks the fake server has been told are reviewed
function route(url, opts) {
  const p = String(url).replace(/^\/api/, '').split('?')[0]
  calls.push(p)
  if (p === '/curriculum' && opts?.method === 'POST') {
    const sent = JSON.parse(opts.body || '{}').rows || []
    SAVED.push(...sent)
    sent.forEach((row) => {
      const at = SERVER_ROWS.find((r) => Number(r.session_no) === Number(row.session_no))
      if (at) Object.assign(at, row)
    })
    return { saved: sent.length, rows: SERVER_ROWS }
  }
  if (p === '/curriculum/insert') {
    const at = Number(JSON.parse(opts?.body || '{}').at_session_no)
    let shifted = 0
    SERVER_ROWS = SERVER_ROWS.map((r) => {
      if (Number(r.session_no) >= at) { shifted += 1; return { ...r, session_no: Number(r.session_no) + 1 } }
      return r
    })
    SERVER_ROWS.push({ session_no: at, topic: '', session_name: '', key_takeaways: [],
                       ppt_link: '', deck_status: 'none', extracted: false })
    SERVER_ROWS.sort((a, b) => a.session_no - b.session_no)
    return { course: 'Operating Systems', inserted: at, shifted, rows: SERVER_ROWS }
  }
  if (p.startsWith('/curriculum/') && opts?.method === 'DELETE') {
    const no = Number(p.split('/')[2])
    let shifted = 0
    SERVER_ROWS = SERVER_ROWS.filter((r) => Number(r.session_no) !== no)
      .map((r) => {
        if (Number(r.session_no) > no) { shifted += 1; return { ...r, session_no: Number(r.session_no) - 1 } }
        return r
      })
    SERVER_ROWS.sort((a, b) => a.session_no - b.session_no)
    return { ok: true, removed: no, shifted, rows: SERVER_ROWS }
  }
  // DELETING A COURSE, answered the way the real server does: the first request refuses
  // with a 409 that NAMES the teams sharing the course, and only an explicit second one
  // (detach_teams=true) goes ahead. That two-step is the whole behaviour under test — a
  // static stub could not tell the two calls apart.
  if (p === '/courses' && opts?.method === 'DELETE') {
    if (!/detach_teams=true/.test(String(url))) {
      return { __status: 409, __body: { detail: {
        message: "'Operating Systems' is shared with OS Curriculum Team.",
        kind: 'course_shared', teams: [{ id: 4, name: 'OS Curriculum Team' }] } } }
    }
    DELETED.push('Operating Systems')
    return { ok: true, deleted: 'Operating Systems', sessions_removed: 34,
             teams_detached: [{ id: 4, name: 'OS Curriculum Team' }],
             decks_cleared: [], history_kept: true, course: null, courses: [] }
  }
  // The guided review actions. Each records what it was asked for — which is the whole
  // question here: does the panel send the reviewer's choice, or quietly drop it?
  if (p === '/guided/g31/regenerate') {
    REGENS.push(JSON.parse(opts?.body || '{}'))
    return { ok: true, apply_to_following: !!JSON.parse(opts?.body || '{}').apply_to_following }
  }
  if (p === '/guided/g31/split') {
    const b = JSON.parse(opts?.body || '{}')
    SPLITS.push(b)
    // The real endpoint answers with the whole updated view, because renumbering touches
    // the later chunks too — so the stub does the same, one slide longer.
    const v = JSON.parse(JSON.stringify(ROUTES['/guided/g31']))
    v.chunks[1].slides = [{ n: 1, title: 'Buffering Basics' },
                          { n: 2, title: 'Buffering Basics (continued)' },
                          { n: 3, title: 'Double Buffering' }]
    v.chunks[2].slides = [{ n: 4, title: 'Spooling' }]
    v.chunks[2].markdown = '### Slide 4: Spooling\n\nText.'
    return v
  }
  if (p === '/guided/g31/approve') {
    const b = JSON.parse(opts?.body || '{}')
    APPROVED = b.approved === false
      ? APPROVED.filter((i) => i !== b.index)
      : [...new Set([...APPROVED, b.index])]
    const v = JSON.parse(JSON.stringify(ROUTES['/guided/g31']))
    v.approved_chunks = [...APPROVED].sort((a, b2) => a - b2)
    v.all_approved = APPROVED.length === v.chunks.length
    return v
  }
  if (p === '/sync') return { job_id: 'syncjob' }
  if (p === '/jobs/syncjob') return { status: 'done', logs: ['imported'], result: {
    sessions: SESSIONS, changelog: [], errors: [], extraction_warnings: [],
    counts: { sessions: 1, ingested: 0, cached: 0 } } }
  if (p === '/guided/g31/finalize') { FINALIZED.push(1); return { ok: true } }
  if (p === '/guided/g31') {
    const v = JSON.parse(JSON.stringify(ROUTES[p]))
    v.approved_chunks = [...APPROVED].sort((a, b) => a - b)
    v.all_approved = APPROVED.length === v.chunks.length
    return v
  }
  if (p === '/bootstrap') {
    // Echo the course that was ASKED for. The stub used to answer with the same course
    // every time, so switching course looked like it bounced straight back — and any
    // behaviour that depends on landing on a different course was untestable.
    const asked = /[?&]course=([^&]*)/.exec(String(url))
    const v = JSON.parse(JSON.stringify(ROUTES[p]))
    if (asked) v.course = decodeURIComponent(asked[1])
    return v
  }
  if (p === '/curriculum') return { ...ROUTES[p], rows: SERVER_ROWS }
  if (p in ROUTES) return ROUTES[p]
  return {}
}

// ---- DOM + globals ---------------------------------------------------------
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',
                      { url: 'http://localhost:5173/', pretendToBeVisual: true })
const { window } = dom
global.window = window
global.document = window.document
// Node 22 defines `navigator` as a getter-only global, so it must be redefined
// rather than assigned.
Object.defineProperty(global, 'navigator', { value: window.navigator, configurable: true })
global.HTMLElement = window.HTMLElement
global.Element = window.Element
global.Node = window.Node
global.getComputedStyle = window.getComputedStyle
global.requestAnimationFrame = (cb) => setTimeout(cb, 0)
global.cancelAnimationFrame = clearTimeout
global.localStorage = window.localStorage
global.IS_REACT_ACT_ENVIRONMENT = true
global.fetch = async (url, opts) => {
  const r = route(url, opts)
  // A route may answer with a FAILURE. Every response used to be 200/ok, which made any
  // error branch in the app — the shared-course confirmation, for one — unreachable from
  // this harness however carefully it was written.
  if (r && r.__status) {
    return { ok: false, status: r.__status, headers: { get: () => '' },
             json: async () => r.__body, blob: async () => ({}) }
  }
  return { ok: true, status: 200, headers: { get: () => '' },
           json: async () => r, blob: async () => ({}) }
}

// ---- bundle the real App ---------------------------------------------------
const out = await esbuild.build({
  entryPoints: [path.join(FRONTEND, 'src/App.jsx')],
  bundle: true, write: false, format: 'esm', platform: 'browser',
  jsx: 'automatic', loader: { '.js': 'jsx' },
  external: ['react', 'react-dom', 'react-dom/client', 'react-markdown', 'remark-gfm'],
  define: { 'process.env.NODE_ENV': '"development"' },
})
const code = out.outputFiles[0].text
const tmp = path.join(FRONTEND, 'test', '_app.bundle.mjs')
// react-markdown is ESM-only and irrelevant to layout — stub it so the bundle loads.
fs.writeFileSync(tmp, code
  .replace(/from\s*"react-markdown"/g, 'from "./_stub.mjs"')
  .replace(/from\s*"remark-gfm"/g, 'from "./_stub.mjs"'))
fs.writeFileSync(path.join(FRONTEND, 'test', '_stub.mjs'),
  'export default function S({children}){return children ?? null}\nexport const x=1;\n')

const React = (await import('react')).default
const ReactDOMClient = await import('react-dom/client')
const { act } = await import('react')
const App = (await import(tmp)).default

// ---- mount -----------------------------------------------------------------
const root = ReactDOMClient.createRoot(document.getElementById('root'))
await act(async () => { root.render(React.createElement(App)) })
await act(async () => { await new Promise((r) => setTimeout(r, 60)) })

const $ = (sel) => Array.from(document.querySelectorAll(sel))
const text = () => document.body.textContent.replace(/\s+/g, ' ')
let pass = 0, fail = 0
const check = (name, cond, extra = '') => {
  if (cond) { pass++; console.log('  ok   ' + name) }
  else { fail++; console.log('  FAIL ' + name + (extra ? '  ' + extra : '')) }
}
async function click(el) {
  await act(async () => {
    el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
    await new Promise((r) => setTimeout(r, 40))
  })
}
async function selectOption(sel, value) {
  await act(async () => {
    sel.value = value
    sel.dispatchEvent(new window.Event('change', { bubbles: true }))
    await new Promise((r) => setTimeout(r, 40))
  })
}

console.log('\n== the page opens with ONE request, not eight ==')
const bootCalls = calls.filter((c) => c === '/bootstrap').length
const fanout = calls.filter((c) => ['/courses', '/workspaces', '/curriculum', '/sessions',
                                    '/course-settings', '/status'].includes(c)).length
console.log('       requests on load: ' + JSON.stringify(calls))
check('it bootstraps', bootCalls >= 1)
check('…instead of fanning out across the per-concern endpoints', fanout <= 1,
      `${fanout} fan-out calls`)

console.log('\n== the shell renders ==')
check('a left navigation rail exists', $('.nav').length === 1)
check('one main content area', $('.main').length === 1)
check('the shell is a two-column grid', $('.shell').length === 1)
check('workspace options are listed', $('.wsopt').length >= 2, `got ${$('.wsopt').length}`)
check('Individual is a workspace', text().includes('Individual'))
check('the team appears as a workspace', text().includes('OS Curriculum Team'))
check('a course picker is in the rail', $('.navselect').length === 1)
// THE REGRESSION THIS GUARDS: the individual workspace listed every course the server
// would let the user read, which included a colleague's course reaching them through a
// team — and, before ownership was recorded at all, every course on the instance.
const indOpts = $('.navselect option').map((o) => o.textContent.trim())
console.log('       individual courses offered: ' + JSON.stringify(indOpts))
check('the individual shelf offers the course this user created and has not shared',
      indOpts.some((o) => o.includes('Own Draft')), JSON.stringify(indOpts))
check('…and NOT a colleague\'s course shared through a team',
      !indOpts.some((o) => o.includes('Computer Networks')), JSON.stringify(indOpts))
// THE RULE THIS GUARDS: "moved it to the team" has to mean moved. A course this user
// created but shared with their team belongs to the team, and listing it in both places
// is not a move — it is the same course twice.
// It is not offered as a shelf entry (those carry a session count). It appears only as
// the "currently open, but lives elsewhere" marker, because the select must show what is
// actually loaded rather than render blank against an open course.
check('…and NOT their OWN course once it is shared with their team',
      !indOpts.some((o) => o.includes('Operating Systems (34)')), JSON.stringify(indOpts))
check('…the open-but-elsewhere course is labelled as such, not listed as a second copy',
      indOpts.some((o) => o.includes('Operating Systems — open, shared with a team')),
      JSON.stringify(indOpts))
const tabs = $('.navtab').map((b) => b.textContent.replace(/\s+/g, ' ').trim())
check('tabs are present', tabs.length >= 4, JSON.stringify(tabs))
console.log('       tabs: ' + JSON.stringify(tabs))

console.log('\n== the default view is the curriculum, as a flat sheet ==')
check('curriculum table rendered', $('.curtable').length === 1)
check('it uses the sheet-like layout', $('.sheetlike').length === 1)
const heads = $('.curhead span').map((s) => s.textContent.trim()).filter(Boolean)
console.log('       columns: ' + JSON.stringify(heads))
check('key takeaways is a visible column', heads.some((h) => /key takeaways/i.test(h)))
check('PPT link is a visible column', heads.some((h) => /ppt link/i.test(h)))
check('takeaways are editable inline (no expander)', $('textarea.c-kt').length === ROWS.length)
check('deck status shows per row', text().includes('extracted') && text().includes('no deck'))
check('the create-course card is NOT shown for a course that exists',
      !text().includes('Create a new course'))

console.log('\n== switching tabs changes the view ==')
const byLabel = (label) => $('.navtab').find((b) => b.textContent.includes(label))
await click(byLabel('Generate'))
check('Generate shows the session picker', text().includes('Generate a TR doc'))
check('…and the curriculum table is gone', $('.curtable').length === 0)
check('the session needing a doc is offered', text().includes('Spooling, Buffering'))

await click(byLabel('History'))
check('History shows past runs', text().includes('I/O Systems'))
check('…and not the generate panel', !text().includes('Generate all chunks'))
// The reported defect: "Approved" read 0 against docs the reviewer had approved,
// because the card counted the GRADERS' verdict. Both numbers are shown now, and they
// are different numbers — 2 approved, 1 of them clean.
check('Approved counts the human sign-offs, not the graders',
      /2\s*Approved/.test(text().replace(/\s+/g, ' ')),
      text().replace(/\s+/g, ' ').match(/.{0,40}Approved.{0,40}/)?.[0])
check('…and the graders verdict is shown separately',
      text().includes('Passed all gates'))
check('a doc approved WITH a grader flag still reads approved',
      text().includes('approved') && text().includes('flagged'))

check('no Team tab while working individually — there is no team to show',
      byLabel('Team') === undefined)

await click(byLabel('Agent rules'))
check('Agent rules renders without crashing', text().includes('restate the paragraph'))

console.log('\n== switching into the team workspace ==')
const teamWs = $('.wsopt').find((b) => b.textContent.includes('OS Curriculum Team'))
await click(teamWs)
check('the team workspace is selected',
      $('.wsopt').find((b) => b.textContent.includes('OS Curriculum Team')).className.includes('on'))
check('a Team tab appears now', byLabel('Team') !== undefined)
const opts = $('.navselect option').map((o) => o.textContent.trim())
console.log('       courses offered: ' + JSON.stringify(opts))
check('the team\'s courses are offered', opts.some((o) => o.includes('Operating Systems')))
check('…including the one the user did not create — that is what the team shelf is for',
      opts.some((o) => o.includes('Computer Networks')), JSON.stringify(opts))

await click(byLabel('Team'))
check('Team shows the team name', text().includes('OS Curriculum Team'))
check('…its members', text().includes('colleague@nxtwave.co.in'))
check('…and every course it owns', text().includes('Computer Networks'))
// Membership used to be admin-only in both directions, so the panel could only point
// at /admin. The team's COURSE OWNER can do it themselves now, and this user is one.
check('the course owner is marked on the member list', $('.mtag').length === 1)
// THE REGRESSION THIS GUARDS: the panel read `summary.runs`, a key the server does not
// send, so it showed "Docs built 0" beside a contributor count derived from those very
// runs. Asserted on the number, not on the label.
const metricValue = (label) => {
  const m = $('.metric').find((el) => el.querySelector('.ml')?.textContent.startsWith(label))
  return m?.querySelector('.mv')?.textContent
}
check('Docs built shows the docs actually built', metricValue('Docs built') === '2',
      `got ${metricValue('Docs built')}`)
check('…with the failed/abandoned attempts named beside it, not counted as docs',
      text().includes('3 attempts'), text().replace(/\s+/g, ' ').match(/.{0,30}attempt.{0,20}/)?.[0])
// Members and contributors are different counts, and 3 beside 2 read as a bug until the
// panel said why.
check('Contributors counts everyone who built for the team\'s courses',
      metricValue('Contributors') === '3', `got ${metricValue('Contributors')}`)
check('…and says how many of them are not on the team',
      text().includes('1 not on the team'),
      text().replace(/\s+/g, ' ').match(/.{0,30}not on the team.{0,10}/)?.[0])
check('…and names them where the members are listed',
      text().includes('former.member@nxtwave.co.in'))
check('…and is told they can manage the team',
      text().includes("this team's course owner"))
check('an add-member control is offered', $('input').some(
      (i) => i.placeholder?.includes('colleague@nxtwave.co.in')))
check('a remove control is offered for the other member',
      $('.mx').length === 1)
check('…but not for the owner themselves — reassigning is the admin\'s call',
      $('.memberchip.owner .mx').length === 0)

await click(byLabel('History'))
check('team history shows a COLLEAGUE\'s doc, not just mine',
      text().includes('File Systems') && text().includes('colleague@nxtwave.co.in'))

console.log('\n== a team you do NOT own offers no member controls ==')
// Networks Team is absent from /my/teams, so this ALSO exercises the lighter
// `activeTeamInfo` fallback the panel renders from while the heavier call is in flight —
// which is where can_manage would be easiest to lose.
await click($('.wsopt').find((b) => b.textContent.includes('Networks Team')))
await click(byLabel('Team'))
check('the panel still renders from the workspace record', text().includes('Networks Team'))
check('no add-member control', !$('input').some(
      (i) => i.placeholder?.includes('colleague@nxtwave.co.in')))
check('no remove controls', $('.mx').length === 0)
check('…and it names who to ask instead',
      text().includes('someone.else@nxtwave.co.in'))

const indWs = $('.wsopt').find((b) => b.textContent.includes('Individual'))
await click(indWs)
check('back to Individual',
      $('.wsopt').find((b) => b.textContent.includes('Individual')).className.includes('on'))
check('the Team tab disappears again', byLabel('Team') === undefined)
// COMING BACK MUST LAND ON YOUR OWN COURSE. Only the team direction was handled, so
// switching to Individual left the team's course selected — and it then showed in the
// individual picker as the "currently open" entry, which is precisely the course this
// workspace does not hold.
const backOpts = $('.navselect option').map((o) => o.textContent.trim())
console.log('       individual courses after coming back: ' + JSON.stringify(backOpts))
check('no team course is left in the individual picker',
      !backOpts.some((o) => /Operating Systems|Computer Networks/.test(o)),
      JSON.stringify(backOpts))
check('…and it landed on a course this user actually owns',
      backOpts.some((o) => o.includes('Own Draft')), JSON.stringify(backOpts))

console.log('\n== an open run names its own session, and flags a mismatch ==')
await click(byLabel('Generate'))
const sessSel = $('select').find((s) => Array.from(s.options).some((o) => o.textContent.includes('Spooling')))
check('an abandoned run is offered', text().includes('Unfinished TR doc'))
await click($('button').find((b) => b.textContent.includes('Resume')))
check('the panel names the session the RUN is for',
      text().includes('Generating Session 31'))
check('…and the doc it will produce is stated when it differs from the picker',
      $('.runhead.mismatch').length === 0 || text().includes('will produce that document'))

console.log('\n== a reviewer note can be made to stick to the following chunks ==')
// Most notes are about the DOCUMENT, not the one chunk in front of you. Applying one used
// to mean retyping it into every remaining chunk in turn, waiting for each.
const chunkPanels = () => $('.review-chunk')
check('the chunks are listed for review', chunkPanels().length === 3,
      `got ${chunkPanels().length}`)
await click($('button').filter((b) => b.textContent.includes('Regenerate…'))[1])
check('the reason box opens', text().includes('Why regenerate?'))
const stickBox = $('.checkline input')[0]
check('…offering to apply the note to every chunk after this one', stickBox !== undefined)
check('…and saying how many that is', text().includes('remaining 1 chunk(s)'),
      text().replace(/\s+/g, ' ').match(/.{0,60}remaining.{0,40}/)?.[0])
const reasonBox = $('textarea').find((t) => t.placeholder?.includes('analogy concrete'))
await act(async () => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
  setter.call(reasonBox, 'Drop every analogy.')
  reasonBox.dispatchEvent(new window.Event('input', { bubbles: true }))
})
await click(stickBox)
await click($('button').find((b) => b.textContent.trim() === 'Regenerate'))
check('the note is sent', REGENS.length === 1, JSON.stringify(REGENS))
check('…with the reviewer\'s words', REGENS[0]?.reason === 'Drop every analogy.',
      JSON.stringify(REGENS[0]))
check('…and with the choice to apply it forward', REGENS[0]?.apply_to_following === true,
      JSON.stringify(REGENS[0]))

console.log('\n== the last chunk is not offered the choice — there is nothing after it ==')
await click($('button').filter((b) => b.textContent.includes('Regenerate…')).slice(-1)[0])
check('no apply-forward tick on the final chunk', $('.checkline input').length === 0,
      `got ${$('.checkline input').length}`)
await click($('button').find((b) => b.textContent.trim() === 'Cancel'))

console.log('\n== a slide that carries too much can be split in two ==')
const splitBtn = $('button').filter((b) => b.textContent.includes('Split a slide'))
check('splitting is offered on a chunk that has slides', splitBtn.length === 2,
      `got ${splitBtn.length}`)
check('…and NOT on the opening, which has none',
      chunkPanels()[0].textContent.includes('Split a slide') === false)
await click(splitBtn[0])
const slideSel = $('select').find((sl) => Array.from(sl.options)
  .some((o) => o.textContent.includes('Buffering Basics')))
check('the slides of that chunk are offered by name', slideSel !== undefined)
check('…all of them', Array.from(slideSel.options).filter((o) => o.value).length === 2,
      JSON.stringify(Array.from(slideSel.options).map((o) => o.textContent)))
check('…and it says the later slides are renumbered too',
      text().includes('renumbered automatically'))
await act(async () => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set
  setter.call(slideSel, '2')
  slideSel.dispatchEvent(new window.Event('change', { bubbles: true }))
})
await click($('button').find((b) => b.textContent.includes('Split into 2 slides')))
check('the split is sent', SPLITS.length === 1, JSON.stringify(SPLITS))
check('…naming the chunk and the slide', SPLITS[0]?.index === 1 && SPLITS[0]?.slide_n === 2,
      JSON.stringify(SPLITS[0]))
check('the reply\'s renumbering reaches the screen — a LATER chunk moved',
      text().includes('Slide 4: Spooling'),
      text().replace(/\s+/g, ' ').match(/.{0,40}Spooling.{0,20}/)?.[0])

console.log('\n== Create final TR Doc says it is working ==')
// The status stays 'reviewing' until the next poll lands, and assembling takes a minute
// or two — a button that merely greys out reads as a click that did nothing.
// Ticks go to the SERVER now, so this also proves the round-trip: each click posts, the
// reply carries the updated list, and the panel reads its state back from it.
for (const b of $('button').filter((x) => x.textContent.includes('Approve'))) await click(b)
check('every tick reached the server', APPROVED.length === 3, JSON.stringify(APPROVED))
check('…and the panel shows them from the server\'s answer',
      text().includes('3/3 approved'),
      text().replace(/\s+/g, ' ').match(/.{0,20}approved.{0,10}/)?.[0])
const finalBtn = () => $('button.bigfinal')[0]
check('the final-doc button is enabled once every chunk is approved',
      finalBtn() && !finalBtn().disabled,
      `disabled=${finalBtn()?.disabled}`)
check('…and reads plainly before it is pressed',
      finalBtn().textContent.includes('Create final TR Doc'))
await click(finalBtn())
check('the request went', FINALIZED.length === 1, JSON.stringify(FINALIZED))
check('…and the button now says it is working',
      finalBtn().textContent.includes('Creating the final doc'),
      finalBtn().textContent)
check('…with a spinner on it', finalBtn().querySelector('.spinner') !== null)
check('…and cannot be pressed twice', finalBtn().disabled)
check('…and it says how long this takes',
      text().includes('Assembling, grading and rendering'))

console.log('\n== Course rules: what THIS course is written under ==')
await click(byLabel('Course rules'))
// Named against the course actually OPEN, not a literal: which course the tests above
// leave selected is their business, and these rules belong to whichever it is.
const openForRules = $('.navselect')[0].value
check('the panel names the course that is open',
      text().includes(`Course rules — ${openForRules}`),
      `open=${openForRules}; heading=${$('h2').map((h) => h.textContent.trim())[0]}`)
check('…and says these are the course\'s own, not the agent\'s',
      text().includes('separate from') || text().includes('Agent rules'),
      text().replace(/\s+/g, ' ').match(/.{0,80}Agent rules.{0,40}/)?.[0])
check('an approved skill is listed', text().includes('Show the snippet before explaining it.'))
check('…and a draft too', text().includes('Explain each snippet line by line.'))
// THE PROPERTY THAT MATTERS: a draft has to look like one, or approval is invisible.
check('a draft is visibly a draft', $('.skillrow.draft').length === 1,
      `${$('.skillrow.draft').length} draft rows`)
check('…and an approved skill visibly approved', $('.skillrow.approved').length === 1,
      `${$('.skillrow.approved').length} approved rows`)
check('only the draft is offered for approval',
      $('button').filter((b) => b.textContent.trim() === 'Approve').length === 1,
      `${$('button').filter((b) => b.textContent.trim() === 'Approve').length} approve buttons`)
check('a skill with a machine check says so', text().includes('checked automatically'))
// Path B's traceability, on screen: the reviewer can see the words it came from.
check('a drafted skill shows the words it came from',
      text().includes('explain the code line by line'),
      text().replace(/\s+/g, ' ').match(/.{0,60}from your words.{0,60}/)?.[0])
check('all three ways to add one are offered',
      text().includes('Write one') && text().includes('From my requirements')
      && text().includes('Import from a course'))
check('prerequisites are listed', text().includes('Computer Networks'))
check('…with what they cover', text().includes('42 topic'),
      text().replace(/\s+/g, ' ').match(/.{0,40}topic\(s\).{0,40}/)?.[0])
check('…and the overlap is surfaced as a review signal',
      text().includes('Sockets'), text().replace(/\s+/g, ' ').match(/.{0,80}Sockets.{0,40}/)?.[0])
check('…and it says prerequisites may be REFERRED to, not that they are banned',
      text().includes('refer to them freely'),
      text().replace(/\s+/g, ' ').match(/.{0,60}freely.{0,30}/)?.[0])
// A prerequisite need not be a course this agent holds — the common case is one taught
// somewhere else, known only through its slides.
check('a prerequisite taught elsewhere is listed', text().includes('JS Elsewhere'))
check('…and marked as such, because the two differ in where the decks live',
      $('.memberchip .mtag').length === 1,
      `${$('.memberchip .mtag').length} tagged`)
check('both ways to add one are offered',
      text().includes('a course in this agent') && text().includes('One taught elsewhere'))
await click($('button').find((b) => b.textContent.includes('One taught elsewhere')))
check('…and the elsewhere form asks for a name and its deck links',
      $('input').some((i) => i.placeholder?.includes('taught elsewhere'))
      && $('textarea').some((t) => t.placeholder?.includes('presentation')),
      'name + links')
check('…and says whose the decks become',
      text().includes('they\u2019go if it does') || text().includes('go if it does'),
      text().replace(/\s+/g, ' ').match(/.{0,70}go if it does.{0,20}/)?.[0])

console.log('\n== the budget lives in Settings, not in the curriculum actions ==')
await click(byLabel('Curriculum'))
check('no budget control among the curriculum actions',
      !$('.curactions').some((el) => /budget/i.test(el.textContent)))
check('…and Settings is offered in the rail', byLabel('Settings') !== undefined)
await click(byLabel('Settings'))
check('Settings shows the course length budget',
      text().includes('Document length for every session'))
check('…and is where a single session gets its own budget',
      text().includes('Sessions that need something different'))
check('…and states what is currently applied', text().includes('Currently applied'))

console.log('\n== a course you own can be deleted, in two steps ==')
// A course imported and no longer needed had to stay on the shelf for ever. The user
// signed in here CREATED 'Operating Systems' and shared it with their team, so it is
// theirs to remove — and it is reached through the TEAM workspace now, because a shared
// course lives on the team's shelf and not also on the individual one.
await click($('.wsopt').find((b) => b.textContent.includes('OS Curriculum Team')))
await click(byLabel('Settings'))
const delBtn = $('button').find((b) => b.textContent.includes('Delete “Operating Systems”'))
check('the delete control is offered for a course you created', delBtn !== undefined)
check('…and it warns that a team is working from it',
      text().includes('OS Curriculum Team'))
await click(delBtn)
check('one click only ASKS', text().includes('Delete “Operating Systems” for good?'))
check('…and says the finished documents are kept',
      text().includes('Documents already generated are kept'))
check('…and nothing has been deleted yet', DELETED.length === 0, JSON.stringify(DELETED))
const keepBtn = $('button').find((b) => b.textContent.includes('Keep it'))
check('…and backing out is offered', keepBtn !== undefined)
await click(keepBtn)
check('backing out closes it', !text().includes('for good?'))
check('…and still nothing was deleted', DELETED.length === 0, JSON.stringify(DELETED))
// Round two: confirm. The first request 409s with the team list, the confirmation names
// them, and the second request carries detach_teams.
await click($('button').find((b) => b.textContent.includes('Delete “Operating Systems”')))
await click($('button').find((b) => /Yes/.test(b.textContent)))
check('the confirmation names the team the course is shared with',
      text().includes('OS Curriculum Team'), text().replace(/\s+/g, ' ').slice(0, 200))
await click($('button').find((b) => /Yes/.test(b.textContent)))
check('confirming deletes it', DELETED.length === 1, JSON.stringify(DELETED))

await click(byLabel('Curriculum'))
check('the table is back to its seven columns',
      $('.curhead span').length === 7, `got ${$('.curhead span').length}`)
check('every row has exactly as many cells as the header',
      $('.currow')[1].children.length === 7,
      `row has ${$('.currow')[1].children.length}`)

console.log('\n== the context line says where you are ==')
await click(byLabel('Curriculum'))
check('it names the workspace and the course',
      $('.context').length === 1 && text().includes('Individual') && text().includes('Operating Systems'))
const teamWs2 = $('.wsopt').find((b) => b.textContent.includes('OS Curriculum Team'))
await click(teamWs2)
check('in a team it names the team', $('.context')[0].textContent.includes('OS Curriculum Team'))
check('…and offers its other course in one click',
      $('.ctxswitch .coursechip').some((b) => b.textContent.includes('Computer Networks')))
check('…and says who it is shared with', $('.context')[0].textContent.includes('member'))
await click($('.wsopt').find((b) => b.textContent.includes('Individual')))

console.log('\n== adding a session anywhere, not only from the top ==')
await click(byLabel('Curriculum'))
const bars = $('.insertbar')
check('there is an insert point between rows and at the end',
      bars.length === ROWS.length + 1, `got ${bars.length} for ${ROWS.length} rows`)
check('the last one is the add-at-the-end button',
      bars[bars.length - 1].textContent.includes('at the end'))
const before = $('.currow').length
await click(bars[bars.length - 1])
check('adding at the end inserts a row', $('.currow').length === before + 1)

// THE REPORTED BUG: inserting at the TOP of the list gave the new row the next FREE
// number — "35" above session 1 — instead of taking position 1 and pushing the rest
// down. A curriculum is an ordered list, so the row you put first IS session 1.
// `.currow` is also the class on the HEADER strip (`currow curhead`), so the data rows
// are the ones carrying a number input.
const nums = () => $('.currow:not(.curhead)').map((r) => Number(r.querySelector('.c-no').value))
const numsBefore = nums()
await click($('.insertbar')[0])
const numsAfter = nums()
check('inserting at the top makes it session 1, not the next free number',
      numsAfter[0] === 1, `got ${numsAfter[0]} (was ${numsBefore.join(',')} -> ${numsAfter.join(',')})`)
check('…and the rows below it all move down one',
      numsAfter.length === numsBefore.length + 1
      && numsAfter[1] === numsBefore[0] + 1,
      `${numsBefore.join(',')} -> ${numsAfter.join(',')}`)
check('…leaving no two rows sharing a number',
      new Set(numsAfter).size === numsAfter.length, numsAfter.join(','))
check('…and the user is told the decks moved too',
      text().includes('moved down one'))

// The mirror image, asked for straight after: deleting must CLOSE the gap, not leave
// the course jumping from 4 to 6.
window.confirm = () => true
const beforeDel = nums()
await click($('.currow:not(.curhead)')[0].querySelector('.c-act button'))
const afterDel = nums()
check('deleting a session closes the gap behind it',
      afterDel.length === beforeDel.length - 1 && afterDel[0] === beforeDel[1] - 1,
      `${beforeDel.join(',')} -> ${afterDel.join(',')}`)
check('…leaving the numbering contiguous from where it was',
      new Set(afterDel).size === afterDel.length, afterDel.join(','))
check('…and saying the sessions below moved up',
      text().includes('moved up one'))

// Insert and delete renumber on the SERVER and replace the whole table, so an edit
// made but not yet saved would be silently replaced by the server's copy of that row.
// It cannot just be carried over either — the numbers it was made against have moved —
// so pending edits are saved first, under the numbering they were made under.
SAVED.length = 0
const nameBox = $('.currow:not(.curhead)')[0].querySelector('.c-name')
await act(async () => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  setter.call(nameBox, 'Edited but not saved')
  nameBox.dispatchEvent(new window.Event('input', { bubbles: true }))
  await new Promise((r) => setTimeout(r, 40))
})
await click($('.insertbar')[0])
check('an unsaved edit is saved before the renumber, not thrown away',
      SAVED.some((r) => r.session_name === 'Edited but not saved'),
      JSON.stringify(SAVED))
check('…and the user is told it happened', text().includes('edited session(s) first'))
check('…and the edit survives in the table',
      $('.currow:not(.curhead)').some((r) => r.querySelector('.c-name').value === 'Edited but not saved'),
      $('.currow:not(.curhead)').map((r) => r.querySelector('.c-name').value).join(' | '))

console.log('\n== a duplicate session number is caught before it overwrites anything ==')
const firstNo = $('input.c-no')[0]
// Collide with whatever the SECOND row is actually numbered, rather than a number from
// the fixture: the rows have been renumbered by the inserts above, and a hardcoded
// value silently stopped being a duplicate at all.
const secondNo = $('input.c-no')[1].value
await act(async () => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  setter.call(firstNo, String(secondNo))
  firstNo.dispatchEvent(new window.Event('input', { bubbles: true }))
  await new Promise((r) => setTimeout(r, 40))
})
check('the clash is reported', text().includes('share session number'))
const saveBtn = $('button').find((b) => b.textContent.includes('Save changes'))
check('…and Save is blocked until it is resolved', saveBtn.disabled)

console.log('\n== three controls are gone from the curriculum toolbar ==')
// Removed on request. Asserted rather than assumed: each was a live control, and
// "I removed it" is exactly the kind of claim a build cannot check.
check('no "Re-check all decks"', !text().includes('Re-check all decks'))
check('no "shared with <team>" chip', !text().includes('shared with OS Curriculum Team'))
check('no re-import-from-sheet button',
      !text().includes('Re-import this course from its sheet')
      && !text().includes('Import rows from a sheet'))
check('…and the actions that stayed are still there',
      text().includes('Save changes') && text().includes('Fetch new decks'))

console.log('\n== sharing a course with a team ==')
check('a team that does NOT own it is offered', text().includes('Share with'))
const shareSel = $('.sharebox select')[0]
// Computed against whichever course is actually open, rather than a hardcoded team name:
// the workspace the tests above leave behind decides that, and only the teams that do NOT
// already own the open course are offered.
const openCourse = $('.navselect')[0].value
const expectShare = [
  { name: 'OS Curriculum Team', courses: ['Operating Systems', 'Computer Networks'] },
  { name: 'Networks Team', courses: ['Computer Networks'] },
].filter((t) => !t.courses.includes(openCourse)).map((t) => t.name).join()
const offered = Array.from(shareSel.options).filter((o) => o.value)
  .map((o) => o.textContent).join()
check('…and only the teams that do not already own the open course are listed',
      offered === expectShare,
      `open=${openCourse} offered=${offered} expected=${expectShare}`)

console.log('\n== the create-course flow is reachable ==')
await click($('.navlink').find((b) => b.textContent.includes('Create new course')))
check('the create form appears', text().includes('Create a new course'))
check('the course name is editable when creating',
      $('input').some((i) => !i.disabled && i.placeholder?.includes('Computer Networks')))
check('the PREVIOUS course\'s curriculum is no longer on screen',
      $('.curtable').length === 0)

console.log('\n== a new course lands on its own rules, before anything is generated ==')
// Setting what a course is written under belongs at the START. The alternative is
// generating a document under rules nobody set and correcting it a session at a time.
const newNameBox = $('input').find((i) => i.placeholder?.includes('Computer Networks'))
const newLinkBox = $('input').find((i) => i.placeholder?.includes('docs.google.com/spreadsheets'))
const setInputVal = (el, v) => act(async () => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  setter.call(el, v)
  el.dispatchEvent(new window.Event('input', { bubbles: true }))
})
await setInputVal(newNameBox, 'Brand New Course')
await setInputVal(newLinkBox, 'https://docs.google.com/spreadsheets/d/NEW/edit')
await click($('button').find((b) => b.textContent.includes('Create course')))
await act(async () => { await new Promise((r) => setTimeout(r, 1300)) })
check('creating a course opens its Course rules',
      text().includes('Course rules —'),
      text().replace(/\s+/g, ' ').slice(0, 140))
check('…and says why it opened there',
      text().includes('is created. Set what it is written under before you generate'),
      text().replace(/\s+/g, ' ').match(/.{0,40}is created.{0,60}/)?.[0])
check('…while making clear nothing is locked',
      text().includes('add, edit and retire any of it later'))
check('…and it can be skipped', $('button').some((b) => b.textContent.includes('Skip for now')))

console.log(`\n${pass} passed, ${fail} failed`)
fs.rmSync(tmp, { force: true })
process.exit(fail ? 1 : 0)
