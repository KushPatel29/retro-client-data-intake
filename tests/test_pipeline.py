"""End-to-end tests: the generator plants known problems, the pipeline must find all of them and nothing else."""
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from retro_intake import pipeline
from retro_intake.common import DATA_DIR

KEY_COLS = ["cedant_id", "period", "file_type", "rule_id", "record_key"]


@pytest.fixture(scope="session")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("run") / "warehouse.duckdb"
    pipeline.run(db_path=path, regenerate=True, quiet=True)
    con = duckdb.connect(str(path), read_only=True)
    yield con
    con.close()


@pytest.fixture(scope="session")
def found(db):
    df = db.execute("SELECT cedant_id, period, file_type, rule_id, coalesce(record_key, '') AS record_key "
                    "FROM exceptions").df()
    return set(map(tuple, df[KEY_COLS].values))


@pytest.fixture(scope="session")
def planted():
    key = pd.read_csv(DATA_DIR / "answer_key.csv", dtype=str).fillna("")
    return set(map(tuple, key[KEY_COLS].values))


def test_every_planted_problem_is_caught(found, planted):
    missed = sorted(planted - found)
    assert not missed, f"{len(missed)} planted problems were not caught: {missed[:10]}"


def test_no_false_alarms(found, planted):
    extra = sorted(found - planted)
    assert not extra, f"{len(extra)} exceptions were raised on clean data: {extra[:10]}"


def test_every_rule_is_exercised(planted, db):
    rules = set(db.execute("SELECT rule_id FROM ref_rule").df().rule_id)
    tested = {k[3] for k in planted}
    untested = rules - tested - {"FL008"}   # FL008 (no control totals) has no planted case
    assert not untested, f"rules with no planted test case: {sorted(untested)}"


def test_file_decisions(db):
    d = db.execute("""SELECT f.file_name, d.decision, d.decision_reason FROM file_decision d
                      JOIN file_register f USING (file_id)""").df().set_index("file_name")
    held = set(d.index[d.decision == "HOLD"])
    assert held == {"HRL_billing_202608.txt",      # record count does not match control
                    "Kestrel_inforce_202609.xlsx",  # amount total does not match control
                    "HRL_inforce_202609.txt",       # column renamed (layout changed)
                    "AMR_inforce_202609.dat"}       # 2.4% of rows failed critical rules
    assert d.loc["NBL_billing_202608_resend.csv", "decision"] == "REJECTED"
    assert "FL003" in d.loc["HRL_inforce_202609.txt", "decision_reason"]


def test_missing_file_reported(db):
    m = db.execute("SELECT cedant_id, period, file_type FROM missing_file").fetchall()
    assert m == [("C03", "2026-08", "claims")]


def test_no_critical_row_reaches_production(db):
    for ftype in ("inforce", "billing", "claims"):
        bad = db.execute(f"""SELECT count(*) FROM prod_{ftype} p JOIN exceptions e
                             ON e.file_id = p.file_id AND e.row_num = p.row_num AND e.severity = 'CRITICAL'""").fetchone()[0]
        assert bad == 0


def test_held_files_load_nothing(db):
    n = db.execute("""SELECT count(*) FROM prod_inforce p JOIN file_decision d USING (file_id)
                      WHERE d.decision <> 'GO'""").fetchone()[0]
    assert n == 0


def test_every_staged_row_is_accounted_for(db):
    """For GO files: rows loaded + rows held back = rows received."""
    for ftype in ("inforce", "billing", "claims"):
        diff = db.execute(f"""
            WITH s AS (SELECT file_id, count(*) n FROM stg_{ftype} GROUP BY 1),
                 p AS (SELECT file_id, count(*) n FROM prod_{ftype} GROUP BY 1),
                 q AS (SELECT file_id, count(DISTINCT row_num) n FROM exceptions
                       WHERE severity = 'CRITICAL' AND row_num IS NOT NULL GROUP BY 1)
            SELECT count(*) FROM s JOIN file_decision d USING (file_id)
            LEFT JOIN p USING (file_id) LEFT JOIN q USING (file_id)
            WHERE d.decision = 'GO' AND s.n <> coalesce(p.n, 0) + coalesce(q.n, 0)""").fetchone()[0]
        assert diff == 0


def test_control_totals_match_for_clean_files(db):
    bad = db.execute("""SELECT file_name FROM file_register
                        WHERE status = 'RECEIVED' AND control_count = row_count
                          AND abs(control_total - amount_total) > 0.01
                          AND file_name <> 'Kestrel_inforce_202609.xlsx'""").fetchall()
    assert bad == []


def test_outputs_written(db):
    out = Path(pipeline.OUTPUT)
    assert (out / "dashboard_data.json").exists()
    assert list((out / "exception_reports").glob("*.xlsx"))
    assert list((out / "client_followups").glob("*.md"))


def test_inforce_roll_forward_balances(db):
    """Opening - terminations - unexplained drops + new + late-reported = closing, for every client and month."""
    rf = db.execute("SELECT * FROM mart_rollforward").df()
    assert len(rf) == 8   # 4 clients x 2 months that have a prior month
    calc = (rf.opening_active - rf.lapsed - rf.claims_terminations - rf.disappeared
            + rf.new_business + rf.late_reported)
    assert (calc == rf.closing_active).all(), rf.assign(calc=calc)


def test_followup_drafts_name_the_held_file(db):
    text = (Path(pipeline.OUTPUT) / "client_followups" / "C02_2026-09_followup.md").read_text()
    assert "HRL_inforce_202609.txt" in text and "TobaccoCd" in text
