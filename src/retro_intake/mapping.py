"""Read each client's file in its own layout and map it to the standard format.

Every client sends different column names, date formats, codes and units. One mapping entry per
client in config/clients.yaml handles all of it, so adding a client is configuration, not code.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from .common import AMOUNT_FIELD, period_end, period_start

DATE_FIELDS = {"insured_dob", "issue_date", "date_of_event"}
CODE_FIELDS = {"sex", "smoker", "status", "transaction_type", "claim_type"}
NUM_FIELDS = {"face_amount", "retro_share", "premium", "claim_amount"}
INT_FIELDS = {"issue_age", "life_id"}


class ParsedFile:
    def __init__(self, headers, rows, control_count, control_total, schema_issues):
        self.headers = headers
        self.rows = rows                  # list of dicts, raw strings keyed by source column
        self.control_count = control_count
        self.control_total = control_total
        self.schema_issues = schema_issues  # list of (kind, column)


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_raw(path: Path, c: dict, ftype: str) -> ParsedFile:
    fmt = c["format"]
    mapped = list(c["columns"][ftype].values())
    ctrl_count = ctrl_total = None
    if fmt in ("csv", "pipe"):
        with open(path, newline="", encoding="utf-8") as fh:
            lines = list(csv.reader(fh, delimiter=c["delimiter"]))
        headers, body = lines[0], lines[1:]
        if body and body[-1] and body[-1][0] == "TRL":
            ctrl_count, ctrl_total = int(body[-1][1]), float(body[-1][2])
            body = body[:-1]
        rows = [dict(zip(headers, (x.strip() for x in line))) for line in body if line]
        ctl = path.with_suffix(".ctl")
        if c["control"] == "sidecar" and ctl.exists():
            meta = json.loads(ctl.read_text(encoding="utf-8"))
            ctrl_count, ctrl_total = int(meta["record_count"]), float(meta["total_amount"])
    elif fmt == "xlsx":
        wb = load_workbook(path, read_only=True, data_only=True)
        data = list(wb["DATA"].iter_rows(values_only=True))
        headers = [_s(h) for h in data[0]]
        rows = [dict(zip(headers, (_s(v) for v in r))) for r in data[1:] if any(v is not None for v in r)]
        if "CONTROL" in wb.sheetnames:
            meta = {r[0]: r[1] for r in wb["CONTROL"].iter_rows(values_only=True)}
            ctrl_count, ctrl_total = int(meta["record_count"]), float(meta["total_amount"])
        wb.close()
    else:  # fixed width: no header, so the layout check is on line length
        widths = c["widths"][ftype]
        headers = [n for n, _ in widths]
        expected = sum(w for _, w in widths)
        rows, bad_len = [], 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("TRL"):
                ctrl_count, ctrl_total = int(line[3:12]), int(line[12:30]) / 100
                continue
            if len(line) != expected:
                bad_len += 1
            rec, pos = {}, 0
            for n, w in widths:
                rec[n] = line[pos:pos + w].strip()
                pos += w
            rows.append(rec)
        issues = [("LINE_LENGTH", f"{bad_len} lines")] if bad_len else []
        return ParsedFile(headers, rows, ctrl_count, ctrl_total, issues)

    issues = [("MISSING_COLUMN", m) for m in mapped if m not in headers]
    issues += [("UNEXPECTED_COLUMN", h) for h in headers if h not in mapped]
    return ParsedFile(headers, rows, ctrl_count, ctrl_total, issues)


def _date(v: str, fmt: str):
    try:
        return datetime.strptime(v, fmt).date() if v else None
    except ValueError:
        return None


def _num(v: str):
    try:
        return float(v) if v not in ("", None) else None
    except ValueError:
        return None


def standardize(parsed: ParsedFile, c: dict, ftype: str, meta: dict) -> tuple[pd.DataFrame, list[str]]:
    """Map raw rows to the standard schema. Returns the frame and the standard fields that were missing."""
    mapping = c["columns"][ftype]
    missing = [std for std, src in mapping.items() if src not in parsed.headers]
    mult = c.get("multipliers", {})
    out = []
    for i, raw in enumerate(parsed.rows, start=1):
        rec = {"file_id": meta["file_id"], "row_num": i, "cedant_id": meta["cedant_id"],
               "period": meta["period"]}
        for std, src in mapping.items():
            v = raw.get(src, "") if src in parsed.headers else None
            if std in DATE_FIELDS:
                rec[std] = _date(v, c["date_format"]) if v is not None else None
                rec[std + "_raw"] = v
            elif std in CODE_FIELDS:
                rec[std] = c["codes"][std].get(v) if v else None
                rec[std + "_raw"] = v
            elif std in NUM_FIELDS:
                n = _num(v) if v is not None else None
                rec[std] = None if n is None else round(n * mult.get(std, 1), 6)
            elif std in INT_FIELDS:
                n = _num(v) if v is not None else None
                rec[std] = None if n is None else int(n)
            else:
                rec[std] = v if v else None
        out.append(rec)
    df = pd.DataFrame(out)
    if df.empty:
        return df, missing
    df["period_start"] = period_start(meta["period"])
    df["period_end"] = period_end(meta["period"])
    if ftype == "claims":
        df["record_key"] = df["claim_id"]
    else:
        df["record_key"] = df["policy_id"].astype(str) + "|" + df["life_id"].astype("Int64").astype(str)
    return df, missing


def control_check(parsed: ParsedFile, df: pd.DataFrame, ftype: str) -> dict:
    amount = AMOUNT_FIELD[ftype]
    total = round(float(df[amount].fillna(0).sum()), 2) if not df.empty else 0.0
    return {"row_count": len(df), "amount_total": total,
            "control_count": parsed.control_count, "control_total": parsed.control_total}
