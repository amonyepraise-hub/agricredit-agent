"""Drives the Streamlit dashboard headlessly: run a case, approve it through the form, see the gated release."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


def button(at, label):
    return next(b for b in at.button if label in b.label)

APP = str(Path(__file__).resolve().parents[1] / "src" / "agricredit" / "ui" / "app.py")


def test_dashboard_run_approve_release():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    at.selectbox[0].set_value("BEN-0001").run()
    button(at, "Run agent").click().run()
    assert not at.exception, at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "AWAITING_APPROVAL" in md and "RECOMMEND" in md
    for h in ["2 · What the agent did", "3 · Evidence retrieved", "4 · Risk assessment", "5 · Stress test",
              "6 · Draft advisory", "7 · Officer decision", "8 · Audit trail"]:
        assert h in md, h
    at.radio[0].set_value("APPROVE")
    button(at, "Record decision").click().run()
    assert not at.exception, at.exception
    assert any("all approval checks passed" in s.value for s in at.success)
    assert "RELEASED" in " ".join(m.value for m in at.markdown)


def test_dashboard_refusal_renders():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.selectbox[0].set_value("BEN-0009").run()
    button(at, "Run agent").click().run()
    assert not at.exception
    assert any("not in the cooperative register" in e.value for e in at.error)
