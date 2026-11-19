"""Any OpenAI-compatible chat endpoint: Ollama, llama.cpp server, vLLM, LM Studio, OpenAI itself."""

from __future__ import annotations

from mortal.errors import LLMError
from mortal.llm.http import post_json


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout: float = 30.0, temperature: float = 0.9):
        if not base_url.startswith(("http://", "https://")):
            raise LLMError("MORTAL_OPENAI_BASE_URL must start with http:// or https://")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout
        self._temperature = temperature

    def complete(self, system: str, user: str) -> str:
        payload: dict = {
            "model": self.model,
            "temperature": self._temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        try:
            data = post_json(self._url, payload, self._headers, self._timeout)
        except LLMError as exc:
            if "HTTP 400" not in str(exc):
                raise
            payload.pop("response_format")  # some servers reject JSON mode; the prompt still demands JSON
            data = post_json(self._url, payload, self._headers, self._timeout)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected chat-completions response shape") from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMError("model reply was empty")
        return text
