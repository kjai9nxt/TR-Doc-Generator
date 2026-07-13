"""TR Doc Generator — web frontend (Streamlit).

Launch:
    cd "/home/nxtwave/Desktop/TR Doc Generator"
    streamlit run app.py

Flow:
    1. Connect your two Google Sheets (links) -> validate templates -> sync.
    2. Pick a session -> generate -> watch live progress.
    3. Review the grades and download the Word (.docx) TR doc.
"""
from __future__ import annotations
from pathlib import Path

import streamlit as st

from src import config, sheets, sync, course_loader, pipeline

st.set_page_config(page_title="TR Doc Generator", page_icon="📝", layout="wide")

# --------------------------------------------------------------------------- #
# session state
# --------------------------------------------------------------------------- #
ss = st.session_state
ss.setdefault("synced", False)
ss.setdefault("sessions", [])
ss.setdefault("sync_result", None)
ss.setdefault("gen_result", None)

prev_course, prev_details = sync.last_links()


# --------------------------------------------------------------------------- #
# header + provider status
# --------------------------------------------------------------------------- #
st.title("📝 TR Doc Generator")
st.caption("Generate a recording-ready Word TR doc for one session, in sync with your two Google Sheets.")

m = config.harness()["model"]
key_ok = config.api_key() is not None
c1, c2, c3 = st.columns(3)
c1.metric("Provider", m.get("provider", "?"))
c2.metric("Model", m.get("generator", "?").split("/")[-1])
c3.metric("API key", "✅ loaded" if key_ok else "❌ missing")
if not key_ok:
    st.error("No API key detected. Add it to the `.env` file, then reload this page.")

with st.expander("📋 How your Google Sheets must look (template)"):
    st.markdown(sheets.guide_text())

st.divider()

# --------------------------------------------------------------------------- #
# STEP 1 — connect sheets
# --------------------------------------------------------------------------- #
st.header("1 · Connect your sheets")
col_a, col_b = st.columns(2)
course_link = col_a.text_input("Course Curriculum Structure — Google Sheet link",
                               value=prev_course or "",
                               placeholder="https://docs.google.com/spreadsheets/d/.../edit")
details_link = col_b.text_input("Session Details (past decks) — Google Sheet link",
                                value=prev_details or "",
                                placeholder="https://docs.google.com/spreadsheets/d/.../edit")

if st.button("🔄 Connect & Sync", type="primary", disabled=not (course_link and details_link)):
    try:
        with st.spinner("Validating templates, reading sheets, ingesting decks…"):
            res = sync.sync(course_link, details_link, verbose=True)
        ss.sync_result = res
        ss.sessions = course_loader.load_sessions()
        ss.synced = True
    except sheets.TemplateError as e:
        ss.synced = False
        st.error("Template check failed — the sheet was discarded.")
        st.code(str(e))
        st.info("Fix the sheet to match the template above, then Connect & Sync again.")
    except Exception as e:
        ss.synced = False
        st.error(f"Could not sync: {e}")
        st.info("Make sure both sheets and the linked Slides are shared "
                "'Anyone with the link → Viewer'.")

# show sync outcome
if ss.sync_result is not None and ss.synced:
    res = ss.sync_result
    m1, m2, m3 = st.columns(3)
    m1.metric("Sessions", res.sessions)
    m2.metric("Decks ingested", res.decks_ingested)
    m3.metric("Decks cached", res.decks_cached)
    if res.changelog:
        with st.expander(f"🔍 Changes detected this sync ({len(res.changelog)})", expanded=True):
            for c in res.changelog:
                st.write("•", c)
    else:
        st.success("In sync — no changes since last time.")
    if res.errors:
        for e in res.errors:
            st.warning(e)

# --------------------------------------------------------------------------- #
# STEP 2 — generate
# --------------------------------------------------------------------------- #
if ss.synced and ss.sessions:
    st.divider()
    st.header("2 · Generate a TR doc")

    labels = {f"{s.number} — {s.name}": s.number for s in ss.sessions}
    pick = st.selectbox("Session", list(labels.keys()))
    session_no = labels[pick]
    sel = next(s for s in ss.sessions if s.number == session_no)

    with st.expander(f"Key takeaways for Session {sel.number} ({sel.key_takeaways_count})"):
        for k in sel.key_takeaways:
            st.write("•", k)

    use_judge = st.checkbox("Run the LLM quality judge (rubric /100)", value=True,
                            help="Uncheck for a faster, cheaper draft graded only by the deterministic gates.")

    if st.button("✨ Generate TR Doc", type="primary", disabled=not key_ok):
        logbox = st.empty()
        logs: list[str] = []

        def on_event(msg: str):
            logs.append(msg)
            logbox.code("\n".join(logs))

        try:
            with st.spinner(f"Generating Session {session_no}… this can take a couple of minutes."):
                result = pipeline.run(session_no, use_judge=use_judge,
                                      do_sync=False, on_event=on_event)
            ss.gen_result = result
        except Exception as e:
            st.error(f"Generation failed: {e}")

# --------------------------------------------------------------------------- #
# STEP 3 — result
# --------------------------------------------------------------------------- #
if ss.gen_result is not None:
    st.divider()
    st.header("3 · Result")
    result = ss.gen_result
    final = result["history"][-1]
    te = final["time"]

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Accepted", "✅ Yes" if final["accepted"] else "⚠️ Review")
    r2.metric("Est. recording", f"{te['estimated_minutes']} min", f"budget {te['max_minutes']}")
    r3.metric("Slides", te["slide_count"])
    if "judge" in final:
        r4.metric("Rubric score", f"{final['judge'].get('weighted_total', '-')}/100")

    if not final["accepted"]:
        st.warning("Below one or more gates — the best attempt is still available below.")
        for iss in final.get("issues", []):
            st.write("•", iss)

    docx_path = Path(result["docx"])
    if docx_path.exists():
        st.download_button(
            "⬇️ Download Word (.docx)",
            data=docx_path.read_bytes(),
            file_name=docx_path.name,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
        )

    md_path = docx_path.with_suffix(".md")
    if md_path.exists():
        with st.expander("👀 Preview the TR doc"):
            st.markdown(md_path.read_text(encoding="utf-8"))

    if "judge" in final:
        with st.expander("📊 Rubric breakdown"):
            for dim, obj in final["judge"].get("scores", {}).items():
                st.write(f"**{dim}** — {obj.get('score')}/5")
                st.caption(obj.get("justification", ""))
