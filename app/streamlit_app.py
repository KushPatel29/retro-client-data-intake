"""Retro Client Data Intake: Streamlit app.  Run:  streamlit run app/streamlit_app.py"""
import streamlit as st

st.set_page_config(page_title="Retro Data Intake", page_icon=":material/fact_check:", layout="wide")

import core  # noqa: E402

P = "views/"
pages = {
    "Overview": [
        st.Page(P + "home.py", title="Control room", icon=":material/dashboard:", default=True),
        st.Page(P + "executive.py", title="Executive summary", icon=":material/summarize:"),
        st.Page(P + "role.py", title="Role coverage", icon=":material/checklist:"),
    ],
    "Monthly run": [
        st.Page(P + "intake.py", title="1. Intake and file tracking", icon=":material/inbox:"),
        st.Page(P + "mapping.py", title="2. Mapping and onboarding", icon=":material/swap_horiz:"),
        st.Page(P + "rules.py", title="3. Validation rules", icon=":material/rule:"),
        st.Page(P + "recon.py", title="4. Reconciliation", icon=":material/balance:"),
        st.Page(P + "exceptions.py", title="5. Exceptions and root cause", icon=":material/troubleshoot:"),
        st.Page(P + "followup.py", title="6. Client follow-up", icon=":material/mail:"),
        st.Page(P + "gate.py", title="7. Gate and load", icon=":material/rule_settings:"),
    ],
    "Reporting": [
        st.Page(P + "scorecards.py", title="Quality scorecards", icon=":material/monitoring:"),
        st.Page(P + "trends.py", title="Trends and anomalies", icon=":material/trending_up:"),
    ],
    "GenAI assistants": [
        st.Page(P + "ask_data.py", title="Ask the data", icon=":material/chat:"),
        st.Page(P + "ask_runbook.py", title="Ask the runbook", icon=":material/menu_book:"),
    ],
    "Platform": [
        st.Page(P + "architecture.py", title="Architecture and Azure roadmap", icon=":material/cloud:"),
        st.Page(P + "automation.py", title="Automation", icon=":material/bolt:"),
    ],
    "Controls and governance": [
        st.Page(P + "testing.py", title="Testing and UAT", icon=":material/science:"),
        st.Page(P + "governance.py", title="Data governance", icon=":material/shield:"),
    ],
}
nav = st.navigation(pages)

with st.sidebar:
    st.markdown("### Scenario")
    seed0, prof0 = core.scenario()
    profile = st.selectbox("Planted problems", ["baseline", "heavy", "clean"],
                           index=["baseline", "heavy", "clean"].index(prof0),
                           help="baseline: 156 planted problems; heavy: three times the record-level problems; "
                                "clean: no problems at all", key="sb_profile")
    seed = st.number_input("Random seed", value=int(seed0), step=1, help="A new seed builds a new book of policies.", key="sb_seed")
    if st.button("Generate and run", type="primary", width="stretch", key="sb_run"):
        st.session_state["scenario"] = (int(seed), profile)
        st.rerun()
    res = core.current()
    sc = res["score"]
    st.caption(f"{sc['caught']} of {sc['planted']} planted problems caught, {len(sc['false_alarms'])} false alarms.")
    st.divider()
    core.ai_mode_badge()
    st.caption("All data is synthetic. Every client, treaty and person is fictional. A portfolio project by Kush Patel.")

nav.run()
