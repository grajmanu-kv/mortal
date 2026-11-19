"""Pull one JSON object out of a model reply that may be wrapped in prose or code fences."""

from __future__ import annotations

import json
import re
from typing import Any

from mortal.errors import LLMError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any:
    if not text or not text.strip():
        raise LLMError("empty reply")
    candidates = [text.strip()]
    candidates += [m.strip() for m in _FENCE.findall(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise LLMError("reply did not contain valid JSON")
