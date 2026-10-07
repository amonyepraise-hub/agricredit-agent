"""Protocol tests for the open-weight backends against a fake local HTTP endpoint.
These verify request/response handling and logging, NOT model quality."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agricredit.agent import llm

CAPTURED = {}


class FakeOllama(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CAPTURED[self.path] = body
        if self.path == "/api/chat":
            out = {"message": {"role": "assistant", "content": "<think>internal</think>{\"ok\": true}"}}
        else:
            out = {"choices": [{"message": {"role": "assistant", "content": "{\"ok\": true}"}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_server():
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _call():
    return llm.chat([{"role": "user", "content": "hi"}], purpose="test", json_mode=True, run_id="run-test",
                    case_id=None, node="test", stub_response="STUB")


def test_ollama_backend_request_and_think_stripping(fake_server, monkeypatch):
    monkeypatch.setenv("AGRICREDIT_LLM_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", fake_server)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
    assert llm.parse_json(_call()) == {"ok": True}
    body = CAPTURED["/api/chat"]
    assert body["model"] == "qwen2.5:7b-instruct" and body["format"] == "json" and body["stream"] is False
    assert "think" not in body


def test_qwen3_disables_thinking(fake_server, monkeypatch):
    monkeypatch.setenv("AGRICREDIT_LLM_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", fake_server)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    _call()
    assert CAPTURED["/api/chat"]["think"] is False


def test_openai_compatible_backend(fake_server, monkeypatch):
    monkeypatch.setenv("AGRICREDIT_LLM_BACKEND", "openai_compatible")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", fake_server + "/v1")
    assert llm.parse_json(_call()) == {"ok": True}
    assert CAPTURED["/v1/chat/completions"]["response_format"] == {"type": "json_object"}


def test_unreachable_backend_raises(monkeypatch):
    monkeypatch.setenv("AGRICREDIT_LLM_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("AGRICREDIT_LLM_TIMEOUT_S", "2")
    with pytest.raises(llm.LLMUnavailable):
        _call()


def test_narrative_guard_flags_invented_figures():
    facts = {"pd": 0.0707, "base": 1.3491, "amount": 185000, "suggested": 155000}
    assert llm.unsupported_numbers("PD is 0.07 and capacity 1.35x; reduce to 155,000 FCFA.", facts) == []
    assert llm.unsupported_numbers("Expected profit is 412,000 FCFA.", facts) == [412000.0]
