import streamlit as st

import core

core.header("Every responsibility in the posting, and where this app does it",
            "The Data Integration Analyst posting lists four groups of responsibilities and a set of technical "
            "requirements. Each one below links to the page that shows it working.")

GROUPS = {
    "Data management and quality assurance": [
        ("Manage the intake, validation, transformation and loading of client insurance and reinsurance data",
         "views/intake.py", "File register, hashing, archive; mapping; 31 checks; gate and load"),
        ("Gather and analyse requirements for data integration projects",
         "views/mapping.py", "New-client onboarding: sample file, AI-suggested mapping, test run, sign-off"),
        ("Reconcile control and exception reports before committing data to production",
         "views/recon.py", "Counts and totals vs client controls; roll-forward; premium vs treaty rate"),
        ("Investigate and resolve data quality issues, discrepancies and exceptions",
         "views/exceptions.py", "Exception explorer, record history, case tracker"),
        ("Track, document, archive and maintain client files under policy and regulation",
         "views/governance.py", "Audit trail by hash and load batch, retention, access, classification"),
        ("Follow up on missing, incomplete or inaccurate submissions",
         "views/followup.py", "Drafted email per client and month, tone options, Excel report"),
        ("Develop and maintain data quality standards, validation rules and QC procedures",
         "views/rules.py", "Rule catalogue in SQL, rule workbench, runbook"),
    ],
    "Data analysis and reporting": [
        ("Analyse operational and client data for trends, anomalies, risks and opportunities",
         "views/scorecards.py", "Quality trends by client, timeliness, portfolio figures"),
        ("Create reports, dashboards and data quality metrics", "views/scorecards.py",
         "Quality score, Power BI-ready extracts"),
        ("Root cause analysis on recurring issues", "views/exceptions.py", "Recurring-issue view, AI triage with likely causes"),
        ("Ad hoc reporting and business analysis", "views/ask_data.py", "Ask a question in plain English, see the SQL and the answer"),
    ],
    "Process improvement and automation": [
        ("Streamline manual processes through automation", "views/home.py", "The whole run is one command; PowerShell intake script"),
        ("Design and implement data-related projects and system enhancements", "views/gate.py",
         "What-if thresholds before a change goes live"),
        ("Develop and maintain ETL processes and integration workflows", "views/mapping.py",
         "Config-driven mapping for four layouts: CSV, pipe, Excel, fixed-width"),
        ("Recommend improvements to data management, controls and reporting", "views/ask_runbook.py",
         "Runbook with procedures, searchable by question"),
    ],
    "Collaboration and stakeholder support": [
        ("Work with clients, vendors and internal departments", "views/followup.py", "Clear, specific client communication"),
        ("Support strategic initiatives and data governance", "views/governance.py", "Data dictionary, lineage, AI use policy"),
        ("Testing, validation and user acceptance for system changes", "views/testing.py",
         "Automated tests, planted-problem score, change-impact comparison, UAT checklist"),
        ("Subject matter expertise on data quality and controls", "views/rules.py", "Every rule explained with its SQL"),
    ],
}
for group, items in GROUPS.items():
    st.subheader(group)
    for resp, page, how in items:
        c1, c2 = st.columns([3, 2])
        c1.markdown(f"**{resp}**  \n<span class='note'>{how}</span>", unsafe_allow_html=True)
        with c2:
            st.page_link(page, label="Open the page", icon=":material/arrow_forward:")

st.subheader("Technical requirements")
st.dataframe({
    "Requirement": ["SSIS / Azure Data Factory / Fabric pipelines", "Advanced SQL, stored procedures, tuning",
                    "Python, PowerShell, scripting, API integration", "Data governance and quality frameworks",
                    "Power BI", "Insurance or reinsurance knowledge (asset)"],
    "In this project": ["Same pattern in Python and DuckDB (stage, validate, gate, promote); the author's ADF, SSIS and "
                        "Fabric experience is from Two Rivers", "23 set-based SQL rules; T-SQL procedures in sql/tsql with "
                        "transactions and TRY/CATCH (written, not run against SQL Server)",
                        "Python package; Invoke-FileIntake.ps1 with Pester tests; Claude API call for AI features",
                        "Dimensions, severities, data dictionary, classification, retention, lineage",
                        "Star-schema CSV extracts ready for a Power BI model",
                        "Seriatim inforce, billing and claims; retro share; treaty limits; roll-forward"],
}, hide_index=True, width="stretch")
