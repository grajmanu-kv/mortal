"""Provider protocol: the only thing the engine knows about a language model."""

from __future__ import annotations

from typing import Protocol


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str) -> str:
        """Return the model's raw text reply (expected to contain one JSON object).

        Synchronous and blocking; the engine runs it in a worker thread.
        Raises `mortal.errors.LLMError` on any failure.
        """
        ...
