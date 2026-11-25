import io
import zipfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import core

res = core.current()
core.header("Quality scorecards",
            "A score for each client and month, built from four equal parts: rows passing critical checks, control "
            "totals agreeing, files on time, and files loaded. The parts show what to raise with each client.")

sc = core.q("SELECT * FROM mart_client_scorecard")
sc["month"] = sc.period.map(core.MONTHS)
fs = core.q("SELECT * FROM mart_file_scorecard WHERE decision <> 'REJECTED'")
rows = []
for (c_, p_), g in fs.groupby(["cedant_id", "period"]):
    vals = {"Rows passing": 100 * (g.row_count - g.critical_rows).sum() / max(g.row_count.sum(), 1),
            "Controls agree": 100 * g.controls_ok.astype(float).mean(),
            "On time": 100 * ((g.decision != "MISSING") & (g.days_late == 0)).mean(),
            "Loaded": 100 * (g.decision == "GO").mean()}
    rows += [{"cedant_id": c_, "period": p_, "part": k, "score": v} for k, v in vals.items()]
parts = pd.DataFrame(rows)

a, b = st.columns([3, 2])
with a:
    hm = sc.pivot(index="cedant_name", columns="month", values="quality_score")[list(core.MONTHS.values())]
    fig = px.imshow(hm, text_auto=".0f", aspect="auto", zmin=60, zmax=100,
                    color_continuous_scale=[core.MAG, "#f7c6d8", core.NAVY2, core.NAVY], title="Quality score by client and month")
    fig.update_layout(height=330, coloraxis_showscale=False, xaxis_title=None, yaxis_title=None)
    st.plotly_chart(fig, width="stretch")
with b:
    fig = px.line(sc, x="month", y="quality_score", color="cedant_name", markers=True, title="Trend")
    fig.update_layout(height=330, legend_title=None, yaxis_title=None, xaxis_title=None, legend=dict(orientation="h", y=-0.25),
                      yaxis_range=[50, 102])
    st.plotly_chart(fig, width="stretch")

c1, c2 = st.columns(2)
cid = c1.selectbox("Client", sc.cedant_id.unique(), format_func=core.cname)
per = c2.selectbox("Month", list(core.MONTHS), format_func=core.MONTHS.get, index=2)
p = parts[(parts.cedant_id == cid) & (parts.period == per)]
fig = px.bar(p, x="score", y="part", orientation="h", text=p.score.map(lambda v: f"{v:.0f}"), range_x=[0, 105],
             color="score", color_continuous_scale=[core.MAG, core.NAVY2, core.NAVY], range_color=[50, 100],
             title=f"{core.cname(cid)}, {core.MONTHS[per]}: what drives the score")
fig.update_layout(height=280, coloraxis_showscale=False, yaxis_title=None, xaxis_title=None)
st.plotly_chart(fig, width="stretch")

st.subheader("Loaded portfolio")
pf = core.q("SELECT * FROM mart_portfolio")
pf["client"] = pf.cedant_id.map(core.cname)
st.dataframe(pf[["client", "period", "currency", "policies_loaded", "face_amount", "ceded_amount", "premium_loaded", "claims_loaded"]],
             hide_index=True, width="stretch",
             column_config={c: st.column_config.NumberColumn(format="%,.0f") for c in ("face_amount", "ceded_amount",
                                                                                          "premium_loaded", "claims_loaded")})
st.caption("Production only: client-months whose inforce file was held are not in this table until the client resends. "
           "Amounts are in each treaty's own currency.")

st.subheader("Power BI extracts")
st.write("A small star schema, written as CSV on every run: `dim_cedant`, `dim_rule`, `fact_file`, `fact_exception`, "
         "`fact_client_month`, `fact_rollforward`, `fact_portfolio`. Relate the facts to the dimensions on `cedant_id` and "
         "`rule_id` and the scorecards above rebuild in Power BI.")
folder = Path(res["out"]) / "powerbi"
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(folder.glob("*.csv")):
        z.write(f, f.name)
st.download_button("Download the Power BI extracts (zip)", buf.getvalue(), file_name="retro_intake_powerbi.zip")
