import subprocess
import sys

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

import core
from retro_intake.validate import decision_sql

res = core.current()
sc = res["score"]
core.header("Testing and UAT",
            "Every run is scored against an answer key: the generator plants known problems and records them, and the "
            "checks must find every one without flagging clean data. Before any rule change, compare its impact.")

core.kpis([(sc["planted"], "problems planted"), (sc["caught"], "caught"), (len(sc["missed"]), "missed", bool(sc["missed"])),
           (len(sc["false_alarms"]), "false alarms", bool(sc["false_alarms"]))])
pr = sc["per_rule"]
if len(pr):
    pr = pr.merge(pd.DataFrame([{"rule_id": r["id"], "name": r["name"]} for r in core.rules()]), how="left")
    pr["name"] = pr["name"].fillna(pr.rule_id)
    pr["missed"] = pr.planted - pr.caught
    fig = px.bar(pr.melt(id_vars=["rule_id", "name"], value_vars=["caught", "missed"]), y="rule_id", x="value", color="variable",
                 orientation="h", color_discrete_map={"caught": core.NAVY, "missed": core.MAG}, hover_data=["name"],
                 title="Planted vs caught, by check")
    fig.update_layout(height=640, yaxis=dict(categoryorder="category descending"), yaxis_title=None, xaxis_title=None,
                      legend_title=None)
    st.plotly_chart(fig, width="stretch")
else:
    st.info("The clean scenario plants nothing; the test is that nothing is flagged.")

st.subheader("Automated test suite")
st.write("50 pytest tests: detection on several seeds and profiles, no false alarms, every rule exercised, expected holds, no critical "
         "record in production, rows loaded + held back = rows received, roll-forward balances, layout mapping, AI helpers, SQL "
         "guardrails, and every app page. The button runs the 36 engine tests; the 14 page tests run in CI.")
if st.button("Run the engine tests now"):
    with st.spinner("Running pytest (about 30 seconds)..."):
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(core.ROOT / "tests"),
                            "--ignore", str(core.ROOT / "tests" / "test_app.py")],
                           capture_output=True, text=True, cwd=core.ROOT, timeout=300)
    (st.success if p.returncode == 0 else st.error)(p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "No output")
    st.code(p.stdout[-3000:], language=None)

st.subheader("Change impact: what happens if we change a rule?")
st.write("Switch rules off or change the hold limit and compare with today's decisions. This is the regression "
         "check the runbook asks for before any rule change.")
ids = sorted(core.q("SELECT DISTINCT rule_id FROM exceptions").rule_id)
a, b = st.columns([2, 1])
off = a.multiselect("Switch off", ids, help="Exceptions from these checks are ignored in the comparison.")
rate = b.number_input("Hold limit", 0.0, 0.2, float(core.config()["critical_rate_hold"]), 0.005, format="%.3f")
con = duckdb.connect()
con.execute(f"ATTACH '{core.db_path()}' AS w (READ_ONLY)")
excl = ", ".join(f"'{r}'" for r in off) or "''"
con.execute(f"CREATE VIEW exceptions AS SELECT * FROM w.exceptions WHERE rule_id NOT IN ({excl})")
con.execute("CREATE VIEW file_register AS SELECT * FROM w.file_register")
new = con.execute(f"SELECT file_id, decision AS new_decision, critical_rows AS new_held_back FROM ({decision_sql(rate, core.config()['hold_min_rows'])})").df()
old = con.execute("SELECT d.file_id, f.file_name, d.decision, d.critical_rows FROM w.file_decision d JOIN w.file_register f USING (file_id)").df()
lost = con.execute(f"SELECT rule_id, count(*) n FROM w.exceptions WHERE rule_id IN ({excl}) GROUP BY 1").df()
con.close()
diff = old.merge(new, on="file_id")
ch = diff[(diff.decision != diff.new_decision) | (diff.critical_rows != diff.new_held_back)]
k = st.columns(3)
k[0].metric("Exceptions no longer raised", int(lost.n.sum()) if len(lost) else 0)
k[1].metric("Files changing decision", int((diff.decision != diff.new_decision).sum()))
k[2].metric("Records held back", int(diff.new_held_back.sum()), delta=int(diff.new_held_back.sum() - diff.critical_rows.sum()),
            delta_color="inverse")
if len(ch):
    st.dataframe(ch, hide_index=True, width="stretch")
    planted_off = [k_ for k_ in sc["per_rule"].rule_id if k_ in off] if len(sc["per_rule"]) else []
    if planted_off:
        st.error(f"Switching off {', '.join(planted_off)} would let planted problems through. The regression fails.")

st.subheader("UAT sign-off for a release")
if "uat" not in st.session_state:
    st.session_state["uat"] = pd.DataFrame({"check": [
        "Regression suite passes (pytest and Pester)", "Every planted problem caught, no false alarms",
        "Change impact reviewed: every new or removed exception explained", "Rows loaded + held back = rows received",
        "Roll-forward balances for every client and month", "Follow-up drafts read by Client Administration",
        "Release scheduled for the start of a month, not during a control run"],
        "done": [False] * 7, "signed off by": [""] * 7})
st.session_state["uat"] = st.data_editor(st.session_state["uat"], hide_index=True, width="stretch", key="uat_ed",
                                         disabled=["check"])
done = int(st.session_state["uat"].done.sum())
st.progress(done / 7, text=f"{done} of 7 checks signed off")
