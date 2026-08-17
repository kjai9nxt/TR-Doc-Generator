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
const COURSES = [
  { name: 'Operating Systems', sessions: 34, teams: ['OS Curriculum Team'],
    members: ['dev@nxtwave.co.in'], mine: true },
  { name: 'Computer Networks', sessions: 31, teams: [], members: [], mine: false },
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
    user: { email: 'dev@nxtwave.co.in', is_admin: true },
    status: { key_ok: true, saved_links: { course: 'https://docs.google.com/spreadsheets/d/X/edit' },
              settings: { course_type: 'semester', course_name: 'Operating Systems' },
              policy: { judge_always_on: true, time_always_enforced: true,
                        max_minutes: 40, max_pages: 26, target_pages: 23 } },
    course: 'Operating Systems',
    courses: COURSES,
    workspaces: { teams: [
      { id: 4, name: 'OS Curriculum Team', courses: ['Operating Systems', 'Computer Networks'],
        members: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in'], unknown_courses: [] },
      { id: 5, name: 'Networks Team', courses: ['Computer Networks'],
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
  '/workspaces': { individual: { courses: ['Operating Systems'] },
                   teams: [
                     { id: 4, name: 'OS Curriculum Team', courses: ['Operating Systems', 'Computer Networks'],
                       members: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in'], unknown_courses: [] },
                     // A second team that does NOT own the open course — that is what
                     // makes the "share this course with a team" control applicable.
                     { id: 5, name: 'Networks Team', courses: ['Computer Networks'],
                       members: ['dev@nxtwave.co.in'], unknown_courses: [] },
                   ] },
  '/curriculum': { course: 'Operating Systems', rows: ROWS, imported_from: 'https://docs.google.com/spreadsheets/d/X/edit', pending: 0 },
  '/sessions': { sessions: SESSIONS },
  '/my/history': { courses: [{ course: 'Operating Systems', runs: [
      { id: 'r1', session_no: 30, title: 'I/O Systems', user_email: 'dev@nxtwave.co.in',
        status: 'done', accepted: true, rubric: 100, cost: {}, calls: [], ts: '2026-08-16T10:00:00Z' }],
      summary: { runs: 1 } }], summary: { runs: 1 } },
  '/my/teams': { teams: [{ team: { id: 4, name: 'OS Curriculum Team', course: 'Operating Systems',
                                  courses: ['Operating Systems', 'Computer Networks'],
                                  members: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in'] },
                           members: ['dev@nxtwave.co.in', 'colleague@nxtwave.co.in'],
                           contributors: ['dev@nxtwave.co.in'],
                           summary: { runs: 2 },
                           courses: [{ course: 'Operating Systems', runs: [
                             { id: 'r1', session_no: 30, title: 'I/O Systems', user_email: 'dev@nxtwave.co.in', status: 'done', accepted: true, cost: {}, calls: [] },
                             { id: 'r2', session_no: 29, title: 'File Systems', user_email: 'colleague@nxtwave.co.in', status: 'done', accepted: true, cost: {}, calls: [] }],
                             summary: { runs: 2 } }] }] },
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
  '/guided/g31': { status: 'reviewing', session_no: 31,
                   session_title: 'Spooling, Buffering & Disk Structure',
                   total: 6, index: 2, labels: ['Opening'], chunks: [], logs: [] },
  '/dashboard': { courses: [], summary: {} },
  '/template-guide': { markdown: '# Sheet template\n\nColumns…' },
}

const calls = []
function route(url) {
  const p = String(url).replace(/^\/api/, '').split('?')[0]
  calls.push(p)
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
global.fetch = async (url) => ({
  ok: true, status: 200, headers: { get: () => '' },
  json: async () => route(url), blob: async () => ({}),
})

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

await click(byLabel('Team'))
check('Team shows the team name', text().includes('OS Curriculum Team'))
check('…its members', text().includes('colleague@nxtwave.co.in'))
check('…and every course it owns', text().includes('Computer Networks'))

await click(byLabel('History'))
check('team history shows a COLLEAGUE\'s doc, not just mine',
      text().includes('File Systems') && text().includes('colleague@nxtwave.co.in'))

const indWs = $('.wsopt').find((b) => b.textContent.includes('Individual'))
await click(indWs)
check('back to Individual',
      $('.wsopt').find((b) => b.textContent.includes('Individual')).className.includes('on'))
check('the Team tab disappears again', byLabel('Team') === undefined)

console.log('\n== an open run names its own session, and flags a mismatch ==')
await click(byLabel('Generate'))
const sessSel = $('select').find((s) => Array.from(s.options).some((o) => o.textContent.includes('Spooling')))
check('an abandoned run is offered', text().includes('Unfinished TR doc'))
await click($('button').find((b) => b.textContent.includes('Resume')))
check('the panel names the session the RUN is for',
      text().includes('Generating Session 31'))
check('…and the doc it will produce is stated when it differs from the picker',
      $('.runhead.mismatch').length === 0 || text().includes('will produce that document'))

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
const nums = $('input.c-no').map((i) => Number(i.value))
check('the new row takes a free session number, disturbing none of the others',
      new Set(nums).size === nums.length, JSON.stringify(nums))
await click($('.insertbar')[0])
check('inserting at the TOP puts the row first',
      $('.currow')[1].querySelector('input.c-name').value === '')

console.log('\n== a duplicate session number is caught before it overwrites anything ==')
const firstNo = $('input.c-no')[0]
await act(async () => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  setter.call(firstNo, String(ROWS[0].session_no))
  firstNo.dispatchEvent(new window.Event('input', { bubbles: true }))
  await new Promise((r) => setTimeout(r, 40))
})
check('the clash is reported', text().includes('share session number'))
const saveBtn = $('button').find((b) => b.textContent.includes('Save changes'))
check('…and Save is blocked until it is resolved', saveBtn.disabled)

console.log('\n== sharing a course with a team ==')
check('the team that already owns it is shown, not offered again',
      text().includes('shared with OS Curriculum Team'))
check('a team that does NOT own it is offered', text().includes('Share with'))
const shareSel = $('.sharebox select')[0]
check('…and it is the only one listed',
      Array.from(shareSel.options).filter((o) => o.value).map((o) => o.textContent)
        .join() === 'Networks Team')

console.log('\n== the create-course flow is reachable ==')
await click($('.navlink').find((b) => b.textContent.includes('Create new course')))
check('the create form appears', text().includes('Create a new course'))
check('the course name is editable when creating',
      $('input').some((i) => !i.disabled && i.placeholder?.includes('Computer Networks')))
check('the PREVIOUS course\'s curriculum is no longer on screen',
      $('.curtable').length === 0)

console.log(`\n${pass} passed, ${fail} failed`)
fs.rmSync(tmp, { force: true })
process.exit(fail ? 1 : 0)
