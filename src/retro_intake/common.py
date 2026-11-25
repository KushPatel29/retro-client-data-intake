"""Shared configuration, constants and treaty rate logic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import math

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
INBOX = DATA_DIR / "inbox"
ARCHIVE = DATA_DIR / "archive"
OUTPUT = ROOT / "output"

FILE_TYPES = ("inforce", "billing", "claims")
PERIODS = ("2026-07", "2026-08", "2026-09")
AS_OF = date(2026, 10, 15)   # the day the control run happens

STATUSES = {"ACTIVE", "LAPSED", "DEATH", "CI_PAID"}
TERMINATED = {"LAPSED", "DEATH", "CI_PAID"}
TXN_TYPES = {"FIRST_YEAR", "RENEWAL", "REVERSAL"}
CLAIM_TYPES = {"DEATH", "CRITICAL_ILLNESS"}

AMOUNT_FIELD = {"inforce": "face_amount", "billing": "premium", "claims": "claim_amount"}


def load_config() -> dict:
    with open(CONFIG_DIR / "clients.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_rules() -> list[dict]:
    with open(CONFIG_DIR / "rules.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["rules"]


def period_end(period: str) -> date:
    y, m = (int(x) for x in period.split("-"))
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def period_start(period: str) -> date:
    y, m = (int(x) for x in period.split("-"))
    return date(y, m, 1)


def due_date(period: str, due_day: int) -> date:
    end = period_end(period)
    nxt = date.fromordinal(end.toordinal() + 1)
    return nxt.replace(day=due_day)


def age_last_birthday(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def treaty_monthly_premium(basis: dict, face: float, share: float, attained_age: int,
                           sex: str, smoker: str) -> float:
    """Expected monthly retro premium from the treaty rate basis (synthetic YRT-style table)."""
    rate = basis["base"] * math.exp(basis["slope"] * (attained_age - 30))
    if smoker == "S":
        rate *= basis["smoker"]
    if sex == "F":
        rate *= basis["female"]
    return round(face * share / 1000 * rate / 12, 2)


@dataclass(frozen=True)
class FileKey:
    cedant_id: str
    period: str
    file_type: str

    @property
    def yyyymm(self) -> str:
        return self.period.replace("-", "")
