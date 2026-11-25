"""GenAI helpers, each with a transparent offline method and an optional Claude API method.

Offline (default, no key needed):
  - mapping assistant: profiles a new client file and scores each column against the standard fields
  - ask the data: turns common questions into SQL with a small intent parser
  - ask the runbook: BM25 retrieval over the runbook and rule catalogue, answer built from cited passages
  - exception triage: summary and likely causes from a knowledge base of known patterns
  - follow-up tone: rewrites the drafted email in a chosen tone

With ANTHROPIC_API_KEY set, the same features can call Claude. Every model output is shown to a person
first; generated SQL goes through safesql (read-only, SELECT only, row limit).
"""
from __future__ import annotations

import difflib
import json
import math
import os
import re
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

from .common import ROOT, load_rules

DEFAULT_MODEL = os.environ.get("RETRO_AI_MODEL", "claude-haiku-5-5")


# ------------------------------------------------------------------------------------- model access
def api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        import streamlit as st
        return st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return None


def llm_available() -> bool:
    return bool(api_key())


def complete(system: str, prompt: str, max_tokens: int = 900, model: str = DEFAULT_MODEL) -> str:
    """One call to the Claude Messages API. Raises on any error so the caller can fall back."""
    body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
        "x-api-key": api_key() or "", "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read())
    return "".join(b.get("text", "") for b in data.get("content", []))


# ------------------------------------------------------------------------------------- mapping assistant
STD_INFORCE = ["policy_id", "life_id", "insured_dob", "sex", "smoker", "issue_date", "issue_age",
               "face_amount", "retro_share", "status", "currency", "product"]
SYNONYMS = {
    "policy_id": ["policy number", "policy no", "pol no", "policy ref", "cert no", "certificate", "contract", "policy id"],
    "life_id": ["life", "life no", "insured seq", "life id", "insured number", "coverage", "seq"],
    "insured_dob": ["date of birth", "dob", "birth date", "birth dt", "birthdate"],
    "sex": ["sex", "gender", "gender code"],
    "smoker": ["smoker", "tobacco", "smoker status", "smoking", "tobacco use", "nicotine"],
    "issue_date": ["issue date", "effective date", "eff dt", "commencement date", "issue dt", "start date", "inception"],
    "issue_age": ["issue age", "age at issue", "entry age", "iss age", "age"],
    "face_amount": ["face amount", "sum assured", "amount insured", "face", "benefit amount", "sum insured", "coverage amount"],
    "retro_share": ["retro share", "ceded pct", "retro pct", "share", "quota share", "ceded share", "retro percent"],
    "status": ["status", "policy status", "pol stat", "state"],
    "currency": ["currency", "ccy", "curr", "currency code"],
    "product": ["product", "plan", "plan code", "benefit", "product code"],
}
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d", "%Y/%m/%d", "%d-%b-%Y", "%d.%m.%Y"]
CODE_GUESS = {
    "sex": {"M": "M", "MALE": "M", "1": "M", "F": "F", "FEMALE": "F", "2": "F"},
    "smoker": {"S": "S", "Y": "S", "YES": "S", "1": "S", "SM": "S", "T": "S", "SMOKER": "S",
               "N": "N", "NO": "N", "0": "N", "NS": "N", "NON-SMOKER": "N", "NONSMOKER": "N"},
    "status": {"A": "ACTIVE", "IF": "ACTIVE", "INFORCE": "ACTIVE", "ACTIVE": "ACTIVE", "ACT": "ACTIVE",
               "L": "LAPSED", "LA": "LAPSED", "LAPSE": "LAPSED", "LAPSED": "LAPSED",
               "D": "DEATH", "DC": "DEATH", "DEATH": "DEATH", "DECEASED": "DEATH", "C": "CI_PAID", "CI": "CI_PAID"},
}


def _norm(h: str) -> str:
    h = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(h))
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


def _name_score(header: str, field: str) -> float:
    h = _norm(header)
    best = 0.0
    for syn in SYNONYMS[field] + [field.replace("_", " ")]:
        r = difflib.SequenceMatcher(None, h, syn).ratio()
        toks = set(syn.split())
        overlap = len(toks & set(h.split())) / len(toks)
        best = max(best, r, overlap * 0.95)
    return best


def _profile(values: list[str]) -> dict:
    vals = [str(v).strip() for v in values if str(v).strip() != ""]
    p = {"n": len(vals), "distinct": len(set(vals)), "sample": list(dict.fromkeys(vals))[:6]}
    if not vals:
        return p
    for fmt in DATE_FORMATS:
        ok, years = 0, []
        for v in vals[:200]:
            try:
                years.append(datetime.strptime(v, fmt).year)
                ok += 1
            except ValueError:
                pass
        rate = ok / min(len(vals), 200)
        if rate >= 0.95 and rate > p.get("date_rate", 0):
            p.update(date_format=fmt, date_rate=rate, median_year=sorted(years)[len(years) // 2])
    nums = []
    for v in vals[:500]:
        try:
            nums.append(float(v.replace(",", "")))
        except ValueError:
            pass
    if len(nums) >= 0.95 * min(len(vals), 500) and "date_format" not in p:
        nums.sort()
        p.update(numeric=True, nmin=nums[0], nmax=nums[-1], median=nums[len(nums) // 2],
                 integers=all(x.is_integer() for x in nums))
    p["upper_codes"] = {v.upper() for v in vals}
    return p


def _type_fit(field: str, p: dict) -> tuple[float, str]:
    if not p.get("n"):
        return 0.0, "column is empty"
    codes = p.get("upper_codes", set())
    if field in ("insured_dob", "issue_date"):
        if "date_format" not in p:
            return 0.0, "not a date"
        y = p["median_year"]
        if field == "insured_dob":
            return (1.0, f"dates, median year {y}") if y < 1995 else (0.3, f"dates, but median year {y}")
        return (1.0, f"dates, median year {y}") if y >= 1995 else (0.3, f"dates, but median year {y}")
    if field in CODE_GUESS:
        if p["distinct"] <= 8:
            known = sum(1 for c in codes if c in CODE_GUESS[field]) / max(1, len(codes))
            return known, f"{p['distinct']} distinct codes, {known:.0%} recognised"
        return 0.0, "too many distinct values for a code"
    if field == "currency":
        ok = all(re.fullmatch(r"[A-Z]{3}", c) for c in codes) and p["distinct"] <= 5
        return (1.0, "3-letter currency codes") if ok else (0.0, "not currency codes")
    if not p.get("numeric"):
        if field in ("policy_id", "product"):
            uniq = p["distinct"] / p["n"]
            if field == "policy_id":
                return (1.0 if uniq > 0.9 else 0.2), f"{uniq:.0%} unique text values"
            return (0.8 if p["distinct"] <= 20 else 0.2), f"{p['distinct']} distinct values"
        return 0.0, "not numeric"
    lo, hi, med = p["nmin"], p["nmax"], p["median"]
    if field == "policy_id":
        uniq = p["distinct"] / p["n"]
        return (0.9 if uniq > 0.9 and p["integers"] and med > 1000 else 0.1), f"{uniq:.0%} unique numbers"
    if field == "life_id":
        return (1.0 if p["integers"] and lo >= 0 and hi <= 9 else 0.0), f"integers {lo:g} to {hi:g}"
    if field == "issue_age":
        return (1.0 if p["integers"] and 0 <= lo and hi <= 110 and med > 15 else 0.0), f"integers {lo:g} to {hi:g}"
    if field == "retro_share":
        if 0 < lo and hi <= 1:
            return 1.0, f"fractions {lo:g} to {hi:g}"
        if 1 < med <= 100 and hi <= 100:
            return 0.9, f"percentages {lo:g} to {hi:g}"
        if 100 < med <= 1000 and hi <= 1000:
            return 0.6, f"per-mille values {lo:g} to {hi:g}"
        return 0.0, f"values {lo:g} to {hi:g}"
    if field == "face_amount":
        if med >= 10000:
            return 1.0, f"amounts, median {med:,.0f}"
        if 10 <= med < 10000:
            return 0.7, f"amounts, median {med:,.0f} (thousands?)"
        return 0.0, f"median {med:,.0f}"
    return 0.0, ""


def suggest_mapping(headers: list[str], rows: list[dict]) -> dict:
    """Suggest source column, date format, code map and multiplier for each standard inforce field."""
    profiles = {h: _profile([r.get(h, "") for r in rows]) for h in headers}
    cands = []
    for f in STD_INFORCE:
        for h in headers:
            ns = _name_score(h, f)
            ts, why = _type_fit(f, profiles[h])
            score = 0.45 * ns + 0.55 * ts
            cands.append((score, f, h, ns, ts, why))
    cands.sort(reverse=True)
    used_f, used_h, out = set(), set(), {}
    for score, f, h, ns, ts, why in cands:
        if f in used_f or h in used_h or score < 0.35:
            continue
        used_f.add(f)
        used_h.add(h)
        out[f] = {"source": h, "confidence": round(score, 2), "name_match": round(ns, 2),
                  "values": why, "sample": profiles[h]["sample"]}
    for f in STD_INFORCE:
        out.setdefault(f, {"source": None, "confidence": 0.0, "name_match": 0.0, "values": "no column matched", "sample": []})
    dates = [profiles[out[f]["source"]].get("date_format") for f in ("insured_dob", "issue_date") if out[f]["source"]]
    date_fmt = dates[0] if dates else "%Y-%m-%d"
    codes = {}
    for f, guess in CODE_GUESS.items():
        src = out[f]["source"]
        if src:
            seen = sorted(profiles[src].get("upper_codes", set()))
            codes[f] = {c: guess.get(c.upper()) for c in seen}
    mult = {"face_amount": 1, "retro_share": 1}
    if out["face_amount"]["source"]:
        med = profiles[out["face_amount"]["source"]].get("median", 0)
        if med and med < 10000:
            mult["face_amount"] = 1000
    if out["retro_share"]["source"]:
        med = profiles[out["retro_share"]["source"]].get("median", 0)
        mult["retro_share"] = 1 if med <= 1 else (0.01 if med <= 100 else 0.001)
    return {"fields": out, "date_format": date_fmt, "codes": codes, "multipliers": mult}


def mapping_to_config(sug: dict, base: dict) -> dict:
    """Turn a reviewed suggestion into a client configuration entry (same shape as clients.yaml)."""
    cfg = dict(base)
    cfg["columns"] = {"inforce": {f: v["source"] for f, v in sug["fields"].items() if v["source"]}}
    cfg["date_format"] = sug["date_format"]
    raw_codes = {}
    for f, m in sug["codes"].items():
        raw_codes[f] = {}
        for raw, std in m.items():
            if std:
                raw_codes[f][raw] = std
                raw_codes[f][raw.title()] = std
                raw_codes[f][raw.lower()] = std
    cfg["codes"] = raw_codes
    cfg["multipliers"] = sug["multipliers"]
    return cfg


# ------------------------------------------------------------------------------------- ask the data
CLIENT_WORDS = {"northbridge": "C01", "nbl": "C01", "harbourline": "C02", "harbour": "C02", "hrl": "C02",
                "kestrel": "C03", "atlantic": "C04", "amr": "C04"}
MONTH_WORDS = {"july": "2026-07", "jul": "2026-07", "august": "2026-08", "aug": "2026-08",
               "september": "2026-09", "sept": "2026-09", "sep": "2026-09"}
EXAMPLES = [
    "Which files were held, and why?",
    "Which checks found the most problems?",
    "Show critical exceptions for Harbourline in September",
    "Which files arrived late?",
    "What is the quality score for each client by month?",
    "How much premium was loaded per client and month?",
    "Show the inforce roll-forward for Atlantic Mutual",
    "Which issues keep coming back?",
]


def _filters(q: str, alias: str = "", period: bool = True) -> list[str]:
    a = f"{alias}." if alias else ""
    ql = q.lower()
    w = []
    clients = sorted({v for k, v in CLIENT_WORDS.items() if re.search(rf"\b{k}\b", ql)} |
                     set(re.findall(r"\bc0[1-4]\b", ql.upper().lower())))
    clients = [c.upper() for c in clients]
    if clients:
        w.append(f"{a}cedant_id IN ({', '.join(repr(c) for c in clients)})")
    months = sorted({v for k, v in MONTH_WORDS.items() if re.search(rf"\b{k}\b", ql)} | set(re.findall(r"2026-0[789]", ql)))
    if months and period:
        w.append(f"{a}period IN ({', '.join(repr(m) for m in months)})")
    return w


def ask_data_offline(q: str) -> tuple[str, str]:
    """Return (sql, explanation) for common questions; transparent keyword intents, no model."""
    ql = q.lower()
    types = [t for t in ("inforce", "billing", "claims") if t in ql or (t == "claims" and "claim" in ql)]
    sev = "CRITICAL" if "critical" in ql else ("WARNING" if "warning" in ql else None)

    def where(extra=None, alias="", ft=True, per=True):
        w = _filters(q, alias, per) + (extra or [])
        if types and ft:
            w.append(f"{alias + '.' if alias else ''}file_type IN ({', '.join(repr(t) for t in types)})")
        return (" WHERE " + " AND ".join(w)) if w else ""

    m = re.search(r"\b(NBL\d{7}|AMR\d{8}|KR-\d{6}|4\d{8}|C0\dC\d{6})\b", q, re.IGNORECASE)
    if m:
        k = m.group(1).upper()
        return (f"SELECT cedant_id, period, file_type, rule_id, rule_name, severity, record_key, detail\n"
                f"FROM exceptions WHERE record_key LIKE '{k}%' ORDER BY period"), f"Every exception for record {k}."
    if any(w in ql for w in ("held", "hold", "rejected", "not load", "decision")) and "back" not in ql:
        return ("SELECT cedant_id, period, file_type, file_name, decision, decision_reason\nFROM mart_file_scorecard"
                + where(["decision IN ('HOLD', 'MISSING', 'REJECTED')"]) + "\nORDER BY cedant_id, period"),\
            "Files that were held, missing or rejected, with the reason."
    if "late" in ql or "timel" in ql:
        return ("SELECT cedant_id, period, file_type, file_name, due_date, received_at, days_late\nFROM mart_file_scorecard"
                + where(["days_late > 0"]) + "\nORDER BY days_late DESC"), "Files received after the due date (or still missing)."
    if "missing" in ql and "field" not in ql:
        return "SELECT * FROM missing_file" + where(), "Expected files that never arrived."
    if "score" in ql or "quality" in ql:
        return ("SELECT cedant_name, period, quality_score, files_held, files_missing, files_late, rows_held_back\n"
                "FROM mart_client_scorecard" + where(ft=False) + "\nORDER BY cedant_name, period"), "Client quality score by month."
    if "premium" in ql or "face" in ql or "ceded" in ql or "portfolio" in ql:
        return ("SELECT cedant_id, period, currency, policies_loaded, round(ceded_amount) AS ceded_amount,\n"
                "       round(premium_loaded, 2) AS premium_loaded, round(claims_loaded, 2) AS claims_loaded\n"
                "FROM mart_portfolio" + where(ft=False) + "\nORDER BY cedant_id, period"), "Loaded portfolio figures (GO files only)."
    if "roll" in ql or "opening" in ql or "closing" in ql or "movement" in ql:
        return "SELECT * FROM mart_rollforward" + where(ft=False) + "\nORDER BY cedant_id, period", "Inforce roll-forward."
    if "recurr" in ql or "again" in ql or "keep" in ql or "repeat" in ql:
        return "SELECT * FROM mart_recurring_issues" + where(ft=False, per=False), "Checks that failed for the same client in two or more months."
    if any(w in ql for w in ("most", "top", "common", "which check", "which rule", "by rule", "how many")):
        extra = [f"severity = '{sev}'"] if sev else []
        return ("SELECT rule_id, rule_name, severity, count(*) AS exceptions, count(DISTINCT cedant_id) AS clients\n"
                "FROM exceptions" + where(extra) + "\nGROUP BY ALL ORDER BY exceptions DESC"), "Exceptions counted by check."
    extra = [f"severity = '{sev}'"] if sev else []
    return ("SELECT cedant_id, period, file_type, severity, rule_name, record_key, detail\nFROM exceptions"
            + where(extra) + "\nORDER BY cedant_id, period, severity, rule_id"), "Exceptions matching the client, month, file and severity you named."


def ask_data_llm(q: str, schema_text: str) -> tuple[str, str]:
    system = ("You write DuckDB SQL for a reinsurance client-data quality warehouse. Reply with JSON only: "
              '{"sql": "...", "explanation": "one sentence"}. One SELECT statement, no comments, no DDL or DML. '
              "Client ids: C01 Northbridge Life Re, C02 Harbourline Reinsurance, C03 Kestrel Re Asia, "
              "C04 Atlantic Mutual Re. Periods look like '2026-09'.")
    txt = complete(system, f"Schema:\n{schema_text}\n\nQuestion: {q}")
    m = re.search(r"\{.*\}", txt, re.S)
    data = json.loads(m.group(0))
    return data["sql"], data.get("explanation", "")


# ------------------------------------------------------------------------------------- ask the runbook (RAG)
def _tokens(s: str) -> list[str]:
    stop = {"the", "a", "an", "and", "or", "of", "to", "is", "in", "for", "on", "it", "be", "what", "how", "when",
            "do", "we", "i", "if", "with", "are", "by", "as", "at", "that", "this", "from", "does", "should", "our"}
    toks = [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in stop]
    return [t[:-1] if len(t) > 4 and t.endswith("s") and not t.endswith("ss") else t for t in toks]


class Runbook:
    def __init__(self):
        self.docs = []
        text = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
        for block in re.split(r"\n## ", text)[1:]:
            title, _, body = block.partition("\n")
            self.docs.append({"id": f"RB-{len(self.docs) + 1}", "source": "Runbook", "title": title.strip(),
                              "text": body.strip()})
        for r in load_rules():
            self.docs.append({"id": r["id"], "source": "Rule catalogue", "title": f"{r['id']} {r['name']}",
                              "text": f"{r['name']}. Severity {r['severity'].lower()}, {r['dimension'].lower()} check "
                                      f"on {r['file_type']} files."})
        self.tf = [Counter(_tokens(d["title"] + " " + d["text"])) for d in self.docs]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = sum(self.len) / len(self.len)
        df = Counter(t for tf in self.tf for t in tf)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    EXPAND = {"keeps": ["recurring", "month"], "again": ["recurring"], "repeatedly": ["recurring"],
              "repeat": ["recurring"], "keep": ["retention"], "store": ["retention"], "long": ["retention"]}

    def search(self, q: str, k: int = 3) -> list[dict]:
        words = re.findall(r"[a-z]+", q.lower())
        extra = [e for w in words for e in self.EXPAND.get(w, [])]
        if "keeps" in words:
            words = [w for w in words if w != "keeps"]
        qt = _tokens(" ".join(words + extra))
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in qt:
                if t in tf:
                    f = tf[t]
                    s += self.idf.get(t, 0) * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * self.len[i] / self.avg))
            scores.append(s * (0.6 if self.docs[i]["source"] == "Rule catalogue" else 1.0))
        order = sorted(range(len(self.docs)), key=lambda i: -scores[i])[:k]
        return [{**self.docs[i], "score": round(scores[i], 2)} for i in order if scores[i] > 0]

    def answer_offline(self, q: str, hits: list[dict]) -> str:
        qt = set(_tokens(q))
        picked = []
        for n, h in enumerate(hits, start=1):
            for sent in re.split(r"(?<=[.!?])\s+", h["text"]):
                overlap = len(qt & set(_tokens(sent)))
                if overlap:
                    picked.append((overlap, n, sent))
        picked.sort(key=lambda x: (x[1], -x[0]))
        first = [p for p in picked if p[1] == 1][:3]
        top = hits[0].get("score", 0) if hits else 0
        close = {n for n, h in enumerate(hits, start=1) if n > 1 and h.get("score", 0) >= 0.85 * top}
        rest = [p for p in picked if p[1] in close and p[0] >= 2][:1]
        lines = [f"{s} [{n}]" for _, n, s in first + rest]
        return " ".join(lines) if lines else "The runbook does not cover this. Ask the Client Administration manager."

    def answer_llm(self, q: str, hits: list[dict]) -> str:
        ctx = "\n\n".join(f"[{i}] {h['title']}: {h['text']}" for i, h in enumerate(hits, start=1))
        system = ("Answer only from the numbered passages. Cite passages like [1]. If the passages do not answer, "
                  "say so. Be brief and practical.")
        return complete(system, f"Passages:\n{ctx}\n\nQuestion: {q}", max_tokens=500)


# ------------------------------------------------------------------------------------- exception triage
LIKELY_CAUSE = {
    "IF001": "the extract drops fields for some products or admin systems",
    "IF002": "the extract joins to a one-to-many table (riders, beneficiaries) or was appended twice",
    "IF003": "a date field is not validated at source, or the date format changed",
    "IF004": "date of birth and issue date are swapped or keyed wrongly",
    "IF005": "the client may use age nearest birthday rather than age last birthday; confirm the basis",
    "IF006": "fully reduced or terminated policies are being reported as active",
    "IF007": "large cases above the jumbo limit; confirm they were accepted on facultative terms",
    "IF008": "share entered as a percentage instead of a decimal, or the wrong treaty",
    "IF009": "a new code introduced without notice; update the code map once confirmed",
    "IF010": "policies outside the treaty age limits; confirm they belong to this treaty",
    "IF011": "currency defaulted for one product or block; if concentrated, a systematic extract issue",
    "BL001": "timing cutoff between the billing and policy systems",
    "BL002": "reversals coded as renewals",
    "BL003": "terminations processed late on the billing side",
    "BL004": "the billing extract was run twice or appended",
    "BL005": "wrong rate table, or face and share differ between billing and policy systems",
    "CL001": "claim on a policy never reported to us; may not be covered by the treaty",
    "CL002": "event date keyed wrongly, or a claim attached to the wrong policy",
    "CL003": "claim paid at 100% instead of the retro share",
    "CL004": "the termination has not reached the inforce extract yet",
    "RF001": "terminations not reported, or policies dropped from the extract",
    "RF002": "increases or reductions not flagged as changes; confirm with the client",
    "RF003": "late-reported business; confirm the effective date and back premium",
    "FL001": "the file was cut off or built from a different extract than the control",
    "FL002": "the file was cut off or built from a different extract than the control",
    "FL003": "the client changed the layout without notice",
    "FL004": "the file was not sent; check the client's delivery schedule",
    "FL005": "the client resent a file already received",
    "FL006": "late delivery; check the client's month-end timetable",
    "FL007": "the client added or renamed a column",
}


def triage_offline(exc, files) -> list[str]:
    """Plain-language triage for one client and month from its exceptions and file decisions."""
    out = []
    held = files[files.decision.isin(["HOLD", "MISSING"])]
    for _, f in held.iterrows():
        out.append(f"**{f.file_type.title()} file {f.decision.lower()}.** {f.decision_reason}.")
    if exc.empty:
        out.append("No exceptions this month.")
        return out
    crit = exc[exc.severity == "CRITICAL"]
    by = exc.groupby(["rule_id", "rule_name", "severity", "file_type"]).size().reset_index(name="n").sort_values("n", ascending=False)
    top = by.iloc[0]
    share = top.n / len(exc)
    if share >= 0.5 and top.n >= 10:
        out.append(f"**One issue dominates:** {top.n} of {len(exc)} exceptions are \"{top.rule_name}\" in the "
                   f"{top.file_type} file. That points to a systematic cause: {LIKELY_CAUSE.get(top.rule_id, 'check at source')}.")
    out.append(f"**{len(crit)} critical and {len(exc) - len(crit)} warning exceptions** across "
               f"{exc.file_type.nunique()} file type(s).")
    for _, r in by.head(4).iterrows():
        cause = LIKELY_CAUSE.get(r.rule_id)
        out.append(f"- {r.rule_name} ({r.n}, {r.severity.lower()}, {r.file_type}): likely {cause}." if cause else
                   f"- {r.rule_name} ({r.n}, {r.severity.lower()}, {r.file_type}).")
    action = "Ask for corrected files by the fix-by date" if len(held) else "Send the follow-up with the held-back records"
    out.append(f"**Suggested next step:** {action}, and confirm the likely causes with the client before changing any rule.")
    return out


# ------------------------------------------------------------------------------------- follow-up tone
TONES = ["As drafted", "Friendly", "Formal", "Second reminder"]


def retone_offline(text: str, tone: str) -> str:
    if tone == "As drafted":
        return text
    t = text
    if tone == "Friendly":
        t = t.replace("Hello,", "Hi team,").replace(
            "We have processed them and need your help with the items below.",
            "Thanks as always. Everything is processed, and there are just a few things we need your help with.")
        t = t.replace("Kind regards,", "Thanks so much,")
    elif tone == "Formal":
        t = t.replace("Hello,", "Dear colleagues,").replace(
            "We have processed them and need your help with the items below.",
            "We have completed our review and would be grateful for your assistance with the following matters.")
        t = t.replace("Kind regards,", "Yours sincerely,")
    elif tone == "Second reminder":
        t = t.replace("Hello,", "Hello again,").replace(
            "We have processed them and need your help with the items below.",
            "We have not yet received a response to our earlier note. The items below are still open, and the "
            "held data cannot be included in this month's billing until they are resolved.")
        t = t.replace("Please send corrections", "Please send corrections as a priority")
    return t


def retone_llm(text: str, tone: str) -> str:
    system = ("Rewrite this reinsurance client follow-up email in the requested tone. Keep every file name, "
              "policy number, count, date and table exactly as given. Do not add facts. Return only the email in markdown.")
    return complete(system, f"Tone: {tone}\n\nEmail:\n{text}", max_tokens=1200)
