import pandas as pd
import streamlit as st

import core
from retro_intake import ai

core.header("Executive summary",
            "One page for leadership: where the month stands, what is at risk, and the decisions needed. Built from "
            "the same data as every other page.")

month = st.select_slider("Month", options=list(core.MONTHS), value="2026-09", format_func=core.MONTHS.get)
prev = {"2026-08": "2026-07", "2026-09": "2026-08"}.get(month)
fs = core.q("SELECT * FROM mart_file_scorecard WHERE decision <> 'REJECTED'")
sc = core.q("SELECT * FROM mart_client_scorecard")


def stats(p):
    f = fs[fs.period == p]
    s = sc[sc.period == p]
    if f.empty:
        return None
    ok = f[f.decision == "GO"]
    return {"files": len(f), "on_time": 100 * ((f.decision != "MISSING") & (f.days_late == 0)).mean(),
            "loaded": len(ok), "records": int((ok.row_count - ok.critical_rows).sum()),
            "held_back": int(f.critical_rows.sum()), "score": s.quality_score.mean(),
            "held": int((f.decision == "HOLD").sum()), "missing": int((f.decision == "MISSING").sum())}


now, before = stats(month), (stats(prev) if prev else None)
d = (lambda k, fmt="{:+,.0f}": None if not before else fmt.format(now[k] - before[k]))
c = st.columns(5)
c[0].metric("Files loaded", f"{now['loaded']} of {now['files']}", d("loaded"))
c[1].metric("On time", f"{now['on_time']:.0f}%", d("on_time", "{:+.0f} pts"))
c[2].metric("Records loaded", f"{now['records']:,}", d("records"))
c[3].metric("Held back for correction", f"{now['held_back']:,}", d("held_back"), delta_color="inverse")
c[4].metric("Average quality score", f"{now['score']:.0f}", d("score", "{:+.1f}"))

# exposure not yet in production because an inforce file was held or is missing
exp = core.q("""SELECT f.cedant_id, f.file_name, d.decision_reason, r.currency, count(*) AS policies,
                       sum(i.face_amount * i.retro_share) AS ceded
                FROM stg_inforce i JOIN file_decision d USING (file_id) JOIN file_register f USING (file_id)
                JOIN ref_cedant r ON r.cedant_id = f.cedant_id
                WHERE d.decision = 'HOLD' AND f.period = ? AND i.status = 'ACTIVE' AND i.retro_share BETWEEN 0 AND 1
                GROUP BY ALL""", [month])
held = fs[(fs.period == month) & (fs.decision.isin(["HOLD", "MISSING"]))]
rec = core.q("SELECT * FROM mart_recurring_issues")

st.subheader("Headline")
lines = []
if now["held"] or now["missing"]:
    lines.append(f"{now['held']} held and {now['missing']} missing file(s) in {core.MONTHS[month]}; "
                 f"{now['loaded']} of {now['files']} files loaded and {now['records']:,} records are in production.")
else:
    lines.append(f"Every file for {core.MONTHS[month]} loaded; {now['records']:,} records are in production.")
for _, e in exp.iterrows():
    lines.append(f"{core.cname(e.cedant_id)}: {e.policies:,} active policies with {e.currency} {e.ceded:,.0f} of ceded "
                 f"amount are not in production until the client resends. Reason: {e.decision_reason}.")
if len(rec):
    lines.append(f"{len(rec)} issue(s) have repeated for two or more months and need a decision, not another fix.")
for ln in lines:
    st.markdown(f"- {ln}")

st.subheader("Decisions needed")
dec = []
for _, h in held.iterrows():
    who = core.cname(h.cedant_id)
    if h.decision == "MISSING":
        dec.append((f"Chase {who} for the missing {h.file_type} file", "Client Administration", "This week",
                    f"Overdue {int(h.days_late)} days"))
    elif "FL003" in str(h.decision_reason):
        dec.append((f"Agree {who}'s layout change, then update the mapping", "Data Integration + client", "Before resend",
                    "Client renamed a column without notice"))
    elif "FL00" in str(h.decision_reason):
        dec.append((f"Ask {who} to resend the {h.file_type} file with matching controls", "Data Integration", "5 business days",
                    h.decision_reason))
    else:
        dec.append((f"Ask {who} to fix and resend the {h.file_type} file", "Data Integration", "5 business days", h.decision_reason))
for _, r in rec.iterrows():
    dec.append((f"Agree a documented exception or fix at source: {core.cname(r.cedant_id)}, {r.rule_name.lower()}",
                "Data Integration manager", "Next monthly review", f"Seen in {r.months_seen} months"))
st.dataframe(pd.DataFrame(dec, columns=["decision", "owner", "by", "why"]), hide_index=True, width="stretch")

st.subheader("Risk register")
risks = []
for _, e in exp.iterrows():
    risks.append(("Held inforce delays valuation and billing", core.cname(e.cedant_id), "High", "Likely",
                  f"{e.currency} {e.ceded:,.0f} ceded not loaded", "Resend by fix-by date; escalate if missed"))
if (fs.days_late > 0).any():
    risks.append(("Late files compress the control window", ", ".join(core.cname(x) for x in fs[(fs.days_late > 0) & (fs.decision != "MISSING")].cedant_id.unique()),
                  "Medium", "Possible", "Less time to review before the 15th", "Raise timeliness at the client review"))
if (fs.decision == "MISSING").any():
    risks.append(("Missing claims file means unbooked claims", ", ".join(core.cname(x) for x in fs[fs.decision == "MISSING"].cedant_id.unique()),
                  "High", "Possible", "Claims and reserves understated", "Chase; two months missing triggers escalation"))
risks.append(("Client layout changes without notice", "All clients", "Medium", "Possible", "Files held; manual rework",
              "Layout check holds the file; ask clients for 30 days' notice in the data specification"))
risks.append(("Personal data in client files", "All clients", "High", "Unlikely", "Privacy breach",
              "Encryption, least privilege, masking outside production, retention schedule"))
st.dataframe(pd.DataFrame(risks, columns=["risk", "where", "impact", "likelihood", "consequence", "mitigation"]),
             hide_index=True, width="stretch")

st.subheader("AI-written narrative")
core.ai_mode_badge()
narr = (f"In {core.MONTHS[month]}, {now['loaded']} of {now['files']} client files loaded and {now['records']:,} records "
        f"reached production; {now['held_back']:,} records were held back for correction. "
        + (f"{now['held']} file(s) were held and {now['missing']} did not arrive. " if now["held"] or now["missing"] else "")
        + (f"Compared with {core.MONTHS[prev]}, the average quality score moved {now['score'] - before['score']:+.1f} points. " if before else "")
        + "The priority is to get held files resent before the billing cut-off and to settle recurring issues at their source.")
if ai.llm_available() and st.button("Ask Claude to write the narrative"):
    try:
        with st.spinner("Asking Claude..."):
            narr = ai.complete("Write a five-sentence monthly data integration update for a CIO. Plain, factual, no hype. "
                               "Use only the facts given.", "\n".join(lines) + "\nDecisions:\n" +
                               "\n".join(d_[0] for d_ in dec))
        core.log_ai("Executive narrative", "Claude", core.MONTHS[month], narr, "Shown for review")
    except Exception as e:
        st.warning(f"Claude was not reachable ({e}).")
st.info(narr)

md = (f"# Client data integration: {core.MONTHS[month]}\n\n" + "\n".join(f"- {ln}" for ln in lines) +
      "\n\n## Decisions needed\n" + "\n".join(f"- {a} ({b}, {c_}): {w}" for a, b, c_, w in dec) +
      "\n\n## Narrative\n" + narr + "\n\n_Synthetic data; portfolio project._\n")
st.download_button("Download this summary (.md)", md, file_name=f"data_integration_summary_{month}.md")
