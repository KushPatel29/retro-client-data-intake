"""Smoke test: every page of the Streamlit app runs without an error."""
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
st_testing = pytest.importorskip("streamlit.testing.v1")

PAGES = ["home", "role", "intake", "mapping", "rules", "recon", "exceptions", "followup", "gate",
         "scorecards", "ask_data", "ask_runbook", "testing", "governance"]


@pytest.fixture(scope="module")
def app():
    at = st_testing.AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=300)
    at.run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_runs(app, page):
    app.switch_page(f"views/{page}.py")
    app.run()
    assert not app.exception, [e.value for e in app.exception]
