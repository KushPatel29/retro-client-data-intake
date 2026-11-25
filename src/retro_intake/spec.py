"""Generate the client data specification (data contract) from a client's configuration."""
from __future__ import annotations

FIELD_META = {
    "policy_id": ("text", True, "Unique with life id; never reused"),
    "life_id": ("whole number", True, "1 for single life; 2 for the second life on a joint policy"),
    "insured_dob": ("date", True, "Date of birth of the insured"),
    "sex": ("code", True, "Used for rating"),
    "smoker": ("code", True, "Used for rating"),
    "issue_date": ("date", True, "Policy effective date"),
    "issue_age": ("whole number", True, "Age last birthday at issue"),
    "face_amount": ("amount", True, "Benefit amount on this life, in the treaty currency"),
    "retro_share": ("number", True, "Share ceded to us"),
    "status": ("code", True, "Terminations stay in the file for the month they happen"),
    "currency": ("ISO code", True, "Must be the treaty currency"),
    "product": ("text", False, "Your plan code"),
    "premium": ("amount", True, "Monthly retro premium; negative only on reversals"),
    "transaction_type": ("code", True, "First year, renewal or reversal"),
    "claim_id": ("text", True, "Unique claim reference"),
    "date_of_event": ("date", True, "Date of death or diagnosis"),
    "claim_type": ("code", True, "Death or critical illness"),
    "claim_amount": ("amount", True, "Amount claimed from us (your amount x retro share)"),
}
FMT_LABEL = {"%Y-%m-%d": "YYYY-MM-DD", "%m/%d/%Y": "MM/DD/YYYY", "%d/%m/%Y": "DD/MM/YYYY", "%Y%m%d": "YYYYMMDD",
             "%Y/%m/%d": "YYYY/MM/DD"}
CONTROL_LABEL = {"trailer": "a trailer row (TRL, record count, amount total) as the last line",
                 "sidecar": "a JSON control file with the same name and a .ctl extension",
                 "sheet": "a CONTROL sheet with record_count and total_amount", "none": "none agreed yet"}


def build(c: dict, due_day: int = 10) -> str:
    fmt = FMT_LABEL.get(c["date_format"], c["date_format"])
    lines = [f"# Data specification: {c['name']}", "",
             f"Treaty {c['treaty_id']}, currency {c['currency']}. Issue ages {c['min_issue_age']} to {c['max_issue_age']}; "
             f"jumbo limit {c['jumbo_limit']:,} per life.", "",
             "## Delivery", "",
             f"- Files: inforce, billing and claims, every month, by the {due_day}th of the following month.",
             f"- Names: `{c['filename']}` where file_type is inforce, billing or claims and yyyymm is the month covered.",
             f"- Format: {c['format']}" + (f", delimiter `{c['delimiter']}`" if c.get("delimiter") else "") + f"; dates as {fmt}.",
             f"- Controls: {CONTROL_LABEL.get(c.get('control'), c.get('control'))}. Count and amount must agree with the file.",
             "- Corrections: send a complete replacement file with the same name; it replaces the earlier one.",
             "- Layout changes: tell us 30 days before; a file with a changed layout is held until the mapping is updated.", ""]
    for ftype, cols in c["columns"].items():
        lines += [f"## {ftype.title()} file", "", "| Field | Your column | Type | Format or values | Required | Notes |",
                  "| --- | --- | --- | --- | --- | --- |"]
        widths = dict((n, w) for n, w in c.get("widths", {}).get(ftype, []))
        for std, src in cols.items():
            typ, req, note = FIELD_META.get(std, ("text", False, ""))
            if std in c.get("codes", {}):
                vals = ", ".join(f"{k} = {v}" for k, v in c["codes"][std].items())
            elif typ == "date":
                vals = fmt
            elif std in c.get("multipliers", {}) and c["multipliers"][std] != 1:
                vals = f"value x {c['multipliers'][std]:g} = {std.replace('_', ' ')}"
            else:
                vals = ""
            if widths:
                vals = (vals + "; " if vals else "") + f"width {widths[src]}"
            lines.append(f"| {std} | {src} | {typ} | {vals} | {'yes' if req else 'no'} | {note} |")
        lines.append("")
    lines.append("_Sample specification generated from configuration for a portfolio project; the client is fictional._")
    return "\n".join(lines)
