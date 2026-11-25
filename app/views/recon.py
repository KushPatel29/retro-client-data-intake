import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import core

core.header("4. Reconciliation",
            "Nothing reaches production until it agrees: with the client's own control totals, with last month's "
            "inforce, and across files, with billing checked against the treaty rate and claims against the ceded amount.")

t1, t2, t3, t4 = st.tabs(["Control totals", "Inforce roll-forward", "Premium vs treaty rate", "Claims vs ceded amount"])

with t1:
    ct = core.q("""SELECT cedant_id, period, file_type, file_name, row_count, control_count,
                          row_count - control_count AS count_diff, amount_total, control_total,
                          round(amount_total - control_total, 2) AS amount_diff
                   FROM file_register WHERE status = 'RECEIVED' ORDER BY cedant_id, period, file_type""")
    bad = ct[(ct.count_diff != 0) | (ct.amount_diff.abs() > 0.01)]
    core.kpis([(len(ct), "files reconciled to client controls"), (len(bad), "do not agree", True),
               (f"{ct.amount_total.sum():,.0f}", "total amount checked (mixed currencies)")])
    st.dataframe(bad if len(bad) else ct.head(0), hide_index=True, width="stretch")
    with st.expander("All files"):
        st.dataframe(ct, hide_index=True, width="stretch")
    st.caption("Amount is face amount for inforce, premium for billing and claim amount for claims. A mismatch holds the whole file.")

with t2:
    rf = core.q("SELECT * FROM mart_rollforward")
    if rf.empty:
        st.info("Roll-forward needs two consecutive months.")
    else:
        a, b = st.columns(2)
        cid = a.selectbox("Client", rf.cedant_id.unique(), format_func=core.cname)
        per = b.selectbox("Month", rf[rf.cedant_id == cid].period, format_func=core.MONTHS.get, index=0)
        r = rf[(rf.cedant_id == cid) & (rf.period == per)].iloc[0]
        steps = [("Opening active", r.opening_active, "absolute"), ("Lapsed", -r.lapsed, "relative"),
                 ("Claims", -r.claims_terminations, "relative"), ("Dropped, no termination", -r.disappeared, "relative"),
                 ("New business", r.new_business, "relative"), ("Late-reported", r.late_reported, "relative"),
                 ("Closing active", r.closing_active, "total")]
        fig = go.Figure(go.Waterfall(x=[s[0] for s in steps], y=[s[1] for s in steps], measure=[s[2] for s in steps],
                                     text=[f"{abs(s[1]):,}" for s in steps], textposition="outside",
                                     decreasing=dict(marker_color=core.MAG), increasing=dict(marker_color=core.NAVY2),
                                     totals=dict(marker_color=core.NAVY), connector=dict(line=dict(color="#c9cfdc"))))
        lo = min(r.opening_active, r.closing_active) - r.lapsed - r.claims_terminations - r.disappeared - 20
        fig.update_layout(height=420, yaxis=dict(range=[lo, max(r.opening_active, r.closing_active) + r.new_business + 30]),
                          title=f"{core.cname(cid)}, {core.MONTHS[per]}: active policies")
        st.plotly_chart(fig, width="stretch")
        calc = r.opening_active - r.lapsed - r.claims_terminations - r.disappeared + r.new_business + r.late_reported
        (st.success if calc == r.closing_active else st.error)(
            f"Balances: {r.opening_active:,} − {r.lapsed} − {r.claims_terminations} − {r.disappeared} + {r.new_business} + "
            f"{r.late_reported} = {calc:,}, and the file has {r.closing_active:,} active.")
        if r.disappeared or r.late_reported:
            st.warning("Policies that dropped out with no termination, or appeared late, are listed as roll-forward "
                       "warnings (RF001, RF003) and go to the client to explain.")
        st.dataframe(rf.assign(client=rf.cedant_id.map(core.cname)), hide_index=True, width="stretch")

with t3:
    sc = core.q("""
        WITH x AS (
          SELECT b.cedant_id, b.period, b.record_key, b.premium,
                 round(i.face_amount * i.retro_share / 1000 * r.rate_base
                       * exp(r.rate_slope * (alb(i.insured_dob, i.period_end) - 30))
                       * CASE WHEN i.smoker = 'S' THEN r.rate_smoker ELSE 1 END
                       * CASE WHEN i.sex = 'F' THEN r.rate_female ELSE 1 END / 12, 2) AS expected
          FROM stg_billing b
          JOIN stg_inforce i ON i.cedant_id = b.cedant_id AND i.period = b.period
                            AND i.policy_id = b.policy_id AND i.life_id = b.life_id
          JOIN ref_cedant r ON r.cedant_id = b.cedant_id
          WHERE b.premium > 0 AND b.transaction_type <> 'REVERSAL' AND i.smoker IS NOT NULL AND i.insured_dob IS NOT NULL)
        SELECT x.*, EXISTS (SELECT 1 FROM exceptions e WHERE e.rule_id = 'BL005' AND e.cedant_id = x.cedant_id
                            AND e.period = x.period AND e.record_key = x.record_key) AS flagged
        FROM x WHERE expected > 0""")
    if len(sc) > 6000:
        sc = pd.concat([sc[sc.flagged], sc[~sc.flagged].sample(6000, random_state=1)])
    sc["result"] = sc.flagged.map({True: "Flagged: more than 25% off", False: "Within 25%"})
    fig = px.scatter(sc, x="expected", y="premium", color="result", log_x=True, log_y=True, opacity=0.7,
                     hover_data=["cedant_id", "period", "record_key"],
                     color_discrete_map={"Within 25%": core.NAVY2, "Flagged: more than 25% off": core.MAG})
    m = [sc.expected.min(), sc.expected.max()]
    fig.add_trace(go.Scatter(x=m, y=m, mode="lines", line=dict(color=core.SLATE, dash="dot"), name="billed = treaty rate"))
    fig.update_traces(marker=dict(size=5), selector=dict(mode="markers"))
    fig.update_traces(marker=dict(size=11), selector=dict(name="Flagged: more than 25% off"))
    fig.update_layout(height=460, xaxis_title="monthly premium from the treaty rate", yaxis_title="premium billed by the client",
                      legend_title=None, title="Every billed premium against the treaty rate (log scale)")
    st.plotly_chart(fig, width="stretch")
    st.caption("Expected premium = face × retro share ÷ 1,000 × the treaty's synthetic yearly renewable term rate at "
               "attained age, × 2 for smokers, × 0.85 for women, ÷ 12. Sample of up to 6,000 points; every flagged one is shown.")

with t4:
    cl = core.q("""SELECT c.cedant_id, c.period, c.claim_id, c.claim_type, c.claim_amount,
                          i.face_amount * i.retro_share AS ceded_amount, i.status
                   FROM stg_claims c LEFT JOIN stg_inforce i ON i.cedant_id = c.cedant_id AND i.period = c.period
                    AND i.policy_id = c.policy_id AND i.life_id = c.life_id ORDER BY c.claim_amount DESC""")
    cl["check"] = cl.apply(lambda r: "No matching policy this month" if r.ceded_amount != r.ceded_amount else
                           ("Above ceded amount" if r.claim_amount > r.ceded_amount * 1.01 else "Agrees"), axis=1)
    fig = px.bar(cl, x="claim_id", y=["claim_amount", "ceded_amount"], barmode="group",
                 color_discrete_sequence=[core.MAG, core.NAVY], title="Each claim against the amount ceded on that life")
    fig.update_layout(height=380, xaxis_title=None, yaxis_title=None, legend_title=None, xaxis=dict(showticklabels=False))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(cl, hide_index=True, width="stretch")
