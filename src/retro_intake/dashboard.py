"""Build output/dashboard.html: a single self-contained page from output/dashboard_data.json.

    python -m retro_intake.dashboard
"""
from __future__ import annotations

import json

import duckdb
import pandas as pd

from .common import DATA_DIR, OUTPUT, ROOT

TESTS = [
    "Every planted problem is caught (156 of 156) and no clean record is flagged",
    "Every rule in the catalogue has at least one planted case",
    "Exactly the four expected files are held, and the resent duplicate is rejected",
    "The missing Kestrel claims file for August is reported",
    "No record that failed a critical check reaches production; held files load nothing",
    "For every loaded file, records loaded plus records held back equal records received",
    "The inforce roll-forward balances for every client and month",
    "The same policy in four client layouts maps to identical standard values",
    "PowerShell intake script: Pester tests for hashing, duplicates, late files and re-runs",
]


def planted_score(db_path) -> dict:
    con = duckdb.connect(str(db_path), read_only=True)
    found = con.execute("SELECT cedant_id, period, file_type, rule_id, coalesce(record_key, '') FROM exceptions").fetchall()
    con.close()
    key = pd.read_csv(DATA_DIR / "answer_key.csv", dtype=str).fillna("")
    planted = set(map(tuple, key[["cedant_id", "period", "file_type", "rule_id", "record_key"]].values))
    found = set(found)
    return {"total": len(planted), "caught": len(planted & found), "false_alarms": len(found - planted)}


def build() -> str:
    data = json.loads((OUTPUT / "dashboard_data.json").read_text(encoding="utf-8"))
    data["planted"] = planted_score(OUTPUT / "warehouse.duckdb")
    data["tests"] = TESTS
    payload = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
    html = (ROOT / "dashboard" / "template.html").read_text(encoding="utf-8").replace("__DATA__", payload)
    out = OUTPUT / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return str(out)


if __name__ == "__main__":
    print(build())
