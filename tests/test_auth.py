"""Officer accounts: hashed passwords, bound to registered officers, sign-in required on hosted deployments."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agricredit import auth

APP = str(Path(__file__).resolve().parents[1] / "src" / "agricredit" / "ui" / "app.py")


@pytest.fixture
def users(tmp_path, monkeypatch):
    f = tmp_path / "users.json"
    monkeypatch.setenv("AGRICREDIT_USERS_FILE", str(f))
    return f


def test_password_is_hashed_and_verified(users):
    auth.add_user("houngbo", "correct-horse-battery", "OFF-001")
    stored = json.loads(users.read_text())["users"]["houngbo"]
    assert "correct-horse-battery" not in users.read_text()
    assert stored["officer_id"] == "OFF-001" and len(stored["hash"]) == 64
    who = auth.authenticate("Houngbo", "correct-horse-battery")
    assert who and who["officer_id"] == "OFF-001" and who["officer_name"]
    assert auth.authenticate("houngbo", "wrong-password-123") is None
    assert auth.authenticate("nobody", "correct-horse-battery") is None


def test_rules(users):
    with pytest.raises(auth.AuthError):
        auth.add_user("short", "tooshort", "OFF-001")
    with pytest.raises(auth.AuthError):
        auth.add_user("ghost", "long-enough-pass", "OFF-999")  # unknown officer and no name given
    auth.add_user("dossou", "long-enough-pass", "OFF-002")
    with pytest.raises(auth.AuthError):
        auth.add_user("dossou", "another-long-pass", "OFF-002")
    auth.set_active("dossou", False)
    assert auth.authenticate("dossou", "long-enough-pass") is None


def test_bootstrap_from_env(users, monkeypatch):
    monkeypatch.setenv("AGRICREDIT_BOOTSTRAP_USERS", "judge:judge-pass-2026:OFF-001")
    assert auth.bootstrap_from_env() == ["judge"]
    assert auth.authenticate("judge", "judge-pass-2026")["officer_id"] == "OFF-001"


def test_dashboard_requires_sign_in_and_binds_approval_to_officer(users, monkeypatch):
    monkeypatch.setenv("AGRICREDIT_AUTH_REQUIRED", "true")
    auth.add_user("dossou", "long-enough-pass", "OFF-002")
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert any(b.label == "Sign in" for b in at.button) and not at.selectbox  # nothing visible before sign-in
    at.text_input[0].set_value("dossou")
    at.text_input[1].set_value("wrong-password-1")
    at.button[0].click().run()
    assert any("Wrong username or password" in e.value for e in at.error)
    at.text_input[1].set_value("long-enough-pass")
    at.button[0].click().run()
    assert not at.exception, at.exception
    at.selectbox[0].set_value("BEN-0001").run()
    next(b for b in at.button if "Run agent" in b.label).click().run()
    assert not at.exception, at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "OFF-002" in md and "signed in" in md
    next(r for r in at.radio if r.label == "Decision").set_value("APPROVE")
    next(b for b in at.button if "Record decision" in b.label).click().run()
    assert not at.exception, at.exception
    assert any("all approval checks passed" in s.value for s in at.success)


def test_demo_engine_choice_runs_fast_mode_without_leaking_settings(monkeypatch):
    import os
    monkeypatch.setenv("AGRICREDIT_DEMO_ENGINE_CHOICE", "true")
    monkeypatch.setenv("AGRICREDIT_LLM_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")  # nothing listens: fast mode must not need it
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    engine = next(sb for sb in at.selectbox if sb.label == "Who writes the explanation")
    engine.set_value("fast").run()
    next(sb for sb in at.selectbox if sb.label == "1 · Select a case").set_value("BEN-0001").run()
    next(b for b in at.button if "Run agent" in b.label).click().run()
    assert not at.exception, at.exception
    assert "offline-stub" in " ".join(c.value for c in at.caption)
    assert os.environ["AGRICREDIT_LLM_BACKEND"] == "ollama"  # per-run setting restored afterwards
