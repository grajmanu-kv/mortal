"""Google Gemini via the public REST API (`generateContent`), JSON output mode."""

from __future__ import annotations

from mortal.config import DEFAULT_GEMINI_BASE_URL
from mortal.errors import LLMError
from mortal.llm.http import post_json


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 30.0, temperature: float = 0.9,
                 base_url: str = DEFAULT_GEMINI_BASE_URL, max_tokens: int = 4096):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self.model = model
        self._timeout = timeout
        self._temperature = temperature
        self._max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": self._temperature,
                "maxOutputTokens": self._max_tokens,
                "responseMimeType": "application/json",
            },
        }
        data = post_json(f"{self._base}/{self.model}:generateContent", payload,
                         {"x-goog-api-key": self._key}, self._timeout)
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no reply ({reason})")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text.strip():
            raise LLMError(f"Gemini reply was empty (finish: {candidates[0].get('finishReason', '?')})")
        return text
