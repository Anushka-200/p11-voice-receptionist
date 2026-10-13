"""The dashboard renders against the real database without errors (Streamlit's own headless test runner)."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")


def test_dashboard_runs_and_shows_the_headline_numbers(conn):
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert at.title[0].value.startswith("AI receptionist")
    # either the empty-state hint or the metrics row: both are valid renderings, neither may crash
    assert at.info or len(at.metric) == 4
