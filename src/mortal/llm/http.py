"""Tiny stdlib JSON-over-HTTP helper shared by the hosted/local providers."""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.request
from typing import Any

from mortal.errors import LLMError


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    """POST `payload` as JSON and decode the JSON reply. Never includes headers in errors."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"HTTP {exc.code} from model endpoint: {detail}".strip()) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"model endpoint unreachable: {getattr(exc, 'reason', exc)}") from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError("model endpoint returned non-JSON") from exc
    if not isinstance(decoded, dict):
        raise LLMError("unexpected response shape")
    return decoded
