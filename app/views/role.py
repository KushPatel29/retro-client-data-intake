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
         "views/mapping.py", "Client data specification (data contract) per client; new-client onboarding with test run"),
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
         "views/trends.py", "Month-over-month movement flags, largest exposures, client metrics"),
        ("Create reports, dashboards and data quality metrics", "views/scorecards.py",
         "Quality score, Power BI-ready extracts"),
        ("Root cause analysis on recurring issues", "views/exceptions.py", "Recurring-issue view, AI triage with likely causes"),
        ("Ad hoc reporting and business analysis", "views/ask_data.py", "Ask a question in plain English, see the SQL and the answer"),
    ],
    "Process improvement and automation": [
        ("Streamline manual processes through automation", "views/automation.py",
         "Measured step timings, manual vs automated, time-saved calculator"),
        ("Design and implement data-related projects and system enhancements", "views/architecture.py",
         "Target architecture on SQL Server, ADF/Fabric, Power BI; phased rollout; what-if gate"),
        ("Develop and maintain ETL processes and integration workflows", "views/mapping.py",
         "Config-driven mapping for four layouts: CSV, pipe, Excel, fixed-width"),
        ("Recommend improvements to data management, controls and reporting", "views/executive.py",
         "Decisions needed and risk register each month; searchable runbook"),
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

st.subheader("For leadership")
for label, page, how in [
        ("Monthly executive summary", "views/executive.py", "Where the month stands, exposure not yet loaded, decisions and risks"),
        ("How it would run on our stack", "views/architecture.py", "SQL Server, ADF or Fabric, Power BI, Azure OpenAI; security; rollout"),
        ("Is the AI any good, and is it safe?", "views/testing.py", "Measured accuracy on labelled test sets; guardrails; AI audit log")]:
    c1, c2 = st.columns([3, 2])
    c1.markdown(f"**{label}**  \n<span class='note'>{how}</span>", unsafe_allow_html=True)
    with c2:
        st.page_link(page, label="Open the page", icon=":material/arrow_forward:")

st.subheader("Technical requirements")
st.dataframe({
    "Requirement": ["SSIS / Azure Data Factory / Fabric pipelines", "Advanced SQL, stored procedures, tuning",
                    "Python, PowerShell, scripting, API integration", "Data governance and quality frameworks",
                    "Power BI", "Insurance or reinsurance knowledge (asset)"],
    "In this project": ["Same pattern in Python and DuckDB (stage, validate, gate, promote) plus an ADF pipeline template; "
                        "the author's ADF, SSIS and Fabric experience is from Two Rivers",
                        "23 set-based SQL rules with query plans; T-SQL tables and procedures in sql/tsql with "
                        "transactions and TRY/CATCH (written, not run against SQL Server)",
                        "Python package; Invoke-FileIntake.ps1 with Pester tests passing in CI; Claude API call for AI features",
                        "Dimensions, severities, data dictionary, classification, retention, lineage",
                        "Star-schema extracts and DAX measures for a Power BI model",
                        "Seriatim inforce, billing and claims; retro share; treaty register; jumbo exposures; roll-forward"],
}, hide_index=True, width="stretch")
