"""Evaluation of the AI features against labelled test sets, so their accuracy is measured, not claimed."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import ai, mapping, onboarding, safesql
from .common import load_config

ASK_CASES = [  # question, reference SQL, columns compared as a set
    ("Which files were held, and why?",
     "SELECT file_name FROM mart_file_scorecard WHERE decision IN ('HOLD', 'MISSING', 'REJECTED')", ["file_name"]),
    ("Which files arrived late?", "SELECT file_name FROM mart_file_scorecard WHERE days_late > 0", ["file_name"]),
    ("Show critical exceptions for Harbourline in September",
     "SELECT record_key, detail FROM exceptions WHERE cedant_id = 'C02' AND period = '2026-09' AND severity = 'CRITICAL'",
     ["detail"]),
    ("How much premium was loaded per client and month?",
     "SELECT cedant_id, period FROM mart_portfolio", ["cedant_id", "period"]),
    ("Which checks found the most problems?",   # ranked answer: its first row must be the top check
     "SELECT rule_id FROM exceptions GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", ["rule_id", "first"]),
    ("Which issues keep coming back?", "SELECT cedant_id, rule_id FROM mart_recurring_issues", ["cedant_id", "rule_id"]),
    ("Show the inforce roll-forward for Atlantic Mutual",
     "SELECT period, closing_active FROM mart_rollforward WHERE cedant_id = 'C04'", ["period", "closing_active"]),
    ("What is the quality score for Kestrel in August?",
     "SELECT quality_score FROM mart_client_scorecard WHERE cedant_id = 'C03' AND period = '2026-08'", ["quality_score"]),
    ("Critical billing issues for Northbridge in August",
     "SELECT detail FROM exceptions WHERE cedant_id = 'C01' AND period = '2026-08' AND file_type = 'billing' "
     "AND severity = 'CRITICAL'", ["detail"]),
    ("Which files are missing?", "SELECT cedant_id, period, file_type FROM missing_file", ["cedant_id", "period", "file_type"]),
]
RUNBOOK_CASES = [
    ("When is a file held?", "The gate: when a file is held"),
    ("What if the control total does not agree?", "Control totals"),
    ("How do we onboard a new client?", "Onboarding a new client"),
    ("A client keeps sending duplicates", "Recurring issues and root cause"),
    ("Can AI decide what loads to production?", "Using AI tools safely"),
    ("How long do we keep client files?", "Data security and retention"),
    ("How do we test a rule change?", "Rule changes and user acceptance testing"),
    ("When do we escalate to the manager?", "Escalation"),
    ("What is a retro share?", "Glossary"),
    ("When are files due each month?", "Monthly calendar"),
    ("The client sent a corrected file. What happens to the old one?", "Resubmissions"),
    ("A file arrived after the due date", "Late and missing files"),
]


def eval_mapping(inbox: Path) -> pd.DataFrame:
    cfg = load_config()
    rows = []
    for cid, c in cfg["clients"].items():
        p = inbox / c["filename"].format(file_type="inforce", yyyymm="202607")
        parsed = mapping.read_raw(p, c, "inforce")
        sug = ai.suggest_mapping(parsed.headers, parsed.rows)
        truth = c["columns"]["inforce"]
        ok = sum(sug["fields"][f]["source"] == src for f, src in truth.items())
        mult_ok = all(abs(sug["multipliers"][k] - c["multipliers"].get(k, 1)) < 1e-9 for k in ("face_amount", "retro_share"))
        rows.append({"layout": f"{c['name']} ({c['format']})", "fields correct": f"{ok}/{len(truth)}",
                     "date format": sug["date_format"] == c["date_format"], "units": mult_ok, "score": ok / len(truth)})
    headers, data, _ = onboarding.sample_file()
    sug = ai.suggest_mapping(headers, data)
    truth = dict(zip(["policy_id", "life_id", "insured_dob", "sex", "smoker", "issue_date", "issue_age", "face_amount",
                      "retro_share", "status", "currency", "product"], onboarding.HEADERS))
    ok = sum(sug["fields"][f]["source"] == src for f, src in truth.items())
    rows.append({"layout": "Aurora Life Re, never seen before (csv)", "fields correct": f"{ok}/{len(truth)}",
                 "date format": sug["date_format"] == "%Y/%m/%d",
                 "units": sug["multipliers"] == {"face_amount": 1000, "retro_share": 0.01}, "score": ok / len(truth)})
    return pd.DataFrame(rows)


def eval_ask(db_path: Path) -> pd.DataFrame:
    con = safesql.connect(db_path)
    out = []
    try:
        for q, ref, cols in ASK_CASES:
            sql, _ = ai.ask_data_offline(q)
            try:
                got = safesql.run(con, sql)
                want = safesql.run(con, ref)
                if "first" in cols:
                    cols = [c for c in cols if c != "first"]
                    got = got.head(1)
                ok = all(c in got.columns for c in cols) and \
                    set(map(tuple, got[cols].astype(str).values)) == set(map(tuple, want[cols].astype(str).values))
                note = "" if ok else f"returned {len(got)} rows, expected {len(want)}"
            except Exception as e:
                ok, note = False, str(e)[:80]
            out.append({"question": q, "correct": ok, "note": note})
    finally:
        con.close()
    return pd.DataFrame(out)


def eval_runbook() -> pd.DataFrame:
    rb = ai.Runbook()
    out = []
    for q, want in RUNBOOK_CASES:
        hits = rb.search(q, k=3)
        titles = [h["title"] for h in hits]
        out.append({"question": q, "expected section": want, "top result": titles[0] if titles else "",
                    "top 1": bool(titles) and titles[0] == want, "top 3": want in titles})
    return pd.DataFrame(out)
