"""Choose a provider for a life. `None` means "use the offline rule-based demi-god"."""

from __future__ import annotations

from mortal.config import Settings
from mortal.errors import LLMError
from mortal.llm.base import Provider
from mortal.llm.gemini import GeminiProvider
from mortal.llm.openai_compat import OpenAICompatProvider


def available_providers(settings: Settings) -> list[dict]:
    return [
        {"id": "stub", "label": "Offline rule-based demi-god", "available": True, "model": None,
         "note": "No network, no key. Deterministic for a given seed."},
        {"id": "gemini", "label": "Gemini", "available": bool(settings.gemini_api_key),
         "model": settings.gemini_model, "note": "Set GEMINI_API_KEY."},
        {"id": "openai", "label": "Ollama / OpenAI-compatible", "available": True,
         "model": settings.openai_model,
         "note": f"Endpoint {settings.openai_base_url} (change with MORTAL_OPENAI_BASE_URL)."},
    ]


def resolve_provider_id(choice_provider: str, settings: Settings) -> str:
    if choice_provider != "auto":
        return choice_provider
    if settings.gemini_api_key:
        return "gemini"
    if settings.openai_base_url_explicit:
        return "openai"
    return "stub"


def make_provider(provider_id: str, model: str | None, settings: Settings) -> Provider | None:
    pid = resolve_provider_id(provider_id, settings)
    if pid == "stub":
        return None
    if pid == "gemini":
        return GeminiProvider(settings.gemini_api_key or "", model or settings.gemini_model,
                              settings.llm_timeout, base_url=settings.gemini_base_url)
    if pid == "openai":
        return OpenAICompatProvider(settings.openai_base_url, model or settings.openai_model,
                                    settings.openai_api_key, settings.llm_timeout)
    raise LLMError(f"unknown provider {provider_id!r}")
