import json

import pandas as pd
import streamlit as st

import core

core.header("Architecture and Azure roadmap",
            "How this prototype would run on a Microsoft stack: SQL Server, Azure Data Factory or Fabric, Power BI and "
            "Azure OpenAI. Every piece below already exists in the repository as code or as a template.")

st.subheader("Target architecture")
st.graphviz_chart("""
digraph A { rankdir=LR; compound=true; node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, color="#0b1f4b", fillcolor="#eef1f7"];
  edge [color="#5a6680", fontname="Helvetica", fontsize=9];
  subgraph cluster_in { label="Clients"; style="rounded"; color="#c9cfdc"; sftp [label="SFTP / secure upload\\nper-client folders"]; }
  subgraph cluster_az { label="Azure (private network, managed identities)"; style="rounded"; color="#c9cfdc";
    adls [label="ADLS Gen2\\nraw + archive (immutable)"]; adf [label="Data Factory or Fabric pipeline\\npl_client_intake"];
    func [label="Azure Function\\nmap_and_stage (clients.yaml)"]; kv [label="Key Vault\\nsecrets"];
    aoai [label="Azure OpenAI\\nmapping, Q&A, drafts", fillcolor="#efe9ff", color="#4b2bb3"]; mon [label="Monitor + alerts\\nTeams"]; }
  subgraph cluster_sql { label="SQL Server / Azure SQL"; style="rounded"; color="#c9cfdc";
    stg [label="stg.*"]; dq [label="dq.* rules, exceptions,\\nusp_Validate / usp_GateAndPromote"]; prod [label="prod.*", fillcolor="#0b1f4b", fontcolor="white"]; }
  pbi [label="Power BI\\nscorecards, exec summary"]; app [label="Internal app\\n(.NET Blazor or Streamlit)"];
  sftp -> adls -> adf; adf -> func -> stg -> dq -> prod -> pbi; adf -> mon; func -> kv [style=dashed]; app -> dq; app -> aoai [style=dashed]; dq -> app; }
""")

st.subheader("What maps to what")
st.dataframe(pd.DataFrame([
    ("File register, hashing, archive", "intake.py, Invoke-FileIntake.ps1", "ADF Get Metadata + dq.usp_RegisterFile; ADLS immutable archive"),
    ("Per-client mapping", "mapping.py + clients.yaml", "Azure Function or Fabric notebook reading the same YAML"),
    ("Staging tables", "DuckDB stg_*", "SQL Server stg schema (sql/tsql/01_tables.sql)"),
    ("31 checks", "rules.yaml (SQL)", "dq.usp_ValidateInforce, set-based T-SQL (sql/tsql/02_...)"),
    ("Gate and load", "validate.decide / promote", "dq.usp_GateAndPromote with transaction and TRY/CATCH (sql/tsql/03_...)"),
    ("Orchestration", "pipeline.py", "adf/pl_client_intake.json: ForEach file, retries, failure alert"),
    ("Reporting", "Marts + Power BI CSV extracts", "Power BI semantic model on prod and dq; refresh at end of pipeline"),
    ("GenAI assistants", "ai.py (offline or Claude API)", "Azure OpenAI behind the same guardrails; no client records in prompts"),
    ("Tests", "pytest + Pester in GitHub Actions", "Azure DevOps or GitHub Actions; regression run before every release"),
], columns=["capability", "in this prototype", "on the Microsoft stack"]), hide_index=True, width="stretch")

t1, t2, t3, t4 = st.tabs(["ADF pipeline (template)", "SQL Server procedures", "PowerShell intake", "CI workflow"])
with t1:
    st.caption("A template written to match this pipeline; it has not been deployed to an Azure subscription.")
    st.code(json.dumps(json.loads((core.ROOT / "adf" / "pl_client_intake.json").read_text()), indent=2), language="json")
with t2:
    st.caption("Written for SQL Server 2019+ / Azure SQL to mirror the tested DuckDB logic; not run against SQL Server.")
    files = sorted((core.ROOT / "sql" / "tsql").glob("*.sql"))
    pick = st.selectbox("Script", [f.name for f in files], index=1)
    st.code((core.ROOT / "sql" / "tsql" / pick).read_text(), language="sql")
with t3:
    st.caption("Runs on Windows PowerShell 5.1 and PowerShell 7; its Pester tests pass in GitHub Actions on every push.")
    st.code((core.ROOT / "scripts" / "Invoke-FileIntake.ps1").read_text(), language="powershell")
with t4:
    st.code((core.ROOT / ".github" / "workflows" / "ci.yml").read_text(), language="yaml")

st.subheader("Security and AI controls")
st.dataframe(pd.DataFrame([
    ("Identity", "Managed identities for pipeline, function and SQL; no passwords in code; secrets in Key Vault"),
    ("Network", "Private endpoints for storage and SQL; SFTP limited to client IP ranges"),
    ("Data protection", "Encryption at rest and in transit; dynamic data masking on DOB and personal fields outside prod"),
    ("Traceability", "Immutable archive, file hash, load batch on every production record"),
    ("AI", "Azure OpenAI in the tenant; schema and aggregates only in prompts; read-only SQL; human approval; AI audit log"),
    ("Change", "Rules and mappings in version control; change impact and UAT before a month starts"),
], columns=["area", "control"]), hide_index=True, width="stretch")

st.subheader("Phased rollout")
st.dataframe(pd.DataFrame([
    ("Days 1-30", "Learn and baseline", "Run the current process alongside the team; document every client layout and quirk; "
     "measure today's time per file and error rates", "Client inventory, data specifications, baseline metrics"),
    ("Days 31-60", "Controls first", "Rule catalogue in SQL Server; control totals and roll-forward checks; file register; "
     "exception reports for the two largest clients", "Fewer files reaching production unchecked"),
    ("Days 61-90", "Automate and report", "Pipeline for the first clients; Power BI quality scorecard; follow-up templates "
     "agreed with Client Administration", "Monthly control run on a schedule, scorecard in front of leadership"),
    ("After 90 days", "Scale and assist", "Remaining clients onboarded by configuration; GenAI mapping assistant and "
     "Q&A behind approval steps", "New clients in days, not weeks"),
], columns=["when", "focus", "work", "outcome"]), hide_index=True, width="stretch")
