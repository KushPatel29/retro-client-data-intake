"""File intake: register every file that arrives, spot duplicates, late and missing files, and archive.

Mirrors scripts/Invoke-FileIntake.ps1, which does the same job with PowerShell on a Windows file share.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from .common import ARCHIVE, AS_OF, FILE_TYPES, INBOX, PERIODS, due_date


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def filename_patterns(cfg: dict) -> list[tuple[str, re.Pattern]]:
    pats = []
    for cid, c in cfg["clients"].items():
        rx = re.escape(c["filename"])
        rx = rx.replace(r"\{file_type\}", r"(?P<file_type>inforce|billing|claims)")
        rx = rx.replace(r"\{yyyymm\}", r"(?P<yyyymm>\d{6})")
        rx = rx.replace(re.escape(Path(c["filename"]).suffix), r"(?P<suffix>_[a-z0-9]+)?" + re.escape(Path(c["filename"]).suffix))
        pats.append((cid, re.compile("^" + rx + "$", re.IGNORECASE)))
    return pats


def register_files(cfg: dict, inbox: Path = INBOX) -> pd.DataFrame:
    """Return the file register: one row per data file found in the inbox, oldest arrival first."""
    pats = filename_patterns(cfg)
    rows = []
    for path in sorted(inbox.iterdir()):
        if path.suffix.lower() == ".ctl" or path.is_dir():
            continue
        st = path.stat()
        rec = {"file_name": path.name, "path": str(path), "bytes": st.st_size,
               "received_at": datetime.fromtimestamp(st.st_mtime), "sha256": sha256(path),
               "cedant_id": None, "file_type": None, "period": None}
        for cid, rx in pats:
            m = rx.match(path.name)
            if m:
                y = m.group("yyyymm")
                rec.update(cedant_id=cid, file_type=m.group("file_type").lower(), period=f"{y[:4]}-{y[4:]}")
                break
        rows.append(rec)
    reg = pd.DataFrame(rows).sort_values("received_at", kind="stable").reset_index(drop=True)
    reg.insert(0, "file_id", [f"F{i + 1:04d}" for i in range(len(reg))])

    reg["status"] = "RECEIVED"
    reg["duplicate_of"] = None
    first_by_hash: dict[str, str] = {}
    for i, r in reg.iterrows():
        if r["cedant_id"] is None:
            reg.at[i, "status"] = "UNRECOGNIZED"
            continue
        if r["sha256"] in first_by_hash:
            reg.at[i, "status"] = "REJECTED_DUPLICATE"
            reg.at[i, "duplicate_of"] = first_by_hash[r["sha256"]]
        else:
            first_by_hash[r["sha256"]] = r["file_id"]

    reg["due_date"] = [due_date(p, cfg["due_day"]) if p else None for p in reg["period"]]
    reg["days_late"] = [max(0, (r.date() - d).days) if d else 0
                        for r, d in zip(reg["received_at"], reg["due_date"])]
    return reg


def missing_files(cfg: dict, reg: pd.DataFrame) -> pd.DataFrame:
    """Files that were expected, are past their due date, and never arrived."""
    got = set(zip(reg["cedant_id"], reg["period"], reg["file_type"]))
    out = []
    for cid, c in cfg["clients"].items():
        for period in PERIODS:
            for ftype in FILE_TYPES:
                due = due_date(period, cfg["due_day"])
                if (cid, period, ftype) not in got and AS_OF > due:
                    out.append({"cedant_id": cid, "period": period, "file_type": ftype,
                                "expected_name": c["filename"].format(file_type=ftype, yyyymm=period.replace("-", "")),
                                "due_date": due, "days_overdue": (AS_OF - due).days})
    return pd.DataFrame(out)


def archive(reg: pd.DataFrame, root: Path = ARCHIVE) -> None:
    """Copy each file (and its control sidecar) to archive/<client>/<period>/, stamped with its arrival time."""
    if root.exists():
        shutil.rmtree(root)
    for _, r in reg.iterrows():
        sub = root / (r["cedant_id"] or "_unrecognized") / (r["period"] or "unknown")
        sub.mkdir(parents=True, exist_ok=True)
        stamp = r["received_at"].strftime("%Y%m%dT%H%M")
        src = Path(r["path"])
        for f in [src, src.with_suffix(".ctl")]:
            if f.exists():
                shutil.copy2(f, sub / f"{stamp}_{f.name}")
