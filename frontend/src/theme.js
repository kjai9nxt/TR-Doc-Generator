/* THE COLOUR THEME — the app's half of it.
 *
 * The preference is three-valued ('system' | 'light' | 'dark') and the DOM is
 * two-valued: `data-theme` on <html> is always a concrete 'light' or 'dark', so the
 * stylesheet defines the light palette once instead of once per media query.
 *
 * DELIBERATE DUPLICATE: index.html carries an inline copy of resolve/apply that runs
 * in <head>, before the bundle. It has to. A theme resolved here — inside the React
 * tree — paints the default palette for one frame and then flips, and on a ground this
 * dark that flash is the most visible thing about the feature. The inline script also
 * owns the `prefers-color-scheme` listener, since tracking the OS is a document-level
 * job that must keep working whether or not this module was ever imported.
 *
 * So: index.html owns FIRST PAINT and OS changes; this module owns USER CHOICE. Both
 * write the same key and the same attribute, and `apply()` here falls back to doing the
 * work itself when the inline script is absent — which is the case in the jsdom test
 * harness, where App.jsx is mounted without the HTML shell.
 */
export const KEY = 'trdoc.theme'
export const PREFS = ['light', 'system', 'dark']

/** The stored choice, or null when there is none / storage is unavailable. */
function readStored() {
  try {
    const v = localStorage.getItem(KEY)
    return PREFS.includes(v) ? v : null
  } catch { return null }                   // private mode / storage disabled
}

/** The user's choice. Unset means 'dark': this app is designed dark, so light is an
 *  opt-in rather than something an OS setting can hand someone silently. */
export function getPref() {
  return readStored() || 'dark'
}

export function resolve(pref) {
  if (pref === 'light' || pref === 'dark') return pref
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  } catch { return 'dark' }
}

/** Persist a choice and paint it. Returns the concrete theme now in force. */
export function setPref(pref) {
  const p = PREFS.includes(pref) ? pref : 'system'
  if (typeof window.__setTheme === 'function') {
    window.__setTheme(p)                    // the shell's copy: keeps one writer
    return (window.__theme && window.__theme.resolved) || resolve(p)
  }
  try { localStorage.setItem(KEY, p) } catch { /* nothing to do */ }
  const resolved = resolve(p)
  document.documentElement.setAttribute('data-theme', resolved)
  window.__theme = { pref: p, resolved }
  return resolved
}

/** Paint whatever is already stored, for a mount with no HTML shell.
 *
 * localStorage is read FIRST and `window.__theme` is only the fallback, because the
 * one caller that is not a first mount is the cross-tab `storage` handler: there the
 * new value is in storage and `window.__theme` still holds this tab's stale choice, so
 * preferring the in-memory copy made the other tab's change a no-op. Storage is the
 * shared writer, so storage wins; `window.__theme` covers the case where there is no
 * storage to read (private mode), where an in-session choice is all there is.
 */
export function apply() {
  const pref = readStored() || (window.__theme && window.__theme.pref) || 'dark'
  const resolved = resolve(pref)
  if (document.documentElement.getAttribute('data-theme') !== resolved) {
    document.documentElement.setAttribute('data-theme', resolved)
  }
  window.__theme = { pref, resolved }
  return pref
}
