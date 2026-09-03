/* THE ICON SET.
 *
 * Every icon in this app used to be an emoji — 📚 for the curriculum, ✨ for Generate,
 * 🎯 for the course rules. Emoji are the fastest way to make a serious tool look like a
 * toy: they render in a different font from everything around them, they are a
 * different colour on every operating system (and a different PICTURE on some), they
 * cannot inherit the text colour of the thing they sit in, and they cannot be sized to
 * the type they label. A 16px stroke icon that takes `currentColor` does all four.
 *
 * One file, one component, one visual grammar: a 24-unit box, 1.75 stroke, round caps
 * and joins, no fills. Drawn to be legible at 16px, which is the only size that matters
 * here. Anything added later must follow the same grammar or it will read as pasted in
 * from somewhere else.
 */
import React from 'react'

const P = {
  // --- navigation ------------------------------------------------------------
  curriculum: <><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H10a2 2 0 0 1 2 2v13a2 2 0 0 0-2-2H5.5A1.5 1.5 0 0 1 4 15.5z"/><path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H14a2 2 0 0 0-2 2v13a2 2 0 0 1 2-2h4.5a1.5 1.5 0 0 0 1.5-1.5z"/></>,
  generate: <><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="3.2"/></>,
  history: <><path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><path d="M3 4v4h4"/><path d="M12 8v4.4l2.9 1.7"/></>,
  team: <><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19.5a5.7 5.7 0 0 1 11 0"/><path d="M16.2 5.2a3.2 3.2 0 0 1 0 5.9M18 19.5a5.7 5.7 0 0 0-1.6-4"/></>,
  skills: <><path d="M4 6h9M4 12h13M4 18h7"/><path d="M17.5 5.5 19 7l3-3"/></>,
  brain: <><path d="M12 5.5a3 3 0 0 0-5.7-1.3A2.8 2.8 0 0 0 4 9.4a3 3 0 0 0 .6 5A2.9 2.9 0 0 0 9.3 20a2.9 2.9 0 0 0 2.7-1.7z"/><path d="M12 5.5a3 3 0 0 1 5.7-1.3A2.8 2.8 0 0 1 20 9.4a3 3 0 0 1-.6 5A2.9 2.9 0 0 1 14.7 20a2.9 2.9 0 0 1-2.7-1.7z"/><path d="M12 5.5v13"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 14.5a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1v.3a2 2 0 1 1-4 0v-.2a1.6 1.6 0 0 0-2.8-1.1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7h-.3a2 2 0 1 1 0-4h.2a1.6 1.6 0 0 0 1.1-2.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3h.1a1.6 1.6 0 0 0 1-1.4v-.3a2 2 0 1 1 4 0v.2a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7h.3a2 2 0 1 1 0 4h-.2a1.6 1.6 0 0 0-1.4 1z"/></>,
  // --- workspace -------------------------------------------------------------
  person: <><circle cx="12" cy="8" r="3.4"/><path d="M5 20a7 7 0 0 1 14 0"/></>,
  // --- actions ---------------------------------------------------------------
  save: <><path d="M5 4h11l3 3v13H5z"/><path d="M9 4v5h6V4M8 20v-6h8v6"/></>,
  download: <><path d="M12 3v12"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M4 20h16"/></>,
  plus: <><path d="M12 5v14M5 12h14"/></>,
  check: <><path d="m5 12.5 5 5L19 6.5"/></>,
  x: <><path d="M6 6l12 12M18 6 6 18"/></>,
  pencil: <><path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="m14.5 6.5 3 3"/></>,
  trash: <><path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="M6 7l1 13h10l1-13"/></>,
  doc: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></>,
  search: <><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></>,
  // --- the skill categories --------------------------------------------------
  // A sequence: three stops on one line, which is exactly what a teaching flow is.
  flow: <><circle cx="5" cy="12" r="2.2"/><circle cx="12" cy="12" r="2.2"/><circle cx="19" cy="12" r="2.2"/><path d="M7.2 12h2.6M14.2 12h2.6"/></>,
  book: <><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H5.5A1.5 1.5 0 0 0 4 19.5z"/><path d="M8 8h7M8 11.5h5"/></>,
  image: <><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="m4 17 4.5-4.5L12 16l3-3 5 5"/></>,
  flag: <><path d="M5 21V4.5h13l-2.5 4 2.5 4H5"/></>,
  globe: <><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c2.2 2.4 3.4 5.4 3.4 8.5s-1.2 6.1-3.4 8.5c-2.2-2.4-3.4-5.4-3.4-8.5S9.8 5.9 12 3.5z"/></>,
  // --- states ----------------------------------------------------------------
  info: <><circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.4"/></>,
  warn: <><path d="M10.3 4.3 2.8 17.2A1.9 1.9 0 0 0 4.4 20h15.2a1.9 1.9 0 0 0 1.6-2.8L13.7 4.3a1.9 1.9 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 16.7v.3"/></>,
  spark: <><path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.5l-1.9-5.7L4.5 11 10.1 9z"/></>,
  link: <><path d="M10.5 13.5a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-1.5 1.5"/><path d="M13.5 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.5 3.5 0 0 0 5 5L12 17"/></>,
  chevron: <><path d="m9 5 7 7-7 7"/></>,
  expand: <><path d="M4 9V4h5M20 15v5h-5M15 4h5v5M9 20H4v-5"/></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.1-4.4"/><path d="M4 5.5V10h4.5"/><path d="M4 13a8 8 0 0 0 14.1 4.4"/><path d="M20 18.5V14h-4.5"/></>,
  scissors: <><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="6.5" cy="17.5" r="2.5"/><path d="M8.7 8.2 20 18M20 6 8.7 15.8"/></>,
  beaker: <><path d="M9.5 3v6.2L4.6 17a2 2 0 0 0 1.7 3h11.4a2 2 0 0 0 1.7-3l-4.9-7.8V3"/><path d="M8 3h8M6.9 14h10.2"/></>,
  folder: <><path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h8a1.5 1.5 0 0 1 1.5 1.5v7.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/></>,
  coin: <><circle cx="12" cy="12" r="8.5"/><path d="M14.5 9.2A3 3 0 0 0 12 8c-1.6 0-2.6.8-2.6 1.9 0 2.7 5.4 1.4 5.4 4.2 0 1.2-1.1 1.9-2.8 1.9a3 3 0 0 1-2.5-1.2"/><path d="M12 6.4v11.2"/></>,
  wrench: <><path d="M14.7 6.3a4.5 4.5 0 0 0 5.8 5.8l-8 8a2.5 2.5 0 1 1-3.5-3.5l8-8z"/><path d="M6.5 6.5 4 4"/></>,
  chat: <><path d="M20.5 12c0 4.1-3.8 7.4-8.5 7.4a10 10 0 0 1-2.4-.3L4.5 21l1.2-3.6A7 7 0 0 1 3.5 12c0-4.1 3.8-7.4 8.5-7.4s8.5 3.3 8.5 7.4z"/></>,
  traffic: <><rect x="7.5" y="2.5" width="9" height="19" rx="3"/><path d="M12 7v.4M12 12v.4M12 17v.4"/></>,
  // --- theme -----------------------------------------------------------------
  // Three, because the choice is three-valued: follow the OS, or override it either
  // way. Drawn at the same 24-box weight as the rest so the switch in the top bar
  // reads as part of the bar and not as a widget dropped into it.
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M18.5 5.5l-1.7 1.7M7.2 16.8l-1.7 1.7"/></>,
  moon: <><path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.5 8.5 0 1 0 10.2 10.2z"/></>,
  monitor: <><rect x="3" y="4.5" width="18" height="12" rx="2"/><path d="M9 20h6M12 16.5V20"/></>,
}

export const ICON_NAMES = Object.keys(P)

export default function Icon({ name, size = 16, className = '', ...rest }) {
  const d = P[name]
  if (!d) return null
  return (
    <svg className={`ic ${className}`} width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true" focusable="false" {...rest}>
      {d}
    </svg>
  )
}
