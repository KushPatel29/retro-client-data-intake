"""Generate synthetic client (cedant) files for three monthly periods, with known defects.

Everything here is synthetic and fictional. The generator writes:
  data/inbox/                one file per client, period and file type, in each client's own layout,
                             with the file's modified time set to its simulated arrival time
  data/answer_key.csv        every defect injected on purpose, so tests can prove each one is caught
"""
from __future__ import annotations

import csv
import json
import os
import random
import shutil
from datetime import date, datetime, timedelta

from openpyxl import Workbook

from .common import (AMOUNT_FIELD, DATA_DIR, INBOX, PERIODS, age_last_birthday, load_config,
                     period_end, period_start, treaty_monthly_premium)

SEED = 20261127
BOOK_SIZE = {"C01": 3000, "C02": 4000, "C03": 1500, "C04": 2500}
PREFIX = {"C01": "NBL", "C02": "", "C03": "KR-", "C04": "AMR"}
PRODUCTS = {"C01": ["TERM20", "TERM10", "UL"], "C02": ["T20", "T30", "UL"],
            "C03": ["TERM", "CI"], "C04": ["TERM", "WOL", "CI"]}
BAD_DATE = {"C01": "2025-02-30", "C02": "02/30/2025", "C03": "30/02/2025", "C04": "20250230"}
BAD_SEX = {"C01": "X", "C02": "9", "C03": "UNKNOWN", "C04": "U"}

# Data defects: (client, period, file type) -> [(rule id, how many records)]
SCHEDULE = {
    ("C01", "2026-07", "inforce"): [("IF001", 4), ("IF005", 3)],
    ("C01", "2026-08", "inforce"): [("IF002", 3), ("IF009", 2)],
    ("C01", "2026-09", "inforce"): [("IF004", 2), ("IF006", 2), ("RF001", 3), ("RF002", 2)],
    ("C02", "2026-07", "inforce"): [("IF003", 3), ("IF008", 2)],
    ("C02", "2026-08", "inforce"): [("IF011", 2), ("RF003", 2), ("IF001", 2)],
    ("C02", "2026-09", "inforce"): [("IF006", 1), ("RF001", 2)],
    ("C03", "2026-07", "inforce"): [("IF009", 3)],
    ("C03", "2026-08", "inforce"): [("IF005", 2), ("RF002", 1), ("IF002", 2)],
    ("C03", "2026-09", "inforce"): [("IF003", 2), ("RF003", 1)],
    ("C04", "2026-07", "inforce"): [("IF004", 2), ("IF008", 1)],
    ("C04", "2026-08", "inforce"): [("IF001", 3), ("RF001", 2)],
    # C04 2026-09: inject enough critical errors to cross the 2% hold threshold
    ("C04", "2026-09", "inforce"): [("IF011", 60)],
    ("C01", "2026-07", "billing"): [("BL001", 2), ("BL005", 2)],
    ("C01", "2026-08", "billing"): [("BL004", 2)],
    ("C02", "2026-08", "billing"): [("BL002", 3), ("BL003", 2)],
    ("C02", "2026-07", "billing"): [("BL005", 3)],
    ("C03", "2026-09", "billing"): [("BL001", 1), ("BL003", 1)],
    ("C04", "2026-07", "billing"): [("BL002", 1), ("BL004", 1)],
    ("C01", "2026-08", "claims"): [("CL003", 1)],
    ("C02", "2026-07", "claims"): [("CL001", 1), ("CL002", 1)],
    ("C02", "2026-09", "claims"): [("CL004", 1)],
    ("C04", "2026-09", "claims"): [("CL001", 1), ("CL003", 1)],
}
# Unusual but valid business that warnings should catch, added as new business
NEW_BUSINESS_FLAGS = {("C01", "2026-08"): [("IF010", 1), ("IF007", 1)],
                      ("C03", "2026-07"): [("IF007", 1)],
                      ("C02", "2026-09"): [("IF010", 2)]}
# File-level problems
CONTROL_COUNT_OFF = {("C02", "2026-08", "billing"): 3}
CONTROL_TOTAL_OFF = {("C03", "2026-09", "inforce"): 12345.67}
SCHEMA_DRIFT = {("C02", "2026-09", "inforce"): ("TobaccoCd", "TobaccoUse")}
MISSING_FILES = {("C03", "2026-08", "claims")}
RESENT_FILES = {("C01", "2026-08", "billing")}
LATE_PERIODS = {("C04", "2026-08"): 14}
# Real business events (not data errors) that the Trends page should surface
LAPSE_SWEEP = {("C04", "2026-08"): 6}        # lapse rate x6: a block of policies lapsed in one month
NEW_BUSINESS_SURGE = {("C01", "2026-09"): 6}  # new business x6: a large group block added


class Raw(str):
    """A value written to the file exactly as given (used for deliberately bad values)."""


PROFILES = {
    "baseline": {"row_factor": 1, "file_problems": True},
    "heavy": {"row_factor": 3, "file_problems": True},
    "clean": {"row_factor": 0, "file_problems": False},
}


class Generator:
    def __init__(self, seed: int = SEED, inbox=None, key_path=None, profile: str = "baseline"):
        self.rng = random.Random(seed)
        self.cfg = load_config()
        self.key: list[dict] = []
        self.seq = {c: 0 for c in BOOK_SIZE}
        self.inbox = inbox or INBOX
        self.key_path = key_path or DATA_DIR / "answer_key.csv"
        prof = PROFILES[profile]
        f = prof["row_factor"]
        self.schedule = {k: [(r, n * f) for r, n in v if n * f > 0] for k, v in SCHEDULE.items()}
        self.new_flags = NEW_BUSINESS_FLAGS if f else {}
        on = prof["file_problems"]
        self.count_off = CONTROL_COUNT_OFF if on else {}
        self.total_off = CONTROL_TOTAL_OFF if on else {}
        self.drift = SCHEMA_DRIFT if on else {}
        self.missing = MISSING_FILES if on else set()
        self.resent = RESENT_FILES if on else set()
        self.late = LATE_PERIODS if on else {}
        self.sweep = LAPSE_SWEEP if on else {}
        self.surge = NEW_BUSINESS_SURGE if on else {}

    # ------------------------------------------------------------------ records
    def new_id(self, cid: str) -> str:
        self.seq[cid] += 1
        n = self.seq[cid]
        if cid == "C02":
            return str(400000000 + n * 7)
        if cid == "C03":
            return f"KR-{n:06d}"
        width = 7 if cid == "C01" else 8
        return f"{PREFIX[cid]}{n:0{width}d}"

    def new_policy(self, cid: str, issue_from: date, issue_to: date, age: int | None = None,
                   face: float | None = None) -> dict:
        r, c = self.rng, self.cfg["clients"][cid]
        span = (issue_to - issue_from).days
        issue = issue_from + timedelta(days=r.randint(0, max(span, 0)))
        if age is None:
            age = r.randint(max(20, c["min_issue_age"]), min(68, c["max_issue_age"]))
        dob = issue - timedelta(days=int(age * 365.25) + r.randint(30, 330))
        age = age_last_birthday(dob, issue)
        if face is None:
            face = min(round(r.lognormvariate(12.9, 0.7), -3), c["jumbo_limit"] * 0.6)
            face = max(face, 50000.0)
        return {
            "policy_id": self.new_id(cid), "life_id": 2 if r.random() < 0.03 else 1,
            "insured_dob": dob, "sex": r.choice("MF"), "smoker": "S" if r.random() < 0.12 else "N",
            "issue_date": issue, "issue_age": age, "face_amount": float(face),
            "retro_share": c["retro_share_default"], "status": "ACTIVE",
            "currency": c["currency"], "product": r.choice(PRODUCTS[cid]), "_flags": set(),
        }

    def add_key(self, cid, period, ftype, rule, rec=None):
        self.key.append({"cedant_id": cid, "period": period, "file_type": ftype, "rule_id": rule,
                         "record_key": "" if rec is None else rec})

    @staticmethod
    def pk(p: dict) -> str:
        return f"{p['policy_id']}|{p['life_id']}"

    # ------------------------------------------------------------------ main
    def run(self) -> None:
        if self.inbox.exists():
            shutil.rmtree(self.inbox)
        self.inbox.mkdir(parents=True)
        for cid in BOOK_SIZE:
            self.run_client(cid)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.key_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["cedant_id", "period", "file_type", "rule_id", "record_key"])
            w.writeheader()
            w.writerows(self.key)

    def run_client(self, cid: str) -> None:
        r, c = self.rng, self.cfg["clients"][cid]
        book = [self.new_policy(cid, date(2008, 1, 1), date(2026, 5, 31)) for _ in range(BOOK_SIZE[cid])]
        claim_seq = 0
        for t, period in enumerate(PERIODS):
            p0, p1 = period_start(period), period_end(period)
            active = [p for p in book if p["status"] == "ACTIVE"]
            claims = []
            r.shuffle(active)
            n_lapse = max(1, int(len(active) * 0.006 * self.sweep.get((cid, period), 1)))
            n_death = max(1, int(len(active) * 0.0012))
            for p in active[:n_lapse]:
                p["status"] = "LAPSED"
            for p in active[n_lapse:n_lapse + n_death]:
                ci = p["product"] == "CI" and r.random() < 0.6
                p["status"] = "CI_PAID" if ci else "DEATH"
                claim_seq += 1
                claims.append(self.claim(cid, claim_seq, p, p0, p1, "CRITICAL_ILLNESS" if ci else "DEATH"))
            # new business issued in this period
            n_new = max(1, int(len(active) * 0.01 * self.surge.get((cid, period), 1)))
            new = [self.new_policy(cid, p0, p1) for _ in range(n_new)]
            for rule, n in self.new_flags.get((cid, period), []):
                for _ in range(n):
                    if rule == "IF010":
                        p = self.new_policy(cid, p0, p1, age=c["max_issue_age"] + 3)
                    else:  # IF007 jumbo face
                        p = self.new_policy(cid, p0, p1, face=round(c["jumbo_limit"] * 1.6, -3))
                    p["_flags"].add(rule)
                    new.append(p)
            book.extend(new)
            # roll-forward problems (only from the second period on)
            dropped, sched = set(), dict(self.schedule.get((cid, period, "inforce"), []))
            stable = [p for p in active[n_lapse + n_death:] if not p["_flags"]]
            pick = iter(stable)
            if t > 0:
                for _ in range(sched.pop("RF001", 0)):        # disappears without a termination
                    p = next(pick)
                    p["status"] = "DROPPED"
                    dropped.add(self.pk(p))
                    self.add_key(cid, period, "inforce", "RF001", self.pk(p))
                for _ in range(sched.pop("RF002", 0)):        # face amount jumps by 3x
                    p = next(pick)
                    while p["face_amount"] * 3 > c["jumbo_limit"]:
                        p = next(pick)
                    p["face_amount"] *= 3
                    self.add_key(cid, period, "inforce", "RF002", self.pk(p))
                for _ in range(sched.pop("RF003", 0)):        # appears late, issued long ago
                    p = self.new_policy(cid, date(2019, 1, 1), date(2024, 12, 31))
                    book.append(p)
                    self.add_key(cid, period, "inforce", "RF003", self.pk(p))
            inforce = [p for p in book if p["status"] != "DROPPED"
                       and p["issue_date"] <= p1 and not p.get("_gone")]
            for p in inforce:
                for flag in p["_flags"]:
                    self.add_key(cid, period, "inforce", flag, self.pk(p))
            billing = self.billing(cid, inforce, p1)
            rows = [dict(p) for p in inforce]
            rows = self.inforce_defects(cid, period, rows, sched, used={k for k in dropped})
            billing = self.billing_defects(cid, period, billing, inforce)
            claims = self.claim_defects(cid, period, claims, inforce, claim_seq)
            claim_seq += 10
            for ftype, data in (("inforce", rows), ("billing", billing), ("claims", claims)):
                self.write_file(cid, period, ftype, data)
            # terminated and dropped policies leave the book
            for p in book:
                if p["status"] in ("LAPSED", "DEATH", "CI_PAID", "DROPPED"):
                    p["_gone"] = True

    # ------------------------------------------------------------------ files
    def claim(self, cid, seq, p, p0, p1, ctype):
        d = p0 + timedelta(days=self.rng.randint(0, (p1 - p0).days))
        return {"claim_id": f"{cid}C{seq:06d}", "policy_id": p["policy_id"], "life_id": p["life_id"],
                "date_of_event": d, "claim_type": ctype,
                "claim_amount": round(p["face_amount"] * p["retro_share"], 2), "currency": p["currency"]}

    def billing(self, cid, inforce, p1):
        basis = self.cfg["clients"][cid]["rate_basis"]
        out = []
        for p in inforce:
            prem = treaty_monthly_premium(basis, p["face_amount"], p["retro_share"],
                                          age_last_birthday(p["insured_dob"], p1), p["sex"], p["smoker"])
            first = (p1 - p["issue_date"]).days < 365
            if p["status"] == "ACTIVE":
                out.append({"policy_id": p["policy_id"], "life_id": p["life_id"], "premium": prem,
                            "transaction_type": "FIRST_YEAR" if first else "RENEWAL", "currency": p["currency"]})
            elif p["status"] == "LAPSED" and self.rng.random() < 0.3:
                out.append({"policy_id": p["policy_id"], "life_id": p["life_id"], "premium": -prem,
                            "transaction_type": "REVERSAL", "currency": p["currency"]})
        return out

    def inforce_defects(self, cid, period, rows, sched, used):
        r = self.rng
        candidates = [x for x in rows if x["status"] == "ACTIVE" and not x["_flags"]
                      and (period_end(period) - x["issue_date"]).days > 400]
        r.shuffle(candidates)
        pool = iter(x for x in candidates if self.pk(x) not in used)
        for rule, n in sched.items():
            for _ in range(n):
                x = next(pool)
                used.add(self.pk(x))
                self.add_key(cid, period, "inforce", rule, self.pk(x))
                if rule == "IF001":
                    x["insured_dob"] = None
                elif rule == "IF002":
                    rows.append(dict(x))
                elif rule == "IF003":
                    x["issue_date"] = Raw(BAD_DATE[cid])
                elif rule == "IF004":
                    x["insured_dob"] = x["issue_date"] + timedelta(days=700)
                elif rule == "IF005":
                    x["issue_age"] += 5
                elif rule == "IF006":
                    x["face_amount"] = 0.0
                elif rule == "IF008":
                    x["retro_share"] = 1.5
                elif rule == "IF009":
                    x["sex"] = Raw(BAD_SEX[cid])
                elif rule == "IF011":
                    x["currency"] = "EUR"
        return rows

    def billing_defects(self, cid, period, billing, inforce):
        r = self.rng
        sched = self.schedule.get((cid, period, "billing"), [])
        normal = [b for b in billing if b["transaction_type"] != "REVERSAL"]
        r.shuffle(normal)
        pool, ccy = iter(normal), self.cfg["clients"][cid]["currency"]
        billed = {(b["policy_id"], b["life_id"]) for b in billing}
        lapsed = [p for p in inforce if p["status"] == "LAPSED" and (p["policy_id"], p["life_id"]) not in billed]
        lapsed = [p for p in inforce if p["status"] in ("DEATH", "CI_PAID")
                  and (p["policy_id"], p["life_id"]) not in billed] + lapsed   # pop() takes lapsed ones first
        for rule, n in sched:
            for _ in range(n):
                if rule == "BL001":
                    pid = self.new_id(cid)
                    billing.append({"policy_id": pid, "life_id": 1, "premium": 88.15,
                                    "transaction_type": "RENEWAL", "currency": ccy})
                    self.add_key(cid, period, "billing", rule, f"{pid}|1")
                    continue
                if rule == "BL003":
                    if not lapsed:
                        continue
                    p = lapsed.pop()
                    prem = treaty_monthly_premium(self.cfg["clients"][cid]["rate_basis"], p["face_amount"],
                                                  p["retro_share"], age_last_birthday(p["insured_dob"], period_end(period)),
                                                  p["sex"], p["smoker"])
                    billing.append({"policy_id": p["policy_id"], "life_id": p["life_id"], "premium": prem,
                                    "transaction_type": "RENEWAL", "currency": ccy})
                    self.add_key(cid, period, "billing", rule, self.pk(p))
                    continue
                b = next(pool)
                self.add_key(cid, period, "billing", rule, self.pk(b))
                if rule == "BL002":
                    b["premium"] = -abs(b["premium"])
                elif rule == "BL004":
                    billing.append(dict(b))
                elif rule == "BL005":
                    b["premium"] = round(b["premium"] * 20, 2)
        return billing

    def claim_defects(self, cid, period, claims, inforce, seq):
        sched = self.schedule.get((cid, period, "claims"), [])
        p0, p1 = period_start(period), period_end(period)
        actives = [p for p in inforce if p["status"] == "ACTIVE" and not p["_flags"] and p["issue_date"] < p0]
        for rule, n in sched:
            for _ in range(n):
                seq += 1
                if rule == "CL001":
                    c = {"claim_id": f"{cid}C{seq:06d}", "policy_id": self.new_id(cid), "life_id": 1,
                         "date_of_event": p0 + timedelta(days=3), "claim_type": "DEATH",
                         "claim_amount": 125000.0, "currency": self.cfg["clients"][cid]["currency"]}
                    claims.append(c)
                elif rule == "CL004":
                    p = actives.pop()
                    c = self.claim(cid, seq, p, p0, p1, "DEATH")
                    claims.append(c)
                else:
                    used = {k["record_key"] for k in self.key}
                    c = next((x for x in claims if x["claim_id"] not in used), None)
                    if c is None:                    # not enough claims this month for the planted case
                        continue
                    if rule == "CL002":
                        pol = next(p for p in inforce if p["policy_id"] == c["policy_id"])
                        c["date_of_event"] = pol["issue_date"] - timedelta(days=45)
                    elif rule == "CL003":
                        c["claim_amount"] = round(c["claim_amount"] * 3, 2)
                self.add_key(cid, period, "claims", rule, c["claim_id"])
        return claims

    # ------------------------------------------------------------------ writing
    def raw_value(self, cid, field, value):
        c = self.cfg["clients"][cid]
        if value is None:
            return ""
        if isinstance(value, Raw):
            return str(value)
        if isinstance(value, date):
            return value.strftime(c["date_format"])
        if field in c["codes"]:
            inverse = {}
            for raw, std in c["codes"][field].items():
                inverse.setdefault(std, raw)
            return inverse[value]
        mult = c["multipliers"].get(field)
        if mult is not None:
            v = value / mult
            if c["format"] == "fixed" or field == "face_amount":
                return int(round(v))
            return round(v, 4)
        return value

    def write_file(self, cid, period, ftype, rows):
        key = (cid, period, ftype)
        if key in self.missing:
            self.add_key(cid, period, ftype, "FL004")
            return
        c = self.cfg["clients"][cid]
        mapping = c["columns"][ftype]
        headers = list(mapping.values())
        if key in self.drift:
            old, new = self.drift[key]
            headers = [new if h == old else h for h in headers]
            self.add_key(cid, period, ftype, "FL003")
            self.add_key(cid, period, ftype, "FL007")   # the renamed column also shows up as unexpected
        table = [[self.raw_value(cid, f, row.get(f)) for f in mapping] for row in rows]
        count = len(rows) + self.count_off.get(key, 0)
        total = round(sum(float(row[AMOUNT_FIELD[ftype]] or 0) for row in rows), 2)
        total = round(total + self.total_off.get(key, 0), 2)
        if key in self.count_off:
            self.add_key(cid, period, ftype, "FL001")
        if key in self.total_off:
            self.add_key(cid, period, ftype, "FL002")
        name = c["filename"].format(file_type=ftype, yyyymm=period.replace("-", ""))
        path = self.inbox / name
        fmt = c["format"]
        if fmt in ("csv", "pipe"):
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh, delimiter=c["delimiter"])
                w.writerow(headers)
                w.writerows(table)
                if c["control"] == "trailer":
                    w.writerow(["TRL", count, f"{total:.2f}"])
            if c["control"] == "sidecar":
                with open(path.with_suffix(".ctl"), "w", encoding="utf-8") as fh:
                    json.dump({"file": name, "record_count": count, "total_amount": total}, fh)
        elif fmt == "xlsx":
            wb = Workbook()
            ws = wb.active
            ws.title = "DATA"
            ws.append(headers)
            for row in table:
                ws.append(row)
            cs = wb.create_sheet("CONTROL")
            cs.append(["record_count", count])
            cs.append(["total_amount", total])
            wb.save(path)
        else:  # fixed width
            widths = dict((n, w) for n, w in c["widths"][ftype])
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                for row in table:
                    line = ""
                    for h, v in zip(mapping.values(), row):
                        w = widths[h]
                        line += (f"{v:0{w}d}" if isinstance(v, int) else str(v).ljust(w))[:w]
                    fh.write(line + "\n")
                fh.write(f"TRL{count:09d}{int(round(total * 100)):018d}\n")
        arrival = self.arrival(cid, period)
        for p in [path, path.with_suffix(".ctl")]:
            if p.exists():
                os.utime(p, (arrival, arrival))
        if key in self.resent:
            dup = path.with_name(path.stem + "_resend" + path.suffix)
            shutil.copyfile(path, dup)
            later = arrival + 2 * 86400
            os.utime(dup, (later, later))
            self.add_key(cid, period, ftype, "FL005")
        if (cid, period) in self.late:
            self.add_key(cid, period, ftype, "FL006")

    def arrival(self, cid, period) -> float:
        end = period_end(period)
        day = self.late.get((cid, period)) or self.rng.randint(2, 9)
        dt = datetime(end.year + (end.month == 12), end.month % 12 + 1, day,
                      self.rng.randint(6, 20), self.rng.randint(0, 59))
        return dt.timestamp()


def main(seed: int = SEED, inbox=None, key_path=None, profile: str = "baseline", quiet: bool = False) -> Generator:
    g = Generator(seed, inbox, key_path, profile)
    g.run()
    if not quiet:
        print(f"Wrote {len(list(g.inbox.iterdir()))} files to {g.inbox} and {len(g.key)} answer-key rows")
    return g


if __name__ == "__main__":
    main()
