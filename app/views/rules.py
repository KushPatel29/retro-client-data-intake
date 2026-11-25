import pandas as pd
import plotly.express as px
import streamlit as st

import core
from retro_intake import safesql
from retro_intake.validate import FILE_RULES

core.header("3. Validation rules",
            "Twenty-three record-level rules, each one SQL query that returns the failing records, plus eight checks "
            "on whole files. Critical rules hold records back; warnings load them and list them for review.")

rules = core.rules()
counts = core.q("SELECT rule_id, count(*) AS n, count(DISTINCT cedant_id) AS clients FROM exceptions GROUP BY 1")
cat = pd.DataFrame([{"rule": r["id"], "check": r["name"], "file": r["file_type"], "severity": r["severity"],
                     "dimension": r["dimension"]} for r in rules] +
                   [{"rule": k, "check": v[0], "file": "any", "severity": v[1], "dimension": v[2]} for k, v in FILE_RULES.items()])
cat = cat.merge(counts.rename(columns={"rule_id": "rule"}), how="left").fillna({"n": 0, "clients": 0})
cat["n"] = cat.n.astype(int)

c1, c2 = st.columns([3, 2])
with c1:
    f1, f2, f3 = st.columns(3)
    ft = f1.multiselect("File", ["inforce", "billing", "claims", "any"])
    sv = f2.multiselect("Severity", ["CRITICAL", "WARNING"])
    dm = f3.multiselect("Dimension", sorted(cat.dimension.unique()))
    view = core.filt(cat, file=ft, severity=sv, dimension=dm)
    pick = st.dataframe(view.rename(columns={"n": "found"}), hide_index=True, width="stretch", height=420,
                        on_select="rerun", selection_mode="single-row", key="ruletable")
with c2:
    dim = cat.groupby(["dimension", "severity"], as_index=False).n.sum()
    fig = px.bar(dim, y="dimension", x="n", color="severity", orientation="h",
                 color_discrete_map={"CRITICAL": core.MAG, "WARNING": core.NAVY2}, title="Exceptions by quality dimension")
    fig.update_layout(height=420, yaxis_title=None, xaxis_title="exceptions", legend_title=None,
                      yaxis=dict(categoryorder="total ascending"))
    st.plotly_chart(fig, width="stretch")

rows = pick.selection.rows if pick and pick.selection else []
rid = view.iloc[rows[0]].rule if rows else "BL005"
rule = next((r for r in rules if r["id"] == rid), None)
st.subheader(f"{rid}: {cat.set_index('rule').loc[rid, 'check']}")
if rule is None:
    st.write("A file-level check, run in Python during intake and mapping (see `pipeline.py`).")
else:
    st.caption(f"{rule['severity'].title()} · {rule['dimension']} · {rule['file_type']} files"
               + (f" · skipped when a file arrives without {', '.join(rule['requires'])}" if rule.get("requires") else ""))
    st.code(rule["sql"], language="sql")
found = core.q("""SELECT cedant_id, period, record_key, detail FROM exceptions WHERE rule_id = ?
                  ORDER BY cedant_id, period LIMIT 200""", [rid])
st.dataframe(found, hide_index=True, width="stretch", height=min(38 * (len(found) + 1), 280))

st.subheader("Rule workbench")
st.write("Draft a new rule or tune an existing one. The query runs against this month's staged data on a read-only "
         "connection and shows the records it would flag, before anything is added to the catalogue.")
EXAMPLE = """-- Draft rule: smoker rate looks wrong for a client (more than 25% smokers is unusual)
SELECT cedant_id, period, count(*) AS lives,
       round(100.0 * count(*) FILTER (WHERE smoker = 'S') / count(*), 1) AS smoker_pct
FROM stg_inforce
GROUP BY ALL
ORDER BY smoker_pct DESC"""
start = st.radio("Start from", ["Selected rule", "A new draft"], horizontal=True)
sql = st.text_area("SQL", value=(rule["sql"] if (rule and start == "Selected rule") else EXAMPLE), height=230)
if st.button("Run on staged data", type="primary"):
    con = core.safe_con()
    try:
        out = safesql.run(con, sql)
        st.success(f"{len(out)} rows (limit {safesql.MAX_ROWS}).")
        st.dataframe(out, hide_index=True, width="stretch")
    except safesql.Refused as e:
        st.error(f"Refused: {e}")
    except Exception as e:
        st.error(f"The query failed: {e}")
    finally:
        con.close()
with st.expander("Tables you can query"):
    con = core.safe_con()
    st.code(safesql.schema(con), language=None)
    con.close()
core.guardrails(["Read-only connection; file and network access from SQL are switched off.",
                 "One SELECT statement at a time; results capped at 500 rows.",
                 "A new rule goes live only after a regression run on the last three months (see Testing and UAT)."])
