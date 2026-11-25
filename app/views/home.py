import plotly.graph_objects as go
import streamlit as st

import core
from retro_intake import ai

res = core.current()
t = res["summary"]
core.header("Retro client data intake: control room",
            "Every month, ceding companies send policy, premium and claims files in their own layouts. This control "
            "room shows what arrived, what passed the checks, what was held, and what each client must fix. "
            "Control run as of October 15, 2026, for July to September.")

files = core.q("SELECT * FROM mart_file_scorecard ORDER BY cedant_id, period, file_type")
files["status"] = files.apply(core.status_of, axis=1)
core.kpis([(f"{t['rows_loaded']:,}", "records loaded to production"),
           (f"{t['rows_held_back']:,}", "records held back for correction", True),
           (f"{t['files_hold']} + {t['files_missing']}", "files held or missing", True),
           (f"{t['files_go']} of {t['files_registered']}", "files loaded"),
           (f"{res['score']['caught']} / {res['score']['planted']}", "planted problems caught")])

st.subheader("Every file for every client and month")
st.caption("Each square is one file. Click a square to see what the checks found.")
board = files[files.decision != "REJECTED"].copy()
types = ["inforce", "billing", "claims"]
board["x"] = board.apply(lambda r: list(core.MONTHS).index(r.period) * 4 + types.index(r.file_type), axis=1)
clients = list(dict.fromkeys(board.cedant_id))
board["y"] = board.cedant_id.map(lambda c: len(clients) - clients.index(c))
board["label"] = board.apply(lambda r: "missing" if r.decision == "MISSING" else (
    "held" if r.decision == "HOLD" else (f"{int(r.critical_rows)} back" if r.critical_rows else "ok")), axis=1)
fig = go.Figure()
for status, color in core.STATUS_COLOR.items():
    d = board[board.status == status]
    if d.empty:
        continue
    fig.add_trace(go.Scatter(
        x=d.x, y=d.y, mode="markers+text", name=status, text=d.label, textposition="middle center",
        textfont=dict(color="white" if status in ("Loaded", "Held") else "#0b1f4b", size=11),
        marker=dict(symbol="square", size=54, color=color, line=dict(width=[3 if dl > 0 else 0 for dl in d.days_late],
                                                                      color=core.LATE)),
        customdata=d[["cedant_name", "period", "file_type", "file_name", "decision_reason", "row_count", "days_late"]],
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]} %{customdata[2]}: %{customdata[3]}<br>"
                      "%{customdata[4]}<br>%{customdata[5]:,} records, %{customdata[6]} days late<extra></extra>"))
for i, p in enumerate(core.MONTHS):
    fig.add_annotation(x=i * 4 + 1, y=0.12, yref="y", text=f"<b>{core.MONTHS[p]}</b>", showarrow=False,
                       font=dict(size=13, color="#0b1f4b"))
fig.update_layout(height=420, showlegend=True, legend=dict(orientation="h", y=-0.04, x=0.25),
                  xaxis=dict(tickvals=[i * 4 + j for i in range(3) for j in range(3)],
                             ticktext=[t for p in core.MONTHS for t in types], side="top",
                             showgrid=False, zeroline=False, range=[-0.8, 10.8]),
                  yaxis=dict(tickvals=[len(clients) - i for i in range(len(clients))],
                             ticktext=[core.cname(c) for c in clients], showgrid=False, zeroline=False,
                             range=[-0.2, len(clients) + 0.6]),
                  margin=dict(l=10, r=10, t=40, b=10), dragmode=False)
ev = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key="board",
                     config={"displayModeBar": False})
st.caption("An orange outline marks a file received after its due date.")

sel = None
if ev and ev.selection and ev.selection.points:
    p = ev.selection.points[0]
    hit = board[(board.x == p["x"]) & (board.y == p["y"])]
    if len(hit):
        sel = hit.iloc[0]
if sel is None:
    sel = board[board.decision == "HOLD"].iloc[0] if (board.decision == "HOLD").any() else board.iloc[0]

with st.container(border=True):
    st.markdown(f"#### {sel.cedant_name}, {core.MONTHS[sel.period]} {sel.file_type}: {sel.status.lower()}")
    st.write(f"`{sel.file_name}`. {sel.decision_reason}.")
    c = st.columns(5)
    c[0].metric("Records", f"{int(sel.row_count):,}")
    c[1].metric("Held back", int(sel.critical_rows or 0))
    c[2].metric("Warnings", int(sel.warning_rows or 0))
    c[3].metric("Controls agree", "n/a" if sel.decision == "MISSING" else ("Yes" if sel.controls_ok else "No"))
    c[4].metric("Days late" if sel.decision != "MISSING" else "Days overdue", int(sel.days_late or 0))
    exc = core.q("""SELECT severity, rule_name AS "check", coalesce(record_key, 'whole file') AS record, detail
                    FROM exceptions WHERE cedant_id = ? AND period = ? AND file_type = ?
                    ORDER BY severity, rule_id""", [sel.cedant_id, sel.period, sel.file_type])
    if len(exc):
        st.dataframe(exc, hide_index=True, width="stretch", height=min(38 * (len(exc) + 1), 300))
    else:
        st.success("No exceptions. Every record loaded.")

st.subheader("AI briefing for this month")
core.ai_mode_badge()
month = st.select_slider("Month", options=list(core.MONTHS), value="2026-09", format_func=core.MONTHS.get)
cols = st.columns(2)
for i, cid in enumerate(clients):
    exc = core.q("SELECT * FROM exceptions WHERE cedant_id = ? AND period = ?", [cid, month])
    fl = core.q("SELECT * FROM mart_file_scorecard WHERE cedant_id = ? AND period = ?", [cid, month])
    with cols[i % 2].container(border=True):
        st.markdown(f"**{core.cname(cid)}**")
        st.markdown("\n\n".join(ai.triage_offline(exc, fl)[:4]))
st.caption("Briefings come from the exception data and a knowledge base of known causes. Likely causes are "
           "suggestions to confirm with the client, not findings.")
