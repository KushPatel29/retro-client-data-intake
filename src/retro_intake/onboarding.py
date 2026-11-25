"""New-client onboarding: a sample file from a client we have never seen, and a test run of a draft mapping."""
from __future__ import annotations

import csv
import io
import random
from datetime import date, timedelta

import duckdb
import pandas as pd

from . import mapping, validate
from .common import age_last_birthday, load_rules
from .pipeline import STG_COLS, TYPES

NEW_CLIENT = {"name": "Aurora Life Re (new client, fictional)", "country": "Canada", "treaty_id": "AUR-YRT-2027",
              "currency": "CAD", "min_issue_age": 18, "max_issue_age": 80, "jumbo_limit": 20000000,
              "format": "csv", "delimiter": ",", "control": "none"}
HEADERS = ["Cert No", "Insured Seq", "Birth Dt", "Gender", "Tobacco Use", "Eff Dt", "Iss Age",
           "Amt Insured (000s)", "Retro Pct", "Pol Stat", "Curr", "Plan"]


def sample_file(n: int = 400, seed: int = 5, defects: bool = True) -> tuple[list[str], list[dict], str]:
    """A first file from a new client, in a layout none of the existing mappings knows."""
    r = random.Random(seed)
    rows = []
    for i in range(n):
        issue = date(2012, 1, 1) + timedelta(days=r.randint(0, 5000))
        age = r.randint(22, 66)
        dob = issue - timedelta(days=int(age * 365.25) + r.randint(30, 330))
        rows.append({"Cert No": f"AU-{700000 + i * 3}", "Insured Seq": "1",
                     "Birth Dt": dob.strftime("%Y/%m/%d"), "Gender": r.choice(["Male", "Female"]),
                     "Tobacco Use": "Y" if r.random() < 0.1 else "N", "Eff Dt": issue.strftime("%Y/%m/%d"),
                     "Iss Age": str(age_last_birthday(dob, issue)), "Amt Insured (000s)": str(r.choice([100, 250, 500, 750, 1000, 2000])),
                     "Retro Pct": "35", "Pol Stat": r.choice(["A"] * 30 + ["L"]), "Curr": "CAD",
                     "Plan": r.choice(["T10", "T20", "UL"])})
    if defects:
        rows[7]["Gender"] = "Unknown"
        rows[19]["Birth Dt"] = ""
        rows[33]["Eff Dt"] = "2019/02/30"
        rows[50]["Curr"] = "USD"
        rows.append(dict(rows[60]))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADERS)
    w.writeheader()
    w.writerows(rows)
    return HEADERS, rows, buf.getvalue()


def read_csv_text(text: str) -> tuple[list[str], list[dict]]:
    rdr = csv.DictReader(io.StringIO(text))
    rows = [{k: (v or "").strip() for k, v in row.items()} for row in rdr]
    return list(rdr.fieldnames or []), rows


def test_mapping(client_cfg: dict, headers: list[str], rows: list[dict], period: str = "2026-09"):
    """Standardize the sample with a draft mapping and run the inforce rules on it, in memory."""
    for f in ("transaction_type", "claim_type"):
        client_cfg.setdefault("codes", {}).setdefault(f, {})
    for f in ("sex", "smoker", "status"):
        client_cfg["codes"].setdefault(f, {})
    parsed = mapping.ParsedFile(headers, rows, None, None, [])
    meta = {"file_id": "NEW", "cedant_id": "C05", "period": period}
    df, missing = mapping.standardize(parsed, client_cfg, "inforce", meta)
    con = duckdb.connect()
    con.execute("""CREATE MACRO alb(dob, d) AS
                   year(d) - year(dob) - CASE WHEN strftime(d, '%m%d') < strftime(dob, '%m%d') THEN 1 ELSE 0 END""")
    ref = pd.DataFrame([{"cedant_id": "C05", "currency": client_cfg["currency"],
                         "min_issue_age": client_cfg["min_issue_age"], "max_issue_age": client_cfg["max_issue_age"],
                         "jumbo_limit": float(client_cfg["jumbo_limit"])}])
    con.execute("CREATE TABLE ref_cedant AS SELECT * FROM ref")
    reg = pd.DataFrame([{"file_id": "NEW", "cedant_id": "C05", "period": period, "file_type": "inforce"}])
    con.execute("CREATE TABLE file_register AS SELECT * FROM reg")
    con.execute("CREATE TABLE file_missing_fields (file_id VARCHAR, field VARCHAR)")
    for f in missing:
        con.execute("INSERT INTO file_missing_fields VALUES ('NEW', ?)", [f])
    base = ["file_id", "row_num", "cedant_id", "period", "period_start", "period_end", "record_key"]
    for c in STG_COLS["inforce"]:
        if c not in df.columns:
            df[c] = None
    sel = ", ".join(f"CAST({c} AS {TYPES[c]}) AS {c}" if c in TYPES else c for c in base + STG_COLS["inforce"])
    con.register("_d", df[base + STG_COLS["inforce"]])
    con.execute(f"CREATE TABLE stg_inforce AS SELECT {sel} FROM _d")
    validate.create_exceptions_table(con)
    rules = [r for r in load_rules() if r["file_type"] == "inforce" and r["id"].startswith("IF")]
    counts = validate.run_rules(con, rules)
    exc = con.execute("SELECT rule_id, rule_name, severity, row_num, record_key, detail FROM exceptions ORDER BY row_num").df()
    preview = con.execute("SELECT policy_id, life_id, insured_dob, sex, smoker, issue_date, issue_age, face_amount, "
                          "retro_share, status, currency, product FROM stg_inforce LIMIT 8").df()
    con.close()
    return {"rows": len(df), "missing_fields": missing, "counts": counts, "exceptions": exc, "preview": preview}
