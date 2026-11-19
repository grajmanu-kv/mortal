"""Runtime settings read from the environment. Leaf module."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from mortal.errors import ConfigError

DEFAULT_OPENAI_BASE_URL = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    return int(_float(env, key, float(default)))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    gemini_api_key: str | None
    gemini_model: str
    gemini_base_url: str
    openai_base_url: str
    openai_base_url_explicit: bool
    openai_api_key: str | None
    openai_model: str
    llm_timeout: float
    max_decisions_per_day: int
    fate_events_per_day: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        base = env.get("MORTAL_OPENAI_BASE_URL", "").strip()
        return cls(
            data_dir=Path(env.get("MORTAL_DATA_DIR", "mortal_data")).expanduser(),
            gemini_api_key=(env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or None),
            gemini_model=env.get("MORTAL_GEMINI_MODEL", "gemini-2.5-flash"),
            gemini_base_url=env.get("MORTAL_GEMINI_BASE_URL", DEFAULT_GEMINI_BASE_URL).rstrip("/"),
            openai_base_url=(base or DEFAULT_OPENAI_BASE_URL).rstrip("/"),
            openai_base_url_explicit=bool(base),
            openai_api_key=env.get("MORTAL_OPENAI_API_KEY") or None,
            openai_model=env.get("MORTAL_OPENAI_MODEL", "llama3.1"),
            llm_timeout=_float(env, "MORTAL_LLM_TIMEOUT", 30.0),
            max_decisions_per_day=_int(env, "MORTAL_MAX_DECISIONS_PER_DAY", 8),
            fate_events_per_day=_float(env, "MORTAL_FATE_EVENTS_PER_DAY", 1.0),
        )
