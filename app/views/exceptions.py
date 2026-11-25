import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import core
from retro_intake import ai

core.header("5. Exceptions and root cause",
            "Find what failed, see whether it keeps happening, look at a policy's full history, and track each issue "
            "until the client fixes it.")

exc = core.q("SELECT * FROM exceptions")
exc["client"] = exc.cedant_id.map(core.cname)
f1, f2, f3, f4 = st.columns(4)
cl = f1.multiselect("Client", sorted(exc.cedant_id.unique()), format_func=core.cname)
mo = f2.multiselect("Month", list(core.MONTHS), format_func=core.MONTHS.get)
ft = f3.multiselect("File", ["inforce", "billing", "claims"])
sv = f4.multiselect("Severity", ["CRITICAL", "WARNING"])
v = core.filt(exc, cedant_id=cl, period=mo, file_type=ft, severity=sv)

a, b = st.columns(2)
with a:
    par = v.groupby(["rule_name", "severity"], as_index=False).size().sort_values("size", ascending=False)
    par["cum"] = par["size"].cumsum() / max(par["size"].sum(), 1) * 100
    fig = go.Figure()
    fig.add_bar(x=par.rule_name, y=par["size"], marker_color=[core.MAG if s == "CRITICAL" else core.NAVY2 for s in par.severity],
                name="exceptions")
    fig.add_scatter(x=par.rule_name, y=par.cum, yaxis="y2", mode="lines+markers", line=dict(color=core.NAVY), name="cumulative %")
    fig.update_layout(title="Pareto: a few checks explain most exceptions", height=400, showlegend=False,
                      yaxis2=dict(overlaying="y", side="right", range=[0, 105], ticksuffix="%", showgrid=False),
                      xaxis=dict(tickangle=-40))
    st.plotly_chart(fig, width="stretch")
with b:
    v2 = v.assign(short=v.cedant_id)
    hm = v2.pivot_table(index="rule_name", columns="short", values="rule_id", aggfunc="count", fill_value=0)
    if len(hm):
        fig = go.Figure(go.Heatmap(z=hm.values, x=[core.cname(c).split()[0] for c in hm.columns], y=hm.index,
                                   text=[[str(x) if x else "" for x in row] for row in hm.values], texttemplate="%{text}",
                                   colorscale=[[0, "#ffffff"], [0.02, "#dde4f3"], [1, core.NAVY]], showscale=False,
                                   xgap=2, ygap=2, hovertemplate="%{y}<br>%{x}: %{z}<extra></extra>"))
        fig.update_layout(title="Where each check fires, by client", height=max(320, 26 * len(hm) + 90),
                          yaxis=dict(autorange="reversed"), xaxis=dict(side="top"), margin=dict(t=70))
        st.plotly_chart(fig, width="stretch")

st.subheader(f"{len(v)} exceptions")
st.dataframe(v[["client", "period", "file_type", "severity", "rule_id", "rule_name", "record_key", "detail"]],
             hide_index=True, width="stretch", height=300)

st.subheader("Recurring issues")
rec = core.q("SELECT * FROM mart_recurring_issues")
if len(rec):
    rec["client"] = rec.cedant_id.map(core.cname)
    st.dataframe(rec[["client", "rule_id", "rule_name", "severity", "months_seen", "occurrences", "periods"]],
                 hide_index=True, width="stretch")
    st.caption("The same policies flagged as jumbo or outside the age band every month are candidates for an agreed, "
               "documented exception, so the team stops re-reviewing them.")
else:
    st.write("No check failed for the same client in two or more months.")

st.subheader("Policy history")
key = st.text_input("Policy number (or policy|life)", value=v.record_key.dropna().iloc[0].split("|")[0] if len(v) else "")
if key:
    pid = key.split("|")[0].strip()
    hist = core.q("""SELECT period, file_id, row_num, status, insured_dob_raw AS dob_as_sent, issue_date_raw AS issue_as_sent,
                            issue_age, face_amount, retro_share, currency FROM stg_inforce WHERE policy_id = ? ORDER BY period""", [pid])
    bil = core.q("SELECT period, transaction_type, premium FROM stg_billing WHERE policy_id = ? ORDER BY period", [pid])
    ex = core.q("SELECT period, file_type, rule_name, severity, detail FROM exceptions WHERE record_key LIKE ? ORDER BY period", [pid + "|%"])
    c1, c2 = st.columns([3, 2])
    c1.markdown("**As reported in each month's inforce file**")
    c1.dataframe(hist, hide_index=True, width="stretch")
    c2.markdown("**Billing**")
    c2.dataframe(bil, hide_index=True, width="stretch")
    st.markdown("**Exceptions on this policy**")
    st.dataframe(ex, hide_index=True, width="stretch")

st.subheader("AI triage for a client and month")
core.ai_mode_badge()
t1, t2 = st.columns(2)
tc = t1.selectbox("Client", sorted(exc.cedant_id.unique()), format_func=core.cname, key="tc")
tm = t2.selectbox("Month", list(core.MONTHS), format_func=core.MONTHS.get, index=2, key="tm")
te = exc[(exc.cedant_id == tc) & (exc.period == tm)]
tf = core.q("SELECT * FROM mart_file_scorecard WHERE cedant_id = ? AND period = ?", [tc, tm])
with st.container(border=True):
    st.markdown("\n\n".join(ai.triage_offline(te, tf)))
if ai.llm_available() and st.button("Ask Claude for a root-cause write-up"):
    with st.spinner("Asking Claude..."):
        try:
            summary = te.groupby(["rule_id", "rule_name", "severity", "file_type"]).size().to_string()
            st.info(ai.complete("You are a reinsurance data quality analyst. From the exception counts, write a short "
                                "root-cause hypothesis and next steps. Label hypotheses as unconfirmed.",
                                f"Client {core.cname(tc)}, {tm}.\nFiles:\n{tf[['file_type','decision','decision_reason']].to_string()}"
                                f"\nExceptions:\n{summary}"))
        except Exception as e:
            st.warning(f"Claude was not reachable ({e}).")

st.subheader("Case tracker")
st.write("Track each open issue to resolution. Changes are kept for this session.")
if "cases" not in st.session_state:
    g = exc[exc.severity == "CRITICAL"].groupby(["cedant_id", "period", "rule_id", "rule_name"]).size().reset_index(name="records")
    g["client"] = g.cedant_id.map(core.cname)
    g["status"] = "Open"
    g["owner"] = ""
    g["note"] = ""
    st.session_state["cases"] = g[["client", "period", "rule_id", "rule_name", "records", "status", "owner", "note"]]
cases = st.data_editor(st.session_state["cases"], hide_index=True, width="stretch", key="case_editor",
                       column_config={"status": st.column_config.SelectboxColumn(
                           options=["Open", "Sent to client", "Client fixing", "Resolved", "Agreed exception"])},
                       disabled=["client", "period", "rule_id", "rule_name", "records"])
st.session_state["cases"] = cases
cnt = cases.status.value_counts()
st.caption(" · ".join(f"{k}: {v}" for k, v in cnt.items()))
