"""Outputs: data quality scorecard, roll-forward, Excel exception reports, client follow-up drafts,
Power BI extracts and the JSON that feeds the dashboard."""
from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .common import AS_OF, OUTPUT, PERIODS

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HOLD_FILL = PatternFill("solid", fgColor="F8D7DA")
WARN_FILL = PatternFill("solid", fgColor="FFF3CD")


def business_days_after(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def build_marts(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE mart_file_scorecard AS
        SELECT f.file_id, f.cedant_id, c.cedant_name, f.period, f.file_type, f.file_name,
               f.received_at, f.due_date, f.days_late, f.row_count, f.control_count,
               f.amount_total, f.control_total,
               d.critical_rows, d.warning_rows,
               CASE WHEN f.row_count > 0 THEN 1 - d.critical_rows / f.row_count END AS row_pass_rate,
               NOT EXISTS (SELECT 1 FROM exceptions e WHERE e.file_id = f.file_id
                           AND e.rule_id IN ('FL001', 'FL002', 'FL003')) AS controls_ok,
               d.decision, d.decision_reason
        FROM file_register f JOIN file_decision d USING (file_id) JOIN ref_cedant c USING (cedant_id)
        UNION ALL
        SELECT NULL, m.cedant_id, c.cedant_name, m.period, m.file_type, m.expected_name, NULL, m.due_date,
               m.days_overdue, 0, NULL, NULL, NULL, 0, 0, NULL, FALSE, 'MISSING', 'Not received'
        FROM missing_file m JOIN ref_cedant c USING (cedant_id)
    """)
    # Score per client and month, four equal parts: rows passing critical checks, files whose control
    # totals agree, files received on time, and files loaded (not held or missing).
    con.execute("""
        CREATE OR REPLACE TABLE mart_client_scorecard AS
        WITH s AS (
            SELECT * FROM mart_file_scorecard WHERE decision <> 'REJECTED')
        SELECT cedant_id, cedant_name, period,
               count(*) AS files_expected,
               count(*) FILTER (WHERE decision <> 'MISSING') AS files_received,
               count(*) FILTER (WHERE decision = 'MISSING') AS files_missing,
               count(*) FILTER (WHERE decision = 'HOLD') AS files_held,
               count(*) FILTER (WHERE days_late > 0 AND decision <> 'MISSING') AS files_late,
               sum(row_count) AS rows_received,
               sum(critical_rows) AS rows_held_back,
               sum(warning_rows) AS rows_with_warnings,
               round(100 * (
                   0.25 * coalesce(sum(row_count - critical_rows) / nullif(sum(row_count), 0), 0)
                 + 0.25 * avg(CASE WHEN controls_ok THEN 1 ELSE 0 END)
                 + 0.25 * avg(CASE WHEN decision <> 'MISSING' AND days_late = 0 THEN 1 ELSE 0 END)
                 + 0.25 * avg(CASE WHEN decision = 'GO' THEN 1 ELSE 0 END)), 1) AS quality_score
        FROM s GROUP BY ALL ORDER BY cedant_id, period
    """)
    # Month-over-month movement of the inforce block, from everything received (not just loaded)
    con.execute("""
        CREATE OR REPLACE TABLE mart_rollforward AS
        WITH k AS (SELECT DISTINCT cedant_id, period, period_start, policy_id, life_id, status, issue_date
                   FROM stg_inforce),
        pairs AS (SELECT DISTINCT c.cedant_id, c.period, c.period_start,
                         strftime(c.period_start - INTERVAL 1 DAY, '%Y-%m') AS prev_period
                  FROM k c WHERE EXISTS (SELECT 1 FROM k p WHERE p.cedant_id = c.cedant_id
                                         AND p.period = strftime(c.period_start - INTERVAL 1 DAY, '%Y-%m')))
        SELECT pr.cedant_id, pr.period,
          (SELECT count(*) FROM k p WHERE p.cedant_id = pr.cedant_id AND p.period = pr.prev_period
             AND p.status = 'ACTIVE') AS opening_active,
          (SELECT count(*) FROM k c WHERE c.cedant_id = pr.cedant_id AND c.period = pr.period
             AND c.status = 'LAPSED') AS lapsed,
          (SELECT count(*) FROM k c WHERE c.cedant_id = pr.cedant_id AND c.period = pr.period
             AND c.status IN ('DEATH', 'CI_PAID')) AS claims_terminations,
          (SELECT count(*) FROM k c WHERE c.cedant_id = pr.cedant_id AND c.period = pr.period
             AND c.issue_date >= pr.period_start) AS new_business,
          (SELECT count(*) FROM k c WHERE c.cedant_id = pr.cedant_id AND c.period = pr.period
             AND c.issue_date < pr.period_start
             AND NOT EXISTS (SELECT 1 FROM k p WHERE p.cedant_id = c.cedant_id AND p.period = pr.prev_period
                             AND p.policy_id = c.policy_id AND p.life_id = c.life_id)) AS late_reported,
          (SELECT count(*) FROM k p WHERE p.cedant_id = pr.cedant_id AND p.period = pr.prev_period
             AND p.status = 'ACTIVE'
             AND NOT EXISTS (SELECT 1 FROM k c WHERE c.cedant_id = p.cedant_id AND c.period = pr.period
                             AND c.policy_id = p.policy_id AND c.life_id = p.life_id)) AS disappeared,
          (SELECT count(*) FROM k c WHERE c.cedant_id = pr.cedant_id AND c.period = pr.period
             AND c.status = 'ACTIVE') AS closing_active
        FROM pairs pr ORDER BY 1, 2
    """)
    con.execute("""
        CREATE OR REPLACE TABLE mart_recurring_issues AS
        SELECT e.cedant_id, e.rule_id, r.rule_name, r.severity,
               count(DISTINCT e.period) AS months_seen, count(*) AS occurrences,
               string_agg(DISTINCT e.period, ', ' ORDER BY e.period) AS periods
        FROM exceptions e JOIN ref_rule r USING (rule_id)
        GROUP BY ALL HAVING count(DISTINCT e.period) >= 2
        ORDER BY months_seen DESC, occurrences DESC
    """)
    con.execute("""
        CREATE OR REPLACE TABLE mart_portfolio AS
        SELECT i.cedant_id, i.period, count(*) AS policies_loaded,
               sum(i.face_amount) AS face_amount, sum(i.face_amount * i.retro_share) AS ceded_amount,
               (SELECT coalesce(sum(premium), 0) FROM prod_billing b
                 WHERE b.cedant_id = i.cedant_id AND b.period = i.period) AS premium_loaded,
               (SELECT coalesce(sum(claim_amount), 0) FROM prod_claims c
                 WHERE c.cedant_id = i.cedant_id AND c.period = i.period) AS claims_loaded,
               any_value(r.currency) AS currency
        FROM prod_inforce i JOIN ref_cedant r USING (cedant_id)
        WHERE i.status = 'ACTIVE' GROUP BY 1, 2 ORDER BY 1, 2
    """)


def _autosize(ws, widths=None):
    for i, col in enumerate(ws.columns, start=1):
        w = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, w + 2), (widths or {}).get(i, 60))


def _sheet(wb, title, df):
    ws = wb.create_sheet(title)
    ws.append(list(df.columns))
    for row in df.itertuples(index=False):
        ws.append([None if pd.isna(v) else (v.to_pydatetime() if hasattr(v, "to_pydatetime") else v) for v in row])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _autosize(ws)
    return ws


def exception_workbooks(con, root=OUTPUT) -> list[str]:
    out = root / "exception_reports"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    pairs = con.execute("SELECT DISTINCT cedant_id, cedant_name, period FROM mart_file_scorecard ORDER BY 1, 3").fetchall()
    for cid, cname, period in pairs:
        files = con.execute("""SELECT file_type AS "File", file_name AS "File name", received_at AS "Received",
                                      days_late AS "Days late", row_count AS "Rows", critical_rows AS "Rows held back",
                                      warning_rows AS "Rows with warnings", decision AS "Decision",
                                      decision_reason AS "Reason"
                               FROM mart_file_scorecard WHERE cedant_id = ? AND period = ?
                               ORDER BY file_type""", [cid, period]).df()
        exc = con.execute("""SELECT e.file_type AS "File", e.severity AS "Severity", e.rule_id AS "Rule",
                                    e.rule_name AS "Check", e.record_key AS "Policy | life or claim",
                                    e.row_num AS "Row in file", e.detail AS "Detail"
                             FROM exceptions e WHERE e.cedant_id = ? AND e.period = ?
                             ORDER BY CASE e.severity WHEN 'CRITICAL' THEN 0 ELSE 1 END, e.file_type, e.rule_id,
                                      e.row_num""", [cid, period]).df()
        by_rule = (exc.groupby(["Severity", "Rule", "Check"]).size().reset_index(name="Count")
                   if len(exc) else pd.DataFrame(columns=["Severity", "Rule", "Check", "Count"]))
        wb = Workbook()
        wb.remove(wb.active)
        ws = _sheet(wb, "Files", files)
        for row in ws.iter_rows(min_row=2):
            if row[7].value in ("HOLD", "MISSING"):
                for c in row:
                    c.fill = HOLD_FILL
        _sheet(wb, "Checks summary", by_rule)
        ws = _sheet(wb, "Exceptions", exc)
        for row in ws.iter_rows(min_row=2):
            if row[1].value == "WARNING":
                row[1].fill = WARN_FILL
            elif row[1].value == "CRITICAL":
                row[1].fill = HOLD_FILL
        path = out / f"{cid}_{period}_exceptions.xlsx"
        wb.save(path)
        written.append(path.name)
    return written


def followups(con, cfg, root=OUTPUT) -> list[str]:
    out = root / "client_followups"
    out.mkdir(parents=True, exist_ok=True)
    reply_by = business_days_after(AS_OF, 5)
    written = []
    pairs = con.execute("SELECT DISTINCT cedant_id, cedant_name, period FROM mart_file_scorecard ORDER BY 1, 3").fetchall()
    for cid, cname, period in pairs:
        files = con.execute("SELECT * FROM mart_file_scorecard WHERE cedant_id = ? AND period = ? AND decision <> 'REJECTED'",
                            [cid, period]).df()
        crit = con.execute("""SELECT file_type, rule_id, rule_name, count(*) n,
                                     string_agg(record_key, ', ' ORDER BY record_key) FILTER (WHERE record_key IS NOT NULL) keys
                              FROM exceptions WHERE cedant_id = ? AND period = ? AND severity = 'CRITICAL'
                                AND rule_id NOT IN ('FL004')
                              GROUP BY 1, 2, 3 ORDER BY 1, 2""", [cid, period]).df()
        warn = con.execute("""SELECT file_type, rule_name, count(*) n FROM exceptions
                              WHERE cedant_id = ? AND period = ? AND severity = 'WARNING'
                                AND rule_id NOT IN ('FL005', 'FL006', 'FL007')
                              GROUP BY 1, 2 ORDER BY 1, 2""", [cid, period]).df()
        missing = files[files.decision == "MISSING"]
        held = files[files.decision == "HOLD"]
        if not len(missing) and not len(held) and not len(crit) and not len(warn):
            continue
        treaty = cfg["clients"][cid]["treaty_id"]
        lines = [f"**To:** Reinsurance Administration, {cname}",
                 f"**Subject:** {treaty}: {period} retro data, items to resolve by {reply_by:%B %d, %Y}", "",
                 "Hello,", "",
                 f"Thank you for your {period} submissions under treaty {treaty}. "
                 "We have processed them and need your help with the items below.", ""]
        n = 1
        if len(missing):
            lines.append(f"**{n}. Files not yet received**")
            for _, m in missing.iterrows():
                lines.append(f"- {m.file_type.title()} file ({m.file_name}), due {m.due_date:%B %d}.")
            lines.append("")
            n += 1
        if len(held):
            lines.append(f"**{n}. Files we could not load (please resend corrected files)**")
            for _, h in held.iterrows():
                lines.append(f"- {h.file_name}: {h.decision_reason}.")
                for _, e in con.execute("""SELECT detail FROM exceptions WHERE file_id = ? AND row_num IS NULL
                                           AND severity = 'CRITICAL'""", [h.file_id]).df().iterrows():
                    lines.append(f"  - {e.detail}")
            lines.append("")
            n += 1
        row_crit = crit[~crit.rule_id.str.startswith("FL")]
        if len(row_crit):
            lines.append(f"**{n}. Records held back until corrected**")
            lines.append("")
            lines.append("| File | Issue | Records | Policy \\| life or claim |")
            lines.append("| --- | --- | --- | --- |")
            for _, e in row_crit.iterrows():
                keys = (e["keys"] or "").split(", ")
                shown = ", ".join(keys[:8]) + (f" and {len(keys) - 8} more" if len(keys) > 8 else "")
                lines.append(f"| {e.file_type} | {e.rule_name} | {e.n} | {shown} |")
            lines.append("")
            n += 1
        if len(warn):
            lines.append(f"**{n}. For your review (loaded, but please confirm)**")
            for _, w in warn.iterrows():
                lines.append(f"- {w.file_type.title()}: {w.rule_name} ({w.n} records)")
            lines.append("")
        lines += ["The full list is in the attached exception report. "
                  f"Please send corrections, or let us know if anything above is expected, by {reply_by:%B %d}.", "",
                  "Kind regards,", "Data Integration, Life Retrocession", "",
                  "_Draft generated from synthetic data for a portfolio project. All names are fictional._"]
        path = out / f"{cid}_{period}_followup.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        written.append(path.name)
    return written


def powerbi_extracts(con, root=OUTPUT) -> None:
    out = root / "powerbi"
    out.mkdir(parents=True, exist_ok=True)
    tables = {"dim_cedant": "SELECT cedant_id, cedant_name, country, treaty_id, currency, file_format FROM ref_cedant",
              "dim_rule": "SELECT * FROM ref_rule",
              "fact_file": "SELECT * FROM mart_file_scorecard",
              "fact_exception": "SELECT * EXCLUDE (rule_name, severity, dimension) FROM exceptions",
              "fact_client_month": "SELECT * FROM mart_client_scorecard",
              "fact_rollforward": "SELECT * FROM mart_rollforward",
              "fact_portfolio": "SELECT * FROM mart_portfolio"}
    for name, sql in tables.items():
        con.execute(f"COPY ({sql}) TO '{out / (name + '.csv')}' (HEADER, DELIMITER ',')")


def dashboard_json(con, workbooks, drafts, root=OUTPUT) -> dict:
    def rows(sql):
        df = con.execute(sql).df()
        return json.loads(df.to_json(orient="records", date_format="iso"))

    data = {
        "as_of": AS_OF.isoformat(),
        "periods": list(PERIODS),
        "cedants": rows("SELECT cedant_id, cedant_name, country, treaty_id, currency, file_format FROM ref_cedant"),
        "files": rows("""SELECT cedant_id, period, file_type, file_name, received_at, days_late, row_count,
                                critical_rows, warning_rows, decision, decision_reason, amount_total, control_total,
                                control_count FROM mart_file_scorecard ORDER BY cedant_id, period, file_type"""),
        "client_scores": rows("SELECT * FROM mart_client_scorecard"),
        "rule_counts": rows("""SELECT r.rule_id, r.rule_name, r.file_type, r.severity, r.dimension,
                                      count(e.rule_id) AS exceptions,
                                      count(DISTINCT e.cedant_id) AS clients
                               FROM ref_rule r LEFT JOIN exceptions e USING (rule_id)
                               GROUP BY ALL ORDER BY r.rule_id"""),
        "exceptions": rows("""SELECT cedant_id, period, file_type, rule_id, rule_name, severity, row_num,
                                     record_key, detail FROM exceptions
                              ORDER BY cedant_id, period, CASE severity WHEN 'CRITICAL' THEN 0 ELSE 1 END, rule_id"""),
        "rollforward": rows("SELECT * FROM mart_rollforward"),
        "recurring": rows("SELECT * FROM mart_recurring_issues"),
        "portfolio": rows("SELECT * FROM mart_portfolio"),
        "totals": rows("""SELECT (SELECT count(*) FROM file_register) AS files_registered,
                                 (SELECT count(*) FROM missing_file) AS files_missing,
                                 (SELECT count(*) FROM file_decision WHERE decision = 'GO') AS files_go,
                                 (SELECT count(*) FROM file_decision WHERE decision = 'HOLD') AS files_hold,
                                 (SELECT count(*) FROM file_decision WHERE decision = 'REJECTED') AS files_rejected,
                                 (SELECT sum(row_count) FROM file_register) AS rows_received,
                                 (SELECT count(*) FROM prod_inforce) + (SELECT count(*) FROM prod_billing)
                                   + (SELECT count(*) FROM prod_claims) AS rows_loaded,
                                 (SELECT count(DISTINCT file_id || '-' || row_num) FROM exceptions
                                   WHERE severity = 'CRITICAL' AND row_num IS NOT NULL) AS rows_held_back,
                                 (SELECT count(*) FROM exceptions) AS exceptions,
                                 (SELECT count(*) FROM ref_rule) AS checks""")[0],
        "outputs": {"exception_reports": workbooks, "client_followups": drafts},
    }
    sample = root / "client_followups"
    data["followup_texts"] = {p.stem: p.read_text(encoding="utf-8") for p in sorted(sample.glob("*.md"))}
    (root / "dashboard_data.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    return data["totals"]


def build_all(con, cfg, root=OUTPUT) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    build_marts(con)
    workbooks = exception_workbooks(con, root)
    drafts = followups(con, cfg, root)
    powerbi_extracts(con, root)
    return dashboard_json(con, workbooks, drafts, root)
