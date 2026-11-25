"""Shared state, data access, styling and small UI helpers for the Streamlit app."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from retro_intake import ai, evaluate, pipeline, safesql  # noqa: E402
from retro_intake.common import load_config, load_rules  # noqa: E402
from retro_intake.generate import SEED  # noqa: E402

NAVY, NAVY2, MAG, LATE, SLATE, GREEN = "#0b1f4b", "#8fa3cf", "#e0115f", "#c2410c", "#5a6680", "#2f7d5b"
MONTHS = {"2026-07": "Jul 2026", "2026-08": "Aug 2026", "2026-09": "Sep 2026"}
STATUS_COLOR = {"Loaded": NAVY, "Loaded, records held back": NAVY2, "Held": MAG, "Missing": "#f4b6cc", "Rejected": SLATE}

pio.templates["retro"] = go.layout.Template(layout=go.Layout(
    font=dict(family="Inter, system-ui, sans-serif", size=13, color="#1b2540"),
    colorway=[NAVY, MAG, NAVY2, LATE, SLATE, GREEN],
    margin=dict(l=10, r=10, t=40, b=10), title=dict(font=dict(size=16)),
    xaxis=dict(gridcolor="#e6e9f0", zerolinecolor="#c9cfdc"), yaxis=dict(gridcolor="#e6e9f0", zerolinecolor="#c9cfdc"),
    hoverlabel=dict(font=dict(family="Inter, system-ui, sans-serif"))))
pio.templates.default = "plotly_white+retro"

CSS = """
<style>
.block-container{padding-top:2rem;max-width:1300px}
h1{letter-spacing:-.02em}
.kpi{border-top:3px solid #0b1f4b;padding:.4rem 0 .2rem}
.kpi.hot{border-top-color:#e0115f}
.kpi b{display:block;font-size:1.9rem;line-height:1.1;color:#0b1f4b;white-space:nowrap}
.kpi.hot b{color:#e0115f}
.kpi span{font-size:.85rem;color:#5a6680}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.78rem;font-weight:600;margin-right:4px}
.pill.c{background:#e0115f;color:#fff}.pill.w{background:#eef1f7;color:#0b1f4b;border:1px solid #d5dbe8}
.pill.go{background:#0b1f4b;color:#fff}.pill.ai{background:#efe9ff;color:#4b2bb3;border:1px solid #d9cffc}
.note{font-size:.85rem;color:#5a6680}
.guard{background:#f6f7fb;border:1px solid #e1e5ef;border-radius:8px;padding:.6rem .9rem;font-size:.88rem}
</style>
"""


def _engine_version() -> str:
    """Fingerprint of the engine code and config, so cached runs are rebuilt whenever either changes."""
    import hashlib
    h = hashlib.sha256()
    for f in sorted(list((ROOT / "src" / "retro_intake").glob("*.py")) + list((ROOT / "config").glob("*.yaml"))):
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


ENGINE_VERSION = _engine_version()


def scenario() -> tuple[int, str]:
    return st.session_state.get("scenario", (SEED, "baseline"))


@st.cache_resource(show_spinner=False)
def run_scenario(seed: int, profile: str, version: str = "") -> dict:
    work = Path(tempfile.gettempdir()) / "retro_runs" / f"{profile}_{seed}_{version}"
    res = pipeline.run(workdir=work, regenerate=True, quiet=True, seed=seed, profile=profile)
    res["score"] = evaluate.score(res["db_path"], res["answer_key"])
    res["work"] = work
    return res


def current() -> dict:
    seed, profile = scenario()
    with st.spinner("Running the monthly control process on the synthetic files..."):
        return run_scenario(seed, profile, ENGINE_VERSION)


def db_path() -> Path:
    return current()["db_path"]


@st.cache_data(show_spinner=False, max_entries=500)
def _q(path: str, sql: str, params: tuple | None) -> pd.DataFrame:
    con = duckdb.connect(path, read_only=True)
    try:
        return con.execute(sql, list(params) if params else None).df()
    finally:
        con.close()


def q(sql: str, params: list | tuple | None = None) -> pd.DataFrame:
    return _q(str(db_path()), sql, tuple(params) if params else None)


def safe_con():
    return safesql.connect(db_path())


@st.cache_data(show_spinner=False)
def config() -> dict:
    return load_config()


@st.cache_data(show_spinner=False)
def rules() -> list[dict]:
    return load_rules()


@st.cache_resource(show_spinner=False)
def runbook() -> "ai.Runbook":
    return ai.Runbook()


def cname(cid: str) -> str:
    return config()["clients"].get(cid, {}).get("name", cid)


def header(title: str, lede: str | None = None):
    st.markdown(CSS, unsafe_allow_html=True)
    st.title(title)
    if lede:
        st.markdown(f"<p style='font-size:1.05rem;color:#3d4866;max-width:80ch'>{lede}</p>", unsafe_allow_html=True)
    seed, profile = scenario()
    if (seed, profile) != (SEED, "baseline"):
        st.caption(f"Scenario: {profile} defects, seed {seed}. Change it in the sidebar.")


def kpis(items: list[tuple]):
    cols = st.columns(len(items))
    for c, it in zip(cols, items):
        value, label = it[0], it[1]
        hot = len(it) > 2 and it[2]
        c.markdown(f"<div class='kpi{' hot' if hot else ''}'><b>{value}</b><span>{label}</span></div>",
                   unsafe_allow_html=True)


def status_of(row) -> str:
    if row["decision"] == "MISSING":
        return "Missing"
    if row["decision"] == "HOLD":
        return "Held"
    if row["decision"] == "REJECTED":
        return "Rejected"
    return "Loaded, records held back" if (row["critical_rows"] or 0) > 0 else "Loaded"


def ai_mode_badge():
    if ai.llm_available():
        st.markdown("<span class='pill ai'>Claude API connected</span>", unsafe_allow_html=True)
    else:
        st.markdown("<span class='pill ai'>Offline AI mode</span> <span class='note'>Transparent built-in methods. "
                    "Add ANTHROPIC_API_KEY to the app's secrets to use Claude.</span>", unsafe_allow_html=True)


def guardrails(lines: list[str]):
    st.markdown("<div class='guard'><b>Guardrails</b><ul style='margin:.3rem 0 0 1rem;padding:0'>" +
                "".join(f"<li>{x}</li>" for x in lines) + "</ul></div>", unsafe_allow_html=True)


def log_ai(feature: str, mode: str, request: str, output: str, outcome: str = "Shown to user") -> None:
    """Audit trail of every AI action in this session: what was asked, what came back, what the person did."""
    from datetime import datetime
    st.session_state.setdefault("ai_log", []).insert(0, {
        "time": datetime.now().strftime("%H:%M:%S"), "feature": feature, "mode": mode,
        "request": str(request)[:160], "output": str(output)[:200], "outcome": outcome})


def filt(df: pd.DataFrame, **cols) -> pd.DataFrame:
    """Keep rows whose column value is in the chosen list; an empty list means no filter."""
    m = pd.Series(True, index=df.index)
    for col, vals in cols.items():
        if vals:
            m &= df[col].isin(vals)
    return df[m]


def money(x: float, ccy: str = "") -> str:
    return f"{ccy} {x:,.0f}".strip()
