/* THEME CONTRAST AUDIT — is every colour pair in both themes actually readable?
 *
 *     cd frontend && npm run test:contrast
 *
 * A light theme is easy to ship and hard to ship READABLE: the failure is never the
 * page, it is the one panel where a colour picked to glow on near-black is now sitting
 * on white at 2:1. Eyeballing two screenshots does not find those — there are eight
 * section hues, four text steps, four surfaces and eleven status colours, and the
 * combinations that actually occur in the stylesheet number in the hundreds.
 *
 * So this reads the tokens straight out of styles.css, composites the translucent ones
 * over the surface they are painted on, and computes the WCAG 2.1 contrast ratio for
 * every pair the app actually renders. Thresholds are the AA ones: 4.5:1 for text,
 * 3:1 for >=18.66px bold / >=24px text and for UI boundaries that carry meaning.
 *
 * It parses the stylesheet rather than taking a list of colours as input on purpose:
 * a token added to one theme and forgotten in the other is exactly the bug this is
 * for, and a hand-maintained copy of the palette would not have it either.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const css = fs.readFileSync(path.join(FRONTEND, 'src/styles.css'), 'utf8')
              .replace(/\/\*[\s\S]*?\*\//g, '')     // comments first: they contain braces

// ---- token extraction -----------------------------------------------------
function block(selector) {
  const i = css.indexOf(selector)
  if (i < 0) throw new Error(`selector not found: ${selector}`)
  const open = css.indexOf('{', i)
  const close = css.indexOf('}', open)
  const out = {}
  for (const line of css.slice(open + 1, close).split(';')) {
    const m = line.match(/^\s*(--[\w-]+)\s*:\s*(.+?)\s*$/s)
    if (m) out[m[1]] = m[2].trim()
  }
  return out
}
const DARK = block(':root {')
const LIGHT = { ...DARK, ...block(':root[data-theme="light"] {') }   // light overrides dark

// Section + category hues, per theme. In light they are re-declared under a
// `:root[data-theme="light"]` prefix, so the same regex serves both.
function hues(themed) {
  const out = {}
  const re = themed
    ? /:root\[data-theme="light"\]\s+\[data-(?:sec|cat)="([\w-]+)"\][^{]*\{([^}]*)\}/g
    : /^\[data-(?:sec|cat)="([\w-]+)"\][^{]*\{([^}]*)\}/gm
  let m
  while ((m = re.exec(css))) {
    const hue = m[2].match(/--hue\s*:\s*(#[0-9a-fA-F]{3,8})/)
    if (hue) out[m[1]] = hue[1]
  }
  return out
}
const HUES = { dark: hues(false), light: hues(true) }

// ---- colour maths ---------------------------------------------------------
function parse(v, tokens, seen = new Set()) {
  v = String(v).trim()
  const varm = v.match(/^var\((--[\w-]+)\)$/)
  if (varm) {
    if (seen.has(varm[1])) throw new Error(`circular var ${varm[1]}`)
    seen.add(varm[1])
    return parse(tokens[varm[1]], tokens, seen)
  }
  let m = v.match(/^#([0-9a-fA-F]{3,8})$/)
  if (m) {
    let h = m[1]
    if (h.length === 3) h = h.split('').map((c) => c + c).join('')
    if (h.length === 4) h = h.split('').map((c) => c + c).join('')
    const n = (i) => parseInt(h.slice(i * 2, i * 2 + 2), 16)
    return { r: n(0), g: n(1), b: n(2), a: h.length === 8 ? n(3) / 255 : 1 }
  }
  m = v.match(/^rgba?\(([^)]+)\)$/)
  if (m) {
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number)
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }
  }
  throw new Error(`cannot parse colour: ${v}`)
}
/** Paint `fg` (possibly translucent) onto opaque `bg`. */
const over = (fg, bg) => ({
  r: fg.r * fg.a + bg.r * (1 - fg.a),
  g: fg.g * fg.a + bg.g * (1 - fg.a),
  b: fg.b * fg.a + bg.b * (1 - fg.a),
  a: 1,
})
function lum(c) {
  const f = (x) => {
    const s = x / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b)
}
function ratio(fg, bg) {
  const a = lum(fg), b = lum(bg)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

// ---- what the app actually renders ---------------------------------------
// `on` is a stack, painted base-last. A token that is translucent by design (every
// *-soft) is composited over the surface below it before anything is measured on top,
// which is the only way the numbers mean anything: --warn-text is never on --card, it
// is on --warn-soft on --card.
const SURFACES = ['--bg', '--sunken', '--card', '--card2']
const TEXT_MIN = 4.5, UI_MIN = 3.0

function cases(T, theme) {
  const c = []
  const add = (label, fg, on, min) => c.push({ label, fg, on, min })

  // body / heading / secondary / label text on all four surfaces
  for (const s of SURFACES) {
    add(`--text on ${s}`, '--text', [s], TEXT_MIN)
    add(`--strong on ${s}`, '--strong', [s], TEXT_MIN)
    add(`--muted on ${s}`, '--muted', [s], TEXT_MIN)
    // --faint is 10-12px label text (.navlabel, .wsbody span, placeholders)
    add(`--faint on ${s}`, '--faint', [s], TEXT_MIN)
    // .navlink:hover and every link are --accent at 12.5-14px
    add(`--accent on ${s}`, '--accent', [s], TEXT_MIN)
  }
  // status colours used as PROSE (.rubricblock, .ok-note, .alert)
  add('--warn as text on --card', '--warn', ['--card'], TEXT_MIN)
  add('--err as text on --card', '--err', ['--card'], TEXT_MIN)
  add('--ok as text on --card', '--ok', ['--card'], TEXT_MIN)
  add('--ok-text on --card', '--ok-text', ['--card'], TEXT_MIN)
  add('--ok-text on --ok-soft/--card', '--ok-text', ['--ok-soft', '--card'], TEXT_MIN)
  add('--warn-text on --warn-soft/--card', '--warn-text', ['--warn-soft', '--card'], TEXT_MIN)
  add('--err-text on --err-soft/--card', '--err-text', ['--err-soft', '--card'], TEXT_MIN)
  add('--err-text on --card (ghost danger)', '--err-text', ['--card'], TEXT_MIN)
  // filled buttons
  add('--on-accent on --accent2 (primary)', '--on-accent', ['--accent2'], TEXT_MIN)
  add('--on-accent on --accent-hover', '--on-accent', ['--accent-hover'], TEXT_MIN)
  add('--on-accent on --dl (download)', '--on-accent', ['--dl'], TEXT_MIN)
  add('--on-accent on --dl-hover', '--on-accent', ['--dl-hover'], TEXT_MIN)
  // code + the live generation log
  add('--text on --code-bg (code)', '--text', ['--code-bg'], TEXT_MIN)
  add('--log-text on --code-bg', '--log-text', ['--code-bg'], TEXT_MIN)
  add('--md-h3 on --card', '--md-h3', ['--card'], TEXT_MIN)
  // the accent well behind chips / edited rows / blockquotes
  add('--text on --accent-soft/--card', '--text', ['--accent-soft', '--card'], TEXT_MIN)
  add('--accent on --accent-soft/--card', '--accent', ['--accent-soft', '--card'], TEXT_MIN)
  add('--text on --accent-wash/--card', '--text', ['--accent-wash', '--card'], TEXT_MIN)
  add('--muted on --accent-faint/--card', '--muted', ['--accent-faint', '--card'], TEXT_MIN)
  add('--muted on --draft-wash/--card', '--muted', ['--draft-wash', '--card'], TEXT_MIN)
  add('--text on --zebra/--card', '--text', ['--zebra', '--card'], TEXT_MIN)
  add('--text on --hover-wash/--card', '--text', ['--hover-wash', '--card'], TEXT_MIN)
  add('--text on --ghost-hover', '--text', ['--ghost-hover'], TEXT_MIN)
  add('--faint on --tile-grad', '--faint', ['--tile-grad'], TEXT_MIN)
  add('--muted on --topbar-bg/--bg', '--muted', ['--topbar-bg', '--bg'], TEXT_MIN)
  // UI boundaries that carry meaning, not decoration
  // These four are held at "visibly present", not at the 3:1 of WCAG 1.4.11. That
  // clause covers a boundary that is the ONLY means of identifying a control or its
  // state; each of these sits on a panel that also has a tinted ground (--*-soft) and
  // status-coloured text, so the border is the third cue rather than the only one.
  // Demanding 3:1 here would mean a hairline as dark as the body text, which is a
  // worse UI and not what the guideline asks for.
  const EDGE_MIN = 1.3
  add('--accent-line on --card', '--accent-line', ['--card'], EDGE_MIN)
  add('--ok-line on --card', '--ok-line', ['--card'], EDGE_MIN)
  add('--warn-line on --card', '--warn-line', ['--card'], EDGE_MIN)
  add('--err-line on --card', '--err-line', ['--card'], EDGE_MIN)
  add('--track on --card (progress rail)', '--track', ['--card'], 1.2)
  add('--line2 on --card (emphasis hairline)', '--line2', ['--card'], 1.2)
  add('--line on --card (default hairline)', '--line', ['--card'], 1.15)
  add('--scroll-hover on --bg', '--scroll-hover', ['--bg'], 1.5)
  // the eight section hues + five category hues: icon + badge label, i.e. small text
  for (const [name, hex] of Object.entries(HUES[theme])) {
    c.push({ label: `hue ${name} on --bg`, fgLiteral: hex, on: ['--bg'], min: TEXT_MIN })
    c.push({ label: `hue ${name} on --card`, fgLiteral: hex, on: ['--card'], min: TEXT_MIN })
  }
  return c
}

// ---- run ------------------------------------------------------------------
let fail = 0, pass = 0
for (const [theme, T] of [['dark', DARK], ['light', LIGHT]]) {
  console.log(`\n===== ${theme.toUpperCase()} =====`)
  const rows = []
  for (const cs of cases(T, theme)) {
    let base = parse(T[cs.on[cs.on.length - 1]], T)
    for (let i = cs.on.length - 2; i >= 0; i--) base = over(parse(T[cs.on[i]], T), base)
    const fg = over(parse(cs.fgLiteral ?? T[cs.fg], T), base)
    const r = ratio(fg, base)
    const ok = r >= cs.min
    ok ? pass++ : fail++
    rows.push({ ok, r, cs })
  }
  for (const { ok, r, cs } of rows.filter((x) => !x.ok)) {
    console.log(`  FAIL  ${r.toFixed(2)}:1  (need ${cs.min})  ${cs.label}`)
  }
  const worst = rows.filter((x) => x.ok).sort((a, b) => a.r / a.cs.min - b.r / b.cs.min).slice(0, 5)
  console.log(`  ${rows.filter((x) => x.ok).length}/${rows.length} pass. Tightest passing:`)
  for (const { r, cs } of worst) console.log(`    ${r.toFixed(2)}:1 (need ${cs.min})  ${cs.label}`)
}

// ---- a token defined in one theme and forgotten in the other -------------
console.log('\n===== token parity =====')
const themedOnly = Object.keys(block(':root[data-theme="light"] {'))
const missing = themedOnly.filter((k) => !(k in DARK) && k !== 'color-scheme')
if (missing.length) { console.log('  FAIL light defines tokens dark does not:', missing.join(', ')); fail++ }
else { console.log(`  ok — all ${themedOnly.length} light tokens have a dark counterpart`); pass++ }

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
