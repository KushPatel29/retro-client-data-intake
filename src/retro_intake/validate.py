"""Run the rule catalogue and the file-level checks, then decide GO / HOLD for every file."""
from __future__ import annotations

import duckdb
import pandas as pd

FILE_RULES = {
    "FL001": ("Record count does not match control", "CRITICAL", "Reconciliation"),
    "FL002": ("Amount total does not match control", "CRITICAL", "Reconciliation"),
    "FL003": ("File layout changed", "CRITICAL", "Completeness"),
    "FL004": ("File not received by due date", "CRITICAL", "Timeliness"),
    "FL005": ("Same file sent twice", "WARNING", "Intake"),
    "FL006": ("File received after due date", "WARNING", "Timeliness"),
    "FL007": ("Unexpected extra column", "WARNING", "Completeness"),
    "FL008": ("No control totals supplied", "WARNING", "Reconciliation"),
}
EXC_COLS = ["file_id", "cedant_id", "period", "file_type", "rule_id", "rule_name", "severity",
            "dimension", "row_num", "record_key", "detail"]


def create_exceptions_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE exceptions (
            file_id VARCHAR, cedant_id VARCHAR, period VARCHAR, file_type VARCHAR, rule_id VARCHAR,
            rule_name VARCHAR, severity VARCHAR, dimension VARCHAR, row_num INTEGER,
            record_key VARCHAR, detail VARCHAR)""")


def add_file_exception(rows: list, meta: dict, rule_id: str, detail: str) -> None:
    name, sev, dim = FILE_RULES[rule_id]
    rows.append({"file_id": meta.get("file_id"), "cedant_id": meta["cedant_id"], "period": meta["period"],
                 "file_type": meta["file_type"], "rule_id": rule_id, "rule_name": name, "severity": sev,
                 "dimension": dim, "row_num": None, "record_key": None, "detail": detail})


def insert_rows(con, rows: list) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows, columns=EXC_COLS)
    df["row_num"] = df["row_num"].astype("Int64")
    con.register("_new_exc", df)
    con.execute("INSERT INTO exceptions SELECT * FROM _new_exc")
    con.unregister("_new_exc")


def run_rules(con: duckdb.DuckDBPyConnection, rules: list[dict]) -> dict[str, int]:
    """Run each rule's SQL in order and append failures to `exceptions`. Returns failures per rule."""
    counts = {}
    for r in rules:
        requires = r.get("requires") or []
        skip = ""
        if requires:
            fields = ", ".join(f"'{f}'" for f in requires)
            skip = f"AND q.file_id NOT IN (SELECT file_id FROM file_missing_fields WHERE field IN ({fields}))"
        sql = f"""
            INSERT INTO exceptions
            SELECT q.file_id, f.cedant_id, f.period, f.file_type, '{r['id']}', '{r['name'].replace("'", "''")}',
                   '{r['severity']}', '{r['dimension']}', q.row_num, q.record_key, q.detail
            FROM ({r['sql']}) q
            JOIN file_register f ON f.file_id = q.file_id
            WHERE f.file_type = '{r['file_type']}' {skip}
        """
        before = con.execute("SELECT count(*) FROM exceptions").fetchone()[0]
        con.execute(sql)
        counts[r["id"]] = con.execute("SELECT count(*) FROM exceptions").fetchone()[0] - before
    return counts


def decision_sql(hold_rate: float, min_rows: int = 100) -> str:
    """The GO / HOLD / REJECTED rule as one SELECT, so the app can run what-if thresholds."""
    return f"""
        WITH rowstats AS (
            SELECT file_id,
                   count(DISTINCT row_num) FILTER (WHERE severity = 'CRITICAL' AND row_num IS NOT NULL) AS critical_rows,
                   count(DISTINCT row_num) FILTER (WHERE severity = 'WARNING' AND row_num IS NOT NULL) AS warning_rows,
                   count(*) FILTER (WHERE severity = 'CRITICAL' AND row_num IS NULL) AS critical_file_checks,
                   string_agg(DISTINCT rule_id, ', ') FILTER (WHERE severity = 'CRITICAL' AND row_num IS NULL) AS file_rules
            FROM exceptions WHERE file_id IS NOT NULL GROUP BY file_id)
        SELECT f.file_id,
               coalesce(s.critical_rows, 0) AS critical_rows,
               coalesce(s.warning_rows, 0) AS warning_rows,
               CASE WHEN f.row_count > 0 THEN coalesce(s.critical_rows, 0) / f.row_count ELSE 0 END AS critical_rate,
               CASE WHEN f.status = 'REJECTED_DUPLICATE' THEN 'REJECTED'
                    WHEN coalesce(s.critical_file_checks, 0) > 0 THEN 'HOLD'
                    WHEN f.row_count >= {min_rows} AND coalesce(s.critical_rows, 0) / f.row_count > {hold_rate} THEN 'HOLD'
                    ELSE 'GO' END AS decision,
               CASE WHEN f.status = 'REJECTED_DUPLICATE' THEN 'Duplicate of ' || f.duplicate_of
                    WHEN coalesce(s.critical_file_checks, 0) > 0 THEN 'Failed file check: ' || s.file_rules
                    WHEN f.row_count >= {min_rows} AND coalesce(s.critical_rows, 0) / f.row_count > {hold_rate}
                         THEN printf('%.1f', 100.0 * s.critical_rows / f.row_count)
                              || '% of rows failed critical rules (limit {hold_rate:.0%})'
                    WHEN coalesce(s.critical_rows, 0) > 0
                         THEN CAST(s.critical_rows AS VARCHAR) || ' rows held back, rest loaded'
                    ELSE 'Clean' END AS decision_reason
        FROM file_register f LEFT JOIN rowstats s USING (file_id)
        WHERE f.status IN ('RECEIVED', 'REJECTED_DUPLICATE')
    """


def decide(con: duckdb.DuckDBPyConnection, hold_rate: float, min_rows: int = 100) -> pd.DataFrame:
    """GO / HOLD / REJECTED per file, with the reason."""
    con.execute("CREATE OR REPLACE TABLE file_decision AS " + decision_sql(hold_rate, min_rows))
    return con.execute("SELECT * FROM file_decision ORDER BY file_id").df()


def promote(con: duckdb.DuckDBPyConnection, batch_id: str) -> dict[str, int]:
    """Copy rows from GO files into production tables, leaving out rows that failed a critical rule."""
    out = {}
    for ftype in ("inforce", "billing", "claims"):
        con.execute(f"""
            CREATE OR REPLACE TABLE prod_{ftype} AS
            SELECT s.*, '{batch_id}' AS load_batch
            FROM stg_{ftype} s JOIN file_decision d USING (file_id)
            WHERE d.decision = 'GO'
              AND NOT EXISTS (SELECT 1 FROM exceptions e WHERE e.file_id = s.file_id AND e.row_num = s.row_num
                              AND e.severity = 'CRITICAL')
        """)
        out[ftype] = con.execute(f"SELECT count(*) FROM prod_{ftype}").fetchone()[0]
    return out
