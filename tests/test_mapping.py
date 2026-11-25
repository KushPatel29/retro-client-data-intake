"""Unit tests: each client's layout maps to the same standard values."""
from datetime import date

import duckdb

from retro_intake import mapping
from retro_intake.common import load_config, treaty_monthly_premium

CFG = load_config()


def _std(cid, ftype, headers, row):
    parsed = mapping.ParsedFile(headers, [dict(zip(headers, row))], None, None, [])
    df, missing = mapping.standardize(parsed, CFG["clients"][cid], ftype,
                                      {"file_id": "T", "cedant_id": cid, "period": "2026-07"})
    return df.iloc[0], missing


def test_same_policy_in_four_layouts_maps_identically():
    cases = {
        "C01": ["NBL0000001", "1", "1970-03-15", "F", "N", "2015-06-01", "45", "500000", "50", "ACTIVE", "CAD", "UL"],
        "C02": ["NBL0000001", "1", "03/15/1970", "2", "0", "06/01/2015", "45", "500", "0.5", "IF", "CAD", "UL"],
        "C03": ["NBL0000001", "1", "15/03/1970", "FEMALE", "NS", "01/06/2015", "45", "500000", "0.5", "INFORCE", "CAD", "UL"],
        "C04": ["NBL0000001", "01", "19700315", "F", "N", "20150601", "045", "000000500000", "00500", "A", "CAD", "UL"],
    }
    results = []
    for cid, row in cases.items():
        headers = list(CFG["clients"][cid]["columns"]["inforce"].values())
        rec, missing = _std(cid, "inforce", headers, row)
        assert not missing
        results.append((rec.insured_dob, rec.sex, rec.smoker, rec.issue_date, rec.issue_age,
                        rec.face_amount, rec.retro_share, rec.status, rec.life_id))
    assert all(r == results[0] for r in results), results
    assert results[0] == (date(1970, 3, 15), "F", "N", date(2015, 6, 1), 45, 500000.0, 0.5, "ACTIVE", 1)


def test_fixed_width_premium_has_implied_decimals():
    headers = list(CFG["clients"]["C04"]["columns"]["billing"].values())
    rec, _ = _std("C04", "billing", headers, ["AMR00000001", "01", "-00000012345", "REV", "GBP"])
    assert rec.premium == -123.45 and rec.transaction_type == "REVERSAL"


def test_unknown_code_is_kept_raw_not_guessed():
    headers = list(CFG["clients"]["C01"]["columns"]["inforce"].values())
    row = ["P1", "1", "1970-03-15", "X", "N", "2015-06-01", "45", "500000", "50", "ACTIVE", "CAD", "UL"]
    rec, _ = _std("C01", "inforce", headers, row)
    assert rec.sex is None and rec.sex_raw == "X"


def test_bad_date_is_kept_raw():
    headers = list(CFG["clients"]["C01"]["columns"]["inforce"].values())
    row = ["P1", "1", "1970-03-15", "M", "N", "2025-02-30", "45", "500000", "50", "ACTIVE", "CAD", "UL"]
    rec, _ = _std("C01", "inforce", headers, row)
    assert rec.issue_date is None and rec.issue_date_raw == "2025-02-30"


def test_missing_column_is_reported():
    headers = [h for h in CFG["clients"]["C02"]["columns"]["inforce"].values() if h != "TobaccoCd"]
    row = ["1", "1", "03/15/1970", "2", "06/01/2015", "45", "500", "0.5", "IF", "USD", "T20"]
    rec, missing = _std("C02", "inforce", headers, row)
    assert missing == ["smoker"] and rec.smoker is None


def test_age_last_birthday_macro():
    con = duckdb.connect()
    con.execute("""CREATE MACRO alb(dob, d) AS
                   year(d) - year(dob) - CASE WHEN strftime(d, '%m%d') < strftime(dob, '%m%d') THEN 1 ELSE 0 END""")
    q = "SELECT alb(DATE '1970-03-15', DATE '{}')"
    assert con.execute(q.format("2015-03-14")).fetchone()[0] == 44
    assert con.execute(q.format("2015-03-15")).fetchone()[0] == 45


def test_treaty_rate_matches_formula():
    basis = {"base": 0.5, "slope": 0.085, "smoker": 2.0, "female": 0.85}
    # 500,000 face, 50% ceded, age 30 male non-smoker: 250 units x 0.50 per year / 12
    assert treaty_monthly_premium(basis, 500000, 0.5, 30, "M", "N") == round(250 * 0.5 / 12, 2)
