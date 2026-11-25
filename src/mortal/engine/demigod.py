"""The demi-god service: picks LLM or rule-based per life, and degrades gracefully.

* LLM replies are parsed leniently (`parse_decision`); an unusable reply raises and is retried
  by the rule-based demi-god so the story never stalls.
* After two consecutive failures the LLM is skipped for the next few decisions (a circuit
  breaker), so a dead endpoint costs one timeout, not one per tick.
"""

from __future__ import annotations

import asyncio
import logging

from mortal.config import Settings
from mortal.core.decision import Decision, DecisionRequest, parse_decision
from mortal.core.models import LifeState, LLMChoice
from mortal.engine.demigod_rule import RuleDemigod
from mortal.engine.prompts import build_prompts
from mortal.errors import LLMError
from mortal.llm.base import Provider
from mortal.llm.factory import make_provider, resolve_provider_id
from mortal.llm.jsonutil import extract_json

log = logging.getLogger(__name__)

BREAKER_FAILURES = 2
BREAKER_COOLDOWN = 5  # decisions served offline before the LLM is tried again


class DemigodService:
    def __init__(self, settings: Settings, rule: RuleDemigod | None = None):
        self._settings = settings
        self.rule = rule or RuleDemigod()
        self._providers: dict[tuple[str, str | None, bool], Provider | None] = {}

    def provider_for(self, choice: LLMChoice, *, long_reply: bool = False) -> Provider | None:
        """The provider a life's backend choice resolves to (None = offline). `long_reply` is for map drawing."""
        key = (choice.provider, choice.model, long_reply)
        if key not in self._providers:
            try:
                self._providers[key] = make_provider(choice.provider, choice.model, self._settings,
                                                     long_reply=long_reply)
            except LLMError as exc:
                log.warning("provider unavailable: %s", exc)
                self._providers[key] = None
        return self._providers[key]

    def _provider_for(self, s: LifeState) -> Provider | None:
        return self.provider_for(s.llm)

    def provider_label(self, s: LifeState) -> str:
        pid = resolve_provider_id(s.llm.provider, self._settings)
        provider = self._provider_for(s)
        return f"{pid}:{provider.model}" if provider else "offline"

    def forget_providers(self) -> None:
        self._providers.clear()

    async def decide(self, req: DecisionRequest, *, allow_llm: bool = True) -> tuple[Decision, str]:
        """Return (decision, source) where source is "llm" or "rule"."""
        s = req.state
        provider = self._provider_for(s) if allow_llm else None
        if provider is not None:
            if s.llm_cooldown > 0:
                s.llm_cooldown -= 1
            else:
                try:
                    system, user = build_prompts(req)
                    text = await asyncio.to_thread(provider.complete, system, user)
                    decision = parse_decision(extract_json(text))
                    s.llm_failures = 0
                    return decision, "llm"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - any failure degrades to the rule demi-god
                    s.llm_failures += 1
                    reason = str(exc)[:160] or exc.__class__.__name__
                    log.warning("demi-god LLM failed (%s): %s", provider.name, reason)
                    s.add_log("system", f"The demi-god falls silent ({reason}). Fate follows routine for now.")
                    if s.llm_failures >= BREAKER_FAILURES:
                        s.llm_cooldown = BREAKER_COOLDOWN
                        s.llm_failures = 0
        return self.rule.decide(req), "rule"
