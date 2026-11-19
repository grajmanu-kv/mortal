from __future__ import annotations

import json

import pytest

from mortal.config import Settings
from mortal.errors import ConfigError, LLMError
from mortal.llm import gemini as gemini_mod
from mortal.llm import openai_compat as openai_mod
from mortal.llm.factory import available_providers, make_provider, resolve_provider_id
from mortal.llm.gemini import GeminiProvider
from mortal.llm.jsonutil import extract_json
from mortal.llm.openai_compat import OpenAICompatProvider


# ----------------------------------------------------------------------------- json extraction
@pytest.mark.parametrize("text", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Sure! Here you go:\n{"a": 1}\nHope that helps.',
    '```\n{"a": 1}\n```',
])
def test_extract_json_handles_wrapping(text):
    assert extract_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "   ", "no json here", "{broken"])
def test_extract_json_rejects_garbage(text):
    with pytest.raises(LLMError):
        extract_json(text)


# ----------------------------------------------------------------------------- settings / factory
def test_settings_parse_and_defaults():
    s = Settings.from_env({})
    assert s.llm_timeout == 30.0 and s.max_decisions_per_day == 8 and s.gemini_api_key is None
    assert not s.openai_base_url_explicit
    s = Settings.from_env({"MORTAL_LLM_TIMEOUT": "5", "MORTAL_OPENAI_BASE_URL": "http://x:1/v1/", "GEMINI_API_KEY": "k"})
    assert s.llm_timeout == 5 and s.openai_base_url == "http://x:1/v1" and s.openai_base_url_explicit


def test_settings_reject_bad_numbers():
    with pytest.raises(ConfigError):
        Settings.from_env({"MORTAL_LLM_TIMEOUT": "soon"})


def test_auto_resolution_prefers_gemini_then_explicit_local_then_stub():
    assert resolve_provider_id("auto", Settings.from_env({})) == "stub"
    assert resolve_provider_id("auto", Settings.from_env({"MORTAL_OPENAI_BASE_URL": "http://h/v1"})) == "openai"
    assert resolve_provider_id("auto", Settings.from_env({"GEMINI_API_KEY": "k", "MORTAL_OPENAI_BASE_URL": "http://h/v1"})) == "gemini"
    assert resolve_provider_id("stub", Settings.from_env({"GEMINI_API_KEY": "k"})) == "stub"


def test_make_provider_and_listing():
    cfg = Settings.from_env({"GEMINI_API_KEY": "k"})
    assert make_provider("stub", None, cfg) is None
    assert make_provider("auto", None, cfg).name == "gemini"
    assert make_provider("openai", "m", cfg).model == "m"
    by_id = {p["id"]: p for p in available_providers(Settings.from_env({}))}
    assert by_id["stub"]["available"] and not by_id["gemini"]["available"]


def test_gemini_without_key_fails_loudly():
    with pytest.raises(LLMError):
        make_provider("gemini", None, Settings.from_env({}))


# ----------------------------------------------------------------------------- providers (HTTP mocked)
def test_gemini_request_shape_and_reply(monkeypatch):
    seen = {}

    def fake_post(url, payload, headers, timeout):
        seen.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {"candidates": [{"content": {"parts": [{"text": '{"narration": "hi"}'}]}}]}

    monkeypatch.setattr(gemini_mod, "post_json", fake_post)
    text = GeminiProvider("secret", "gemini-x", timeout=7).complete("SYS", "USER")
    assert json.loads(text) == {"narration": "hi"}
    assert seen["url"].endswith("/models/gemini-x:generateContent")
    assert "secret" not in seen["url"] and seen["headers"] == {"x-goog-api-key": "secret"}
    assert seen["payload"]["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert seen["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["timeout"] == 7


def test_gemini_blocked_or_empty_replies_raise(monkeypatch):
    monkeypatch.setattr(gemini_mod, "post_json", lambda *a, **k: {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(LLMError, match="SAFETY"):
        GeminiProvider("k", "m").complete("s", "u")
    monkeypatch.setattr(gemini_mod, "post_json", lambda *a, **k: {"candidates": [{"content": {"parts": [{"text": " "}]}}]})
    with pytest.raises(LLMError):
        GeminiProvider("k", "m").complete("s", "u")


def test_openai_compat_reply_and_auth_header(monkeypatch):
    seen = {}

    def fake_post(url, payload, headers, timeout):
        seen.update(url=url, payload=payload, headers=headers)
        return {"choices": [{"message": {"content": '{"narration": "yo"}'}}]}

    monkeypatch.setattr(openai_mod, "post_json", fake_post)
    p = OpenAICompatProvider("http://localhost:11434/v1/", "llama3.1", api_key="tok")
    assert json.loads(p.complete("s", "u")) == {"narration": "yo"}
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["headers"] == {"Authorization": "Bearer tok"}
    assert seen["payload"]["response_format"] == {"type": "json_object"}


def test_openai_compat_retries_without_json_mode_on_400(monkeypatch):
    calls = []

    def fake_post(url, payload, headers, timeout):
        calls.append("response_format" in payload)
        if "response_format" in payload:
            raise LLMError("HTTP 400 from model endpoint: unsupported")
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(openai_mod, "post_json", fake_post)
    OpenAICompatProvider("http://h/v1", "m").complete("s", "u")
    assert calls == [True, False]


def test_openai_compat_rejects_bad_base_url_and_shape(monkeypatch):
    with pytest.raises(LLMError):
        OpenAICompatProvider("ftp://nope", "m")
    monkeypatch.setattr(openai_mod, "post_json", lambda *a, **k: {"choices": []})
    with pytest.raises(LLMError):
        OpenAICompatProvider("http://h/v1", "m").complete("s", "u")
