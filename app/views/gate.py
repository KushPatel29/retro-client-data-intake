import plotly.express as px
import streamlit as st

import core
from retro_intake.validate import decision_sql

core.header("7. Gate and load",
            "A file is held, and nothing from it loads, if a file-level control fails or if too many rows fail critical "
            "checks. Otherwise the clean rows load to production and only the failing ones are held back. Try the "
            "thresholds before changing them for real.")

cfg = core.config()
a, b = st.columns(2)
rate = a.slider("Hold a file when more than this share of rows fails a critical check", 0.0, 0.10,
                float(cfg["critical_rate_hold"]), 0.005, format="%.3f")
minrows = b.slider("...and the file has at least this many rows", 1, 1000, int(cfg["hold_min_rows"]), 1)

now = core.q("SELECT d.file_id, f.file_name, f.cedant_id, f.period, f.file_type, f.row_count, d.critical_rows, "
             "d.decision, d.decision_reason FROM file_decision d JOIN file_register f USING (file_id)")
whatif = core.q(f"SELECT file_id, decision AS new_decision, decision_reason AS new_reason FROM ({decision_sql(rate, minrows)})")
cmp_ = now.merge(whatif, on="file_id")
changed = cmp_[cmp_.decision != cmp_.new_decision]

def loaded(df, col):
    go = df[df[col] == "GO"]
    return int((go.row_count - go.critical_rows).sum())

k = st.columns(4)
k[0].metric("Files held now", int((cmp_.decision == "HOLD").sum()))
k[1].metric("Files held with these settings", int((cmp_.new_decision == "HOLD").sum()),
            delta=int((cmp_.new_decision == "HOLD").sum() - (cmp_.decision == "HOLD").sum()), delta_color="inverse")
k[2].metric("Records loaded now", f"{loaded(cmp_, 'decision'):,}")
k[3].metric("Records loaded with these settings", f"{loaded(cmp_, 'new_decision'):,}",
            delta=f"{loaded(cmp_, 'new_decision') - loaded(cmp_, 'decision'):+,}")
if len(changed):
    st.warning(f"{len(changed)} file(s) would change decision:")
    st.dataframe(changed[["file_name", "row_count", "critical_rows", "decision", "new_decision", "new_reason"]],
                 hide_index=True, width="stretch")
else:
    st.success("No file changes decision at these settings.")

rates = cmp_[cmp_.row_count > 0].assign(rate=lambda d: d.critical_rows / d.row_count)
fig = px.scatter(rates, x="row_count", y="rate", color="new_decision", log_x=True, hover_data=["file_name", "critical_rows"],
                 color_discrete_map={"GO": core.NAVY, "HOLD": core.MAG, "REJECTED": core.SLATE},
                 title="Share of rows failing critical checks, per file")
fig.add_hline(y=rate, line_dash="dot", line_color=core.MAG, annotation_text=f"hold above {rate:.1%}")
fig.add_vline(x=minrows, line_dash="dot", line_color=core.SLATE, annotation_text=f"min {minrows} rows")
fig.update_traces(marker=dict(size=12))
fig.update_layout(height=420, yaxis_tickformat=".1%", xaxis_title="rows in file", yaxis_title=None, legend_title=None)
st.plotly_chart(fig, width="stretch")
st.caption("Files held for a failed control total or layout change stay held at any threshold.")

st.subheader("Production tables after this run")
prod = core.q("""SELECT 'inforce' AS table_name, count(*) AS records, count(DISTINCT cedant_id || period) AS client_months,
                        any_value(load_batch) AS load_batch FROM prod_inforce
                 UNION ALL SELECT 'billing', count(*), count(DISTINCT cedant_id || period), any_value(load_batch) FROM prod_billing
                 UNION ALL SELECT 'claims', count(*), count(DISTINCT cedant_id || period), any_value(load_batch) FROM prod_claims""")
st.dataframe(prod, hide_index=True, width="stretch")

st.subheader("How data moves")
st.graphviz_chart("""
digraph G { rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef1f7", color="#0b1f4b", fontname="Helvetica", fontsize=11];
  edge [color="#5a6680"];
  inbox [label="Client files\\n(SFTP inbox)"]; reg [label="file_register\\nhash, arrival, due"]; arch [label="archive\\nclient / month"];
  map [label="mapping\\nclients.yaml"]; stg [label="stg_inforce\\nstg_billing\\nstg_claims"]; rules [label="31 checks\\nrules.yaml"];
  exc [label="exceptions"]; gate [label="gate\\nGO / HOLD"]; prod [label="prod_* tables\\nload_batch", fillcolor="#0b1f4b", fontcolor="white"];
  marts [label="marts\\nscorecards, roll-forward"]; out [label="Excel reports\\nfollow-up drafts\\nPower BI extracts"];
  inbox -> reg -> map -> stg -> rules -> exc -> gate; reg -> arch; stg -> gate -> prod -> marts -> out; exc -> out [style=dashed]; }
""")
