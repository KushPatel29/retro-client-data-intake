import pandas as pd
import plotly.express as px
import streamlit as st

import core

core.header("Trends and anomalies",
            "Checks catch bad records. This page catches a bad month: each client's book is compared with its own "
            "last month, so a sudden jump in lapses, a swing in the smoker mix or a drop in volume gets questioned "
            "before it reaches valuation.")

m = core.q("""
    WITH i AS (
      SELECT cedant_id, period, count(*) AS records,
             count(*) FILTER (WHERE status = 'ACTIVE') AS active,
             count(*) FILTER (WHERE status = 'LAPSED') AS lapsed,
             count(*) FILTER (WHERE status IN ('DEATH', 'CI_PAID')) AS claim_terms,
             sum(face_amount) FILTER (WHERE status = 'ACTIVE' AND face_amount > 0) AS face,
             avg(face_amount) FILTER (WHERE status = 'ACTIVE' AND face_amount > 0) AS avg_face,
             avg(issue_age) FILTER (WHERE status = 'ACTIVE') AS avg_issue_age,
             100.0 * count(*) FILTER (WHERE smoker = 'S') / nullif(count(*) FILTER (WHERE smoker IS NOT NULL), 0) AS smoker_pct,
             100.0 * count(*) FILTER (WHERE sex = 'F') / nullif(count(*) FILTER (WHERE sex IS NOT NULL), 0) AS female_pct
      FROM stg_inforce GROUP BY 1, 2),
    b AS (SELECT cedant_id, period, sum(premium) AS premium FROM stg_billing GROUP BY 1, 2),
    c AS (SELECT cedant_id, period, count(*) AS claims, sum(claim_amount) AS claim_amount FROM stg_claims GROUP BY 1, 2)
    SELECT i.*, 100.0 * i.lapsed / nullif(i.records, 0) AS lapse_pct, b.premium, c.claims, c.claim_amount
    FROM i LEFT JOIN b USING (cedant_id, period) LEFT JOIN c USING (cedant_id, period)
    ORDER BY 1, 2""")
m["client"] = m.cedant_id.map(core.cname)
st.caption("Built from everything received, including held files, because a bad month is easier to see before it is fixed.")

METRICS = {"active": ("Active policies", 0.03, "pct"), "face": ("Face amount in force", 0.03, "pct"),
           "avg_face": ("Average face amount", 0.05, "pct"), "lapse_pct": ("Lapse rate %", 0.5, "pts"),
           "smoker_pct": ("Smoker mix %", 1.0, "pts"), "female_pct": ("Female mix %", 1.0, "pts"),
           "avg_issue_age": ("Average issue age", 0.5, "abs"), "premium": ("Premium billed", 0.03, "pct")}
sens = st.slider("Sensitivity (lower flags more)", 0.5, 3.0, 1.0, 0.25,
                 help="Multiplies each metric's tolerance: 3% for volumes and amounts, 5% for average face, 0.5 points "
                      "for lapse rate, 1 point for mix, half a year for average issue age.")
rows = []
for cid, g in m.groupby("cedant_id"):
    g = g.sort_values("period").reset_index(drop=True)
    for i in range(1, len(g)):
        for k, (label, tol, kind) in METRICS.items():
            a, b = g.loc[i - 1, k], g.loc[i, k]
            if pd.isna(a) or pd.isna(b) or a == 0:
                continue
            change = (b - a) / a if kind == "pct" else (b - a)
            if abs(change) > tol * sens:
                rows.append({"client": core.cname(cid), "month": core.MONTHS[g.loc[i, "period"]], "metric": label,
                             "last month": a, "this month": b,
                             "change": f"{change:+.1%}" if kind == "pct" else f"{change:+.2f}",
                             "question for the client": {
                                 "active": "Were policies added or dropped in bulk? Is the extract complete?",
                                 "face": "Any large new cases, increases or reductions not flagged as changes?",
                                 "avg_face": "Has the mix of business shifted, or are amounts in a different unit?",
                                 "lapse_pct": "Was there a lapse sweep, a conversion, or a block that moved?",
                                 "smoker_pct": "Did the smoker coding or the underwriting basis change?",
                                 "female_pct": "Did the sex coding change?",
                                 "avg_issue_age": "Did a block of older or younger business come in?",
                                 "premium": "Rate change, catch-up premium, or reversals?"}[k]})
an = pd.DataFrame(rows)
k1, k2 = st.columns(2)
k1.metric("Month-over-month movements flagged", len(an))
k2.metric("Clients with at least one", an.client.nunique() if len(an) else 0)
if len(an):
    st.dataframe(an, hide_index=True, width="stretch",
                 column_config={"last month": st.column_config.NumberColumn(format="%,.2f"),
                                "this month": st.column_config.NumberColumn(format="%,.2f")})
else:
    st.success("No unusual month-over-month movement at this sensitivity.")

pick = st.selectbox("Metric to chart", list(METRICS), format_func=lambda k: METRICS[k][0], index=3)
m["month"] = m.period.map(core.MONTHS)
fig = px.line(m, x="month", y=pick, color="client", markers=True, title=METRICS[pick][0])
fig.update_layout(height=360, legend_title=None, yaxis_title=None, xaxis_title=None)
st.plotly_chart(fig, width="stretch")

st.subheader("Largest exposures")
st.write("Retrocession is about large risks. The biggest ceded amounts on single lives, with the treaty's jumbo limit.")
big = core.q("""SELECT i.cedant_id, i.period, i.policy_id, i.life_id, i.issue_age, i.face_amount,
                       i.face_amount * i.retro_share AS ceded, r.jumbo_limit, r.currency,
                       i.face_amount > r.jumbo_limit AS above_jumbo
                FROM stg_inforce i JOIN ref_cedant r USING (cedant_id)
                WHERE i.period = '2026-09' AND i.status = 'ACTIVE' AND i.retro_share BETWEEN 0 AND 1
                ORDER BY ceded DESC LIMIT 12""")
big["client"] = big.cedant_id.map(core.cname)
st.dataframe(big[["client", "policy_id", "issue_age", "currency", "face_amount", "ceded", "jumbo_limit", "above_jumbo"]],
             hide_index=True, width="stretch",
             column_config={c: st.column_config.NumberColumn(format="%,.0f") for c in ("face_amount", "ceded", "jumbo_limit")})

st.subheader("All metrics by client and month")
st.dataframe(m[["client", "period"] + [c for c in m.columns if c not in ("client", "period", "cedant_id", "month")]],
             hide_index=True, width="stretch",
             column_config={c: st.column_config.NumberColumn(format="%,.1f") for c in
                            ("avg_face", "avg_issue_age", "smoker_pct", "female_pct", "lapse_pct")})
