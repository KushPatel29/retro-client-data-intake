"""Score a run against the generator's answer key: what was planted, what was caught, what was extra."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

KEY_COLS = ["cedant_id", "period", "file_type", "rule_id", "record_key"]


def score(db_path: Path, key_path: Path) -> dict:
    con = duckdb.connect(str(db_path), read_only=True)
    found = con.execute("SELECT cedant_id, period, file_type, rule_id, coalesce(record_key, '') AS record_key "
                        "FROM exceptions").df()
    con.close()
    key = pd.read_csv(key_path, dtype=str).fillna("")
    planted = set(map(tuple, key[KEY_COLS].values))
    got = set(map(tuple, found[KEY_COLS].values))
    per_rule = (pd.DataFrame(sorted(planted), columns=KEY_COLS).assign(caught=lambda d: [tuple(r) in got for r in d.values])
                .groupby("rule_id").agg(planted=("caught", "size"), caught=("caught", "sum")).reset_index()
                if planted else pd.DataFrame(columns=["rule_id", "planted", "caught"]))
    return {"planted": len(planted), "caught": len(planted & got), "missed": sorted(planted - got),
            "false_alarms": sorted(got - planted), "per_rule": per_rule}
