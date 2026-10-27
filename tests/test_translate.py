"""Reading translation of the explanation (French <-> English) with the same safety checks."""
import copy

import pytest

from agricredit.agent import graph, llm
from agricredit.agent.runner import AgentRunner


@pytest.fixture(scope="module")
def french_state():
    import os
    os.environ["AGRICREDIT_ADVISORY_LANGUAGE"] = "fr"
    try:
        st = AgentRunner().start("BEN-0001")
    finally:
        os.environ["AGRICREDIT_ADVISORY_LANGUAGE"] = "en"
    assert st["narrative_language"] == "fr" and "Recommandation" in st["advisory"]["narrative"]
    return st


def test_template_text_is_rerendered_in_english(french_state):
    text, author = graph.translate_explanation(french_state, "en")
    assert text.startswith("Draft for officer review") and "English" in author


def test_same_language_returns_the_original(french_state):
    assert graph.translate_explanation(french_state, "fr")[0] == french_state["advisory"]["narrative"]


def _as_model_text(state, text):
    st = copy.deepcopy(state)
    st["advisory"]["narrative"], st["advisory"]["narrative_author"] = text, "ollama:qwen2.5:7b-instruct"
    return st


def test_model_translation_kept_when_faithful(french_state, monkeypatch):
    monkeypatch.setenv("AGRICREDIT_LLM_BACKEND", "ollama")
    st = _as_model_text(french_state, "La capacité de remboursement est de 2,10 fois en situation normale.")
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "Repayment capacity is 2.10 times in a normal season.")
    text, author = graph.translate_explanation(st, "en")
    assert text.startswith("Repayment capacity is 2.10") and "translated from French" in author


def test_model_translation_rejected_when_it_changes_a_figure(french_state, monkeypatch):
    monkeypatch.setenv("AGRICREDIT_LLM_BACKEND", "ollama")
    st = _as_model_text(french_state, "La capacité de remboursement est de 2,10 fois en situation normale.")
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "Repayment capacity is 3.40 times in a normal season.")
    text, author = graph.translate_explanation(st, "en")
    assert text.startswith("Draft for officer review") and "rejected" in author
