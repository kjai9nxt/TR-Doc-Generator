/* THEME SCREENSHOTS — render the real UI in both themes and look at it.
 *
 *     cd frontend && npm run test:shots
 *
 * The contrast audit proves the PALETTE is readable; it cannot prove the app looks
 * right — that a card has a visible edge against the page, that the switch reads as one
 * object, that nothing has gone white-on-white because a rule was scoped to the wrong
 * selector. That needs pixels.
 *
 * Rather than drive a browser through Google sign-in, this takes the markup the jsdom
 * harness already mounts against a stubbed backend, inlines the BUILT stylesheet (so it
 * is the shipped CSS being judged, not the source), and renders it twice in headless
 * Chrome. Output: test/shots/{dark,light}.png.
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const FRONTEND = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const dumped = path.join(FRONTEND, 'test', '_shot.curriculum.html')
if (!fs.existsSync(dumped)) {
  console.error('no DOM dump — run:  THEME_SHOTS=1 node test/ui_smoke.mjs')
  process.exit(1)
}
const assets = path.join(FRONTEND, 'dist', 'assets')
const cssFile = fs.readdirSync(assets).filter((f) => f.endsWith('.css')).sort().pop()
if (!cssFile) { console.error('no built CSS in dist/assets — run `npm run build`'); process.exit(1) }
const css = fs.readFileSync(path.join(assets, cssFile), 'utf8')
const body = fs.readFileSync(dumped, 'utf8').match(/<body[^>]*>([\s\S]*)<\/body>/)[1]

const outDir = path.join(FRONTEND, 'test', 'shots')
fs.mkdirSync(outDir, { recursive: true })
for (const themeName of ['dark', 'light']) {
  const html = `<!doctype html><html data-theme="${themeName}"><head><meta charset="utf-8">
<style>${css}</style></head><body>${body}</body></html>`
  const page = path.join(outDir, `_${themeName}.html`)
  fs.writeFileSync(page, html)
  const png = path.join(outDir, `${themeName}.png`)
  execFileSync('google-chrome', [
    '--headless', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--force-color-profile=srgb', '--window-size=1440,1500',
    `--screenshot=${png}`, `file://${page}`,
  ], { stdio: 'pipe' })
  console.log(`  ${themeName.padEnd(5)} -> ${path.relative(FRONTEND, png)}  ` +
              `${(fs.statSync(png).size / 1024).toFixed(0)} kB`)
}
