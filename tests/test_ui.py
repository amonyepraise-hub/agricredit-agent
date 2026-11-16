"""Drives the officer desk headlessly: open a request, let the agent review it, approve it, see the gated release."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "src" / "agricredit" / "ui" / "app.py")


def text(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_desk_review_approve_release():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert "Loan requests" in text(at)
    at.button(key="open-BEN-0001").click().run()
    at.button(key="run").click().run()
    assert not at.exception, at.exception
    md = text(at)
    for heading in ["AgriCredit advises", "Why AgriCredit advises this", "What to do next", "Your decision",
                    "Rainfall this season", "Can the farmer still repay if the season goes badly?", "Past loans",
                    "Where every number comes from"]:
        assert heading in md, heading
    assert "_" not in " ".join(m.value for m in at.markdown if "reason" in m.value)  # no internal names in reasons
    at.button(key="approve").click().run()
    assert not at.exception, at.exception
    assert any("ready to send to the farmer" in s.value for s in at.success)
    assert any("12 of 12 safety checks passed" in e.label for e in at.expander)


def test_desk_refusal_renders():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.button(key="open-BEN-0009").click().run()
    at.button(key="run").click().run()
    assert not at.exception
    md = text(at)
    assert "Cannot review this request" in md and "not in the cooperative register" in md


def test_plain_language_helpers():
    from agricredit.ui import helpers as H
    assert H.plain("latest maize price is -45.3% vs the usual September price (2021-2025 median)") == \
        "Maize sells 45% below its usual September price at the local market."
    assert H.package_plain("Maize fertiliser 1 ha: 4 x 50 kg NPK + 2 x 50 kg urea at 2025/26 subsidised prices") == \
        "fertiliser for 1 ha of maize (4 bags of NPK and 2 bags of urea, 50 kg each)"
    assert H.clean_narrative("Advice: RECOMMEND_WITH_CONDITIONS [derived:BEN-2001:x] ok.") == \
        "Advice: lend, with conditions ok."
    assert H.highlight("REAL data: routine maize case | X") is None
