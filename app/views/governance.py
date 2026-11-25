import pandas as pd
import streamlit as st

import core

core.header("Data governance",
            "What each field means, how sensitive it is, where it comes from in each client's file, and how every loaded "
            "record traces back to the exact file it came from.")

cfg = core.config()
DICT = [
    ("policy_id", "Policy number as the client issues it", "Identifier", "Confidential"),
    ("life_id", "Life on the policy (1, 2 for joint lives)", "Identifier", "Internal"),
    ("insured_dob", "Insured's date of birth", "Personal", "Restricted: personal information"),
    ("sex", "Sex used for rating (M/F)", "Personal", "Restricted: personal information"),
    ("smoker", "Smoker class used for rating (S/N)", "Personal", "Restricted: health-related"),
    ("issue_date", "Policy issue (effective) date", "Policy", "Confidential"),
    ("issue_age", "Age at issue, age last birthday", "Policy", "Confidential"),
    ("face_amount", "Benefit amount on the life, treaty currency", "Financial", "Confidential"),
    ("retro_share", "Share of the risk ceded to us, 0 to 1", "Treaty", "Confidential"),
    ("status", "ACTIVE, LAPSED, DEATH or CI_PAID", "Policy", "Internal"),
    ("currency", "ISO currency code; must match the treaty", "Treaty", "Internal"),
    ("product", "Client's plan code", "Policy", "Internal"),
]
rows = []
for f, desc, group, cls in DICT:
    r = {"field": f, "meaning": desc, "group": group, "classification": cls}
    for cid, c in cfg["clients"].items():
        r[cid] = c["columns"]["inforce"].get(f, "")
    rows.append(r)
st.subheader("Data dictionary: inforce")
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
             column_config={cid: st.column_config.TextColumn(core.cname(cid)) for cid in cfg["clients"]})

st.subheader("Audit trail: from a production record back to its file")
pol = st.text_input("Policy number", "NBL0000002")
trail = core.q("""SELECT p.cedant_id, p.period, p.policy_id, p.life_id, p.face_amount, p.load_batch, p.file_id,
                         f.file_name, f.received_at, f.sha256
                  FROM prod_inforce p JOIN file_register f USING (file_id) WHERE p.policy_id = ? ORDER BY p.period""", [pol])
if len(trail):
    st.dataframe(trail, hide_index=True, width="stretch")
    st.caption("The SHA-256 proves the archived file is the one that was loaded. Archived copies are never edited.")
else:
    st.write("No production record with that policy number (it may have been held back or not exist).")

st.subheader("AI use register")
st.dataframe(pd.DataFrame([
    ("Mapping assistant", "Suggest how a new client layout maps", "Column names and sample values (Claude mode only)",
     "Person edits and test-runs before sign-off", "Low"),
    ("Ask the data", "Answer ad hoc questions with SQL", "Table and column names only", "SQL shown; read-only; row limit", "Low"),
    ("Ask the runbook", "Answer process questions", "Runbook passages only", "Cited passages; says when not covered", "Low"),
    ("Exception triage", "Suggest likely causes", "Exception counts only", "Labelled as unconfirmed", "Low"),
    ("Follow-up drafting", "Rewrite email tone", "The draft email (policy numbers, no personal data)",
     "Person edits and approves; nothing is sent from the app", "Medium"),
    ("Executive narrative", "Summarise the month", "Aggregated figures only", "Shown for review", "Low"),
], columns=["feature", "purpose", "data sent to a model", "human control", "risk"]), hide_index=True, width="stretch")

st.subheader("AI audit log (this session)")
log = st.session_state.get("ai_log", [])
if log:
    st.dataframe(pd.DataFrame(log), hide_index=True, width="stretch")
else:
    st.write("No AI actions yet in this session. Ask a question on Ask the data or Ask the runbook, test a mapping, "
             "or approve a follow-up, and each action is recorded here with what the person did with it.")

st.subheader("Controls")
st.dataframe(pd.DataFrame([
    ("Access", "Least privilege by role; personal fields masked outside production"),
    ("Encryption", "Client files and the warehouse encrypted at rest and in transit"),
    ("Retention", "Archived files kept for the retention period, then deleted with a deletion record"),
    ("Change control", "Rule and mapping changes go through change impact and UAT before the start of a month"),
    ("Traceability", "Every production record carries its source file id and load batch; files keep their hash"),
    ("AI use", "AI suggests, a person decides; no client records sent to an external model; SQL runs read-only"),
    ("Data quality", "Rule catalogue with severities by dimension; monthly quality score per client"),
], columns=["control", "how it is met here"]), hide_index=True, width="stretch")
st.caption("A sample policy framework for a portfolio project, not BHG Re's policies.")
