from pathlib import Path

import streamlit as st

import core
from retro_intake import ai

res = core.current()
out = Path(res["out"])
core.header("6. Client follow-up",
            "One follow-up per client per month, drafted straight from the exceptions: missing files, held files with the "
            "reason, held-back records with policy numbers, and warnings to confirm. The Excel exception report goes with it.")

drafts = sorted((out / "client_followups").glob("*.md"))
if not drafts:
    st.success("Nothing to follow up: every file arrived on time and passed.")
    st.stop()
labels = {p.stem: f"{core.cname(p.stem[:3])}, {core.MONTHS[p.stem[4:11]]}" for p in drafts}
default = "C02_2026-09_followup" if "C02_2026-09_followup" in labels else list(labels)[0]
a, b = st.columns([2, 1])
pick = a.selectbox("Client and month", list(labels), index=list(labels).index(default), format_func=labels.get)
tone = b.radio("Tone", ai.TONES, horizontal=False)
text = (out / "client_followups" / f"{pick}.md").read_text(encoding="utf-8")

mode = "offline"
if ai.llm_available():
    mode = st.radio("Rewrite with", ["Built-in templates", "Claude"], horizontal=True)
key = (pick, tone, mode)
if key not in st.session_state.setdefault("tone_cache", {}):
    if mode == "Claude" and tone != "As drafted":
        try:
            with st.spinner("Claude is rewriting the email..."):
                st.session_state["tone_cache"][key] = ai.retone_llm(text, tone)
        except Exception as e:
            st.warning(f"Claude was not reachable ({e}); using the built-in template.")
            st.session_state["tone_cache"][key] = ai.retone_offline(text, tone)
    else:
        st.session_state["tone_cache"][key] = ai.retone_offline(text, tone)
email = st.session_state["tone_cache"][key]

c1, c2 = st.columns([3, 2])
with c1:
    with st.container(border=True):
        st.markdown(email)
    edited = st.text_area("Edit before sending", email, height=260)
with c2:
    cid, per = pick[:3], pick[4:11]
    st.markdown("**What's behind it**")
    fl = core.q("""SELECT file_type, decision, decision_reason, critical_rows, warning_rows FROM mart_file_scorecard
                   WHERE cedant_id = ? AND period = ? AND decision <> 'REJECTED'""", [cid, per])
    st.dataframe(fl, hide_index=True, width="stretch")
    xl = out / "exception_reports" / f"{cid}_{per}_exceptions.xlsx"
    st.download_button("Download the email (.md)", edited, file_name=f"{pick}.md", width="stretch")
    if xl.exists():
        st.download_button("Download the Excel exception report", xl.read_bytes(), file_name=xl.name,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    sent = st.session_state.setdefault("sent", set())
    if st.button("Mark as sent", type="primary", width="stretch", disabled=pick in sent):
        sent.add(pick)
        st.rerun()
    st.caption(f"Marked as sent this session: {len(sent)} of {len(labels)}.")
core.guardrails(["The draft only uses facts from the exception data; the AI rewrite must keep every file name, "
                 "policy number, count and date unchanged.",
                 "A person reads and edits every email before it goes out. Nothing is sent from this app.",
                 "Name the file and the field, not the person."])
