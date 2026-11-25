"""Run the whole monthly control process end to end.

    python -m retro_intake.pipeline            # process whatever is in data/inbox
    python -m retro_intake.pipeline --generate # rebuild the synthetic inbox first
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from . import generate, intake, mapping, report, validate
from .common import ARCHIVE, AS_OF, DATA_DIR, INBOX, OUTPUT, load_config, load_rules

STG_COLS = {
    "inforce": ["policy_id", "life_id", "insured_dob", "insured_dob_raw", "sex", "sex_raw", "smoker", "smoker_raw",
                "issue_date", "issue_date_raw", "issue_age", "face_amount", "retro_share", "status", "status_raw",
                "currency", "product"],
    "billing": ["policy_id", "life_id", "premium", "transaction_type", "transaction_type_raw", "currency"],
    "claims": ["claim_id", "policy_id", "life_id", "date_of_event", "date_of_event_raw", "claim_type",
               "claim_type_raw", "claim_amount", "currency"],
}
TYPES = {"insured_dob": "DATE", "issue_date": "DATE", "date_of_event": "DATE", "life_id": "INTEGER",
         "issue_age": "INTEGER", "face_amount": "DOUBLE", "retro_share": "DOUBLE", "premium": "DOUBLE",
         "claim_amount": "DOUBLE", "row_num": "INTEGER", "period_start": "DATE", "period_end": "DATE"}


def run(db_path: Path | None = None, regenerate: bool = False, quiet: bool = False,
        workdir: Path | None = None, seed: int = generate.SEED, profile: str = "baseline",
        hold_rate: float | None = None) -> dict:
    """Run everything. With `workdir`, the inbox, archive, answer key and outputs all live under it."""
    log = (lambda *a: None) if quiet else print
    inbox, archive, out, key = INBOX, ARCHIVE, OUTPUT, DATA_DIR / "answer_key.csv"
    if workdir is not None:
        workdir = Path(workdir)
        inbox, archive, out, key = workdir / "inbox", workdir / "archive", workdir / "output", workdir / "answer_key.csv"
    if regenerate:
        generate.main(seed=seed, inbox=inbox, key_path=key, profile=profile, quiet=quiet)
    cfg = load_config()
    if hold_rate is not None:
        cfg["critical_rate_hold"] = hold_rate
    rules = load_rules()
    out.mkdir(parents=True, exist_ok=True)
    db_path = db_path or out / "warehouse.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    con.execute("""CREATE MACRO alb(dob, d) AS
                   year(d) - year(dob) - CASE WHEN strftime(d, '%m%d') < strftime(dob, '%m%d') THEN 1 ELSE 0 END""")
    ref = pd.DataFrame([{"cedant_id": k, "cedant_name": v["name"], "country": v["country"],
                         "treaty_id": v["treaty_id"], "currency": v["currency"], "file_format": v["format"],
                         "min_issue_age": v["min_issue_age"], "max_issue_age": v["max_issue_age"],
                         "jumbo_limit": float(v["jumbo_limit"]), "rate_base": v["rate_basis"]["base"],
                         "rate_slope": v["rate_basis"]["slope"], "rate_smoker": v["rate_basis"]["smoker"],
                         "rate_female": v["rate_basis"]["female"]} for k, v in cfg["clients"].items()])
    con.execute("CREATE TABLE ref_cedant AS SELECT * FROM ref")

    # 1. intake -----------------------------------------------------------------------------
    reg = intake.register_files(cfg, inbox)
    missing = intake.missing_files(cfg, reg)
    intake.archive(reg, archive)
    log(f"Intake: {len(reg)} files registered, {len(missing)} missing, "
        f"{(reg.status == 'REJECTED_DUPLICATE').sum()} duplicate")

    # 2. read and map each file ------------------------------------------------------------
    file_exc, frames, missing_fields = [], {t: [] for t in STG_COLS}, []
    reg["row_count"], reg["amount_total"] = 0, 0.0
    reg["control_count"], reg["control_total"] = None, None
    for i, r in reg.iterrows():
        meta = r.to_dict()
        if r["status"] == "REJECTED_DUPLICATE":
            validate.add_file_exception(file_exc, meta, "FL005",
                                        f"Identical to {r['duplicate_of']} (same SHA-256); not processed again")
            continue
        if r["status"] != "RECEIVED":
            continue
        c = cfg["clients"][r["cedant_id"]]
        parsed = mapping.read_raw(Path(r["path"]), c, r["file_type"])
        df, miss = mapping.standardize(parsed, c, r["file_type"], meta)
        chk = mapping.control_check(parsed, df, r["file_type"])
        for k, v in chk.items():
            reg.at[i, k] = v
        for f in miss:
            missing_fields.append({"file_id": r["file_id"], "field": f})
        for kind, col in parsed.schema_issues:
            if kind == "UNEXPECTED_COLUMN":
                validate.add_file_exception(file_exc, meta, "FL007", f"Column '{col}' is not in the agreed layout")
            elif kind == "MISSING_COLUMN":
                validate.add_file_exception(file_exc, meta, "FL003", f"Agreed column '{col}' is missing")
            else:
                validate.add_file_exception(file_exc, meta, "FL003", f"Wrong line length on {col}")
        if chk["control_count"] is None:
            validate.add_file_exception(file_exc, meta, "FL008", "No trailer or control file found")
        else:
            if chk["control_count"] != chk["row_count"]:
                validate.add_file_exception(file_exc, meta, "FL001",
                                            f"Control says {chk['control_count']:,} records, file has {chk['row_count']:,}")
            if abs(chk["control_total"] - chk["amount_total"]) > cfg["amount_tolerance"]:
                validate.add_file_exception(file_exc, meta, "FL002",
                                            f"Control total {chk['control_total']:,.2f}, file adds to "
                                            f"{chk['amount_total']:,.2f} (diff {chk['amount_total'] - chk['control_total']:,.2f})")
        if r["days_late"] > 0:
            validate.add_file_exception(file_exc, meta, "FL006",
                                        f"Due {r['due_date']}, received {r['received_at']:%Y-%m-%d} ({r['days_late']} days late)")
        frames[r["file_type"]].append(df)
    for _, m in missing.iterrows():
        validate.add_file_exception(file_exc, {**m.to_dict(), "file_id": None}, "FL004",
                                    f"Expected {m['expected_name']} by {m['due_date']}; {m['days_overdue']} days overdue")

    reg_out = reg.drop(columns=["path"])
    con.execute("CREATE TABLE file_register AS SELECT * FROM reg_out")
    con.execute("CREATE TABLE missing_file AS SELECT * FROM missing") if len(missing) else \
        con.execute("""CREATE TABLE missing_file (cedant_id VARCHAR, period VARCHAR, file_type VARCHAR,
                       expected_name VARCHAR, due_date DATE, days_overdue INTEGER)""")
    con.execute("CREATE TABLE file_missing_fields (file_id VARCHAR, field VARCHAR)")
    for m in missing_fields:
        con.execute("INSERT INTO file_missing_fields VALUES (?, ?)", [m["file_id"], m["field"]])
    base = ["file_id", "row_num", "cedant_id", "period", "period_start", "period_end", "record_key"]
    for ftype, cols in STG_COLS.items():
        df = pd.concat(frames[ftype], ignore_index=True)[base + cols]
        select = ", ".join(f"CAST({c} AS {TYPES[c]}) AS {c}" if c in TYPES else c for c in base + cols)
        con.register("_stg", df)
        con.execute(f"CREATE TABLE stg_{ftype} AS SELECT {select} FROM _stg")
        con.unregister("_stg")

    # 3. validate ---------------------------------------------------------------------------
    validate.create_exceptions_table(con)
    validate.insert_rows(con, file_exc)
    counts = validate.run_rules(con, rules)
    log("Rules: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))

    # 4. decide and promote -----------------------------------------------------------------
    decisions = validate.decide(con, cfg["critical_rate_hold"], cfg["hold_min_rows"])
    batch = f"B{AS_OF:%Y%m%d}-{datetime.now():%H%M%S}"
    promoted = validate.promote(con, batch)
    log(f"Decisions: {decisions.decision.value_counts().to_dict()}; promoted {promoted}")

    # 5. outputs ----------------------------------------------------------------------------
    rules_meta = [{"rule_id": r["id"], "rule_name": r["name"], "file_type": r["file_type"],
                   "severity": r["severity"], "dimension": r["dimension"]} for r in rules]
    rules_meta += [{"rule_id": k, "rule_name": v[0], "file_type": "any", "severity": v[1], "dimension": v[2]}
                   for k, v in validate.FILE_RULES.items()]
    rm = pd.DataFrame(rules_meta)
    con.execute("CREATE TABLE ref_rule AS SELECT * FROM rm")
    summary = report.build_all(con, cfg, out)
    con.close()
    log(f"Outputs written to {out}")
    return {"decisions": decisions, "promoted": promoted, "rule_counts": counts, "summary": summary,
            "db_path": db_path, "out": out, "answer_key": key, "inbox": inbox}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generate", action="store_true", help="rebuild the synthetic inbox first")
    args = ap.parse_args()
    run(regenerate=args.generate)


if __name__ == "__main__":
    main()
