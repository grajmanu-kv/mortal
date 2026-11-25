"""Exercise the real urllib code path against a local fake model server (OpenAI-style and Gemini-style)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from mortal.config import Settings
from mortal.engine.demigod import DemigodService
from mortal.engine.life import LifeEngine
from mortal.errors import LLMError
from mortal.llm.gemini import GeminiProvider
from mortal.llm.openai_compat import OpenAICompatProvider

DECISION = {
    "narration": "A hush falls over the street.",
    "plan": [{"at": "06:00", "place": "home", "kind": "eat", "activity": "eats"},
             {"at": "09:00", "place": "library", "kind": "study", "activity": "reads"},
             {"at": "13:00", "place": "cafe", "kind": "eat", "activity": "lunches"},
             {"at": "22:30", "place": "home", "kind": "sleep", "activity": "sleeps"}],
}


class Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict]] = []
    mode = "ok"

    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        mode = type(self).mode
        if mode == "slow":
            time.sleep(2)
        if mode == "500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error": "boom"}')
            return
        if mode == "html":
            payload = b"<html>not json</html>"
        elif self.path.endswith("/chat/completions"):
            if mode == "no-json-mode" and "response_format" in body:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "response_format unsupported"}')
                return
            text = "```json\n" + json.dumps(DECISION) + "\n```"  # models love fences
            payload = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
        else:
            payload = json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(DECISION)}]}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def fake_server():
    Handler.seen, Handler.mode = [], "ok"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", Handler
    server.shutdown()


def test_openai_compat_over_real_http(fake_server):
    base, handler = fake_server
    reply = OpenAICompatProvider(base + "/v1", "tiny", api_key="tok", timeout=5).complete("SYS", "USER")
    assert json.loads(reply.strip("`json\n")) == DECISION
    req = handler.seen[0]
    assert req["path"] == "/v1/chat/completions" and req["headers"]["authorization"] == "Bearer tok"
    assert req["body"]["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]


def test_gemini_over_real_http_keeps_the_key_in_a_header_not_the_url(fake_server):
    base, handler = fake_server
    reply = GeminiProvider("sekrit", "g-test", timeout=5, base_url=base + "/v1beta/models").complete("SYS", "USER")
    assert json.loads(reply) == DECISION
    req = handler.seen[0]
    assert req["path"] == "/v1beta/models/g-test:generateContent" and "sekrit" not in req["path"]
    assert req["headers"]["x-goog-api-key"] == "sekrit"


def test_json_mode_rejection_is_retried_plain(fake_server):
    base, handler = fake_server
    handler.mode = "no-json-mode"
    OpenAICompatProvider(base + "/v1", "tiny", timeout=5).complete("s", "u")
    assert ["response_format" in r["body"] for r in handler.seen] == [True, False]


@pytest.mark.parametrize("mode,fragment", [("500", "HTTP 500"), ("html", "non-JSON")])
def test_server_failures_become_llm_errors(fake_server, mode, fragment):
    base, handler = fake_server
    handler.mode = mode
    with pytest.raises(LLMError, match=fragment):
        OpenAICompatProvider(base + "/v1", "tiny", timeout=5).complete("s", "u")


def test_timeouts_and_refused_connections_become_llm_errors(fake_server):
    base, handler = fake_server
    handler.mode = "slow"
    with pytest.raises(LLMError, match="unreachable"):
        OpenAICompatProvider(base + "/v1", "tiny", timeout=0.3).complete("s", "u")
    with pytest.raises(LLMError, match="unreachable"):
        OpenAICompatProvider("http://127.0.0.1:9/v1", "tiny", timeout=1).complete("s", "u")


async def test_a_life_driven_by_a_model_over_http(fake_server, make_state):
    base, handler = fake_server
    cfg = Settings.from_env({"MORTAL_OPENAI_BASE_URL": base + "/v1", "MORTAL_OPENAI_MODEL": "tiny",
                             "MORTAL_LLM_TIMEOUT": "5"})
    state = make_state(provider="openai")
    engine = LifeEngine(state, DemigodService(cfg), cfg)
    await engine.step()
    assert state.last_source == "llm"
    assert [i.place for i in state.plan] == ["home", "library", "cafe", "home"]
    assert "A hush falls over the street." in [e.text for e in state.log]
    prompts = [r["body"]["messages"][1]["content"] for r in handler.seen]
    assert "TRIGGER: world." in prompts[0]  # the world is tended before the person's day is planned
    user_prompt = prompts[1]
    assert "Mood: benevolent" in user_prompt and "TRIGGER: dawn" in user_prompt
