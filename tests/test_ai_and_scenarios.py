"""GenAI helpers (offline mode), SQL guardrails, and detection across other seeds and defect profiles."""
import tempfile
from pathlib import Path

import pytest

from retro_intake import ai, evaluate, onboarding, pipeline, safesql


@pytest.mark.parametrize("seed,profile", [(11, "baseline"), (42, "heavy"), (3, "clean")])
def test_other_scenarios_catch_everything_and_nothing_else(seed, profile):
    res = pipeline.run(workdir=Path(tempfile.mkdtemp()), regenerate=True, quiet=True, seed=seed, profile=profile)
    s = evaluate.score(res["db_path"], res["answer_key"])
    assert s["caught"] == s["planted"] and not s["false_alarms"]
    if profile == "clean":
        assert s["planted"] == 0 and set(res["decisions"].decision) == {"GO"}


def test_mapping_assistant_maps_an_unseen_layout():
    headers, rows, _ = onboarding.sample_file()
    sug = ai.suggest_mapping(headers, rows)
    got = {f: v["source"] for f, v in sug["fields"].items()}
    assert got == {"policy_id": "Cert No", "life_id": "Insured Seq", "insured_dob": "Birth Dt", "sex": "Gender",
                   "smoker": "Tobacco Use", "issue_date": "Eff Dt", "issue_age": "Iss Age",
                   "face_amount": "Amt Insured (000s)", "retro_share": "Retro Pct", "status": "Pol Stat",
                   "currency": "Curr", "product": "Plan"}
    assert sug["date_format"] == "%Y/%m/%d"
    assert sug["multipliers"] == {"face_amount": 1000, "retro_share": 0.01}
    assert sug["codes"]["sex"]["UNKNOWN"] is None          # never guessed


def test_mapping_test_run_finds_the_sample_problems():
    headers, rows, _ = onboarding.sample_file()
    cfg = ai.mapping_to_config(ai.suggest_mapping(headers, rows), onboarding.NEW_CLIENT)
    out = onboarding.test_mapping(cfg, headers, rows)
    assert {k for k, v in out["counts"].items() if v} == {"IF001", "IF002", "IF003", "IF009", "IF011"}
    assert out["preview"].face_amount.min() >= 100000 and out["preview"].retro_share.iloc[0] == 0.35


@pytest.fixture(scope="module")
def run_res():
    return pipeline.run(workdir=Path(tempfile.mkdtemp()), regenerate=True, quiet=True)


@pytest.fixture(scope="module")
def con(run_res):
    c = safesql.connect(run_res["db_path"])
    yield c
    c.close()


@pytest.mark.parametrize("sql", ["DELETE FROM exceptions", "SELECT 1; SELECT 2", "CREATE TABLE x AS SELECT 1",
                                 "SELECT * FROM read_csv('/etc/passwd')", "PRAGMA version", "ATTACH 'x.db'",
                                 "COPY exceptions TO 'x.csv'"])
def test_guardrails_refuse_unsafe_sql(con, sql):
    with pytest.raises(safesql.Refused):
        safesql.run(con, sql)


def test_every_example_question_runs(con):
    for q in ai.EXAMPLES + ["critical billing issues in august for northbridge", "exceptions for policy NBL0000475"]:
        sql, _ = ai.ask_data_offline(q)
        df = safesql.run(con, sql)
        assert len(df) > 0, q


@pytest.mark.parametrize("q,title", [("When is a file held?", "The gate: when a file is held"),
                                     ("How do we onboard a new client?", "Onboarding a new client"),
                                     ("Can AI decide what loads to production?", "Using AI tools safely"),
                                     ("A client keeps sending duplicates", "Recurring issues and root cause")])
def test_runbook_retrieval_finds_the_right_section(q, title):
    rb = ai.Runbook()
    hits = rb.search(q)
    assert hits[0]["title"] == title
    assert "[1]" in rb.answer_offline(q, hits)


def test_ai_evaluations(run_res):
    from retro_intake import aieval
    em = aieval.eval_mapping(run_res["inbox"])
    assert (em.score == 1).all() and em["date format"].all() and em.units.all()
    assert aieval.eval_ask(run_res["db_path"]).correct.all()
    rb = aieval.eval_runbook()
    assert rb["top 3"].all() and rb["top 1"].mean() >= 0.8


def test_data_specification_lists_every_field():
    from retro_intake import spec
    from retro_intake.common import load_config
    c = load_config()["clients"]["C04"]
    doc = spec.build(c)
    for src in c["columns"]["inforce"].values():
        assert f"| {src} |" in doc
    assert "YYYYMMDD" in doc and "width 12" in doc


def test_guardrail_blocks_file_and_dynamic_sql():
    from retro_intake import safesql
    bad = ["SELECT * FROM read_csv_auto('/etc/passwd')", "SELECT * FROM query('select 1')",
           "SELECT * FROM query_table('exceptions')", "SELECT * FROM duckdb_settings()",
           "SELECT current_setting('home_directory')", "SELECT * FROM parquet_scan('x')",
           "SELECT * FROM '/etc/passwd'", "SELECT * FROM 's3://bucket/x.csv'", "SELECT * FROM sniff_csv('a')"]
    for sql in bad:
        with pytest.raises(safesql.Refused):
            safesql.check(sql)
    for sql in ["SELECT count(*) FROM exceptions", "SELECT * FROM exceptions WHERE status = 'N/A'",
                "SELECT rule_id, count(*) AS n FROM exceptions GROUP BY 1 ORDER BY n DESC OFFSET 0"]:
        safesql.check(sql)
