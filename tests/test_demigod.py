from __future__ import annotations

import json
import random

import pytest

from mortal.config import Settings
from mortal.core.decision import DecisionRequest
from mortal.engine.demigod import BREAKER_COOLDOWN, DemigodService
from mortal.engine.demigod_rule import RuleDemigod, mood_class
from mortal.engine.prompts import SYSTEM_PROMPT
from mortal.errors import LLMError

from conftest import run_ticks


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, replies):
        self.replies, self.calls, self.prompts = list(replies), 0, []

    def complete(self, system, user):
        self.calls += 1
        self.prompts.append((system, user))
        reply = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


def service_with(provider, settings):
    svc = DemigodService(settings)
    svc._providers[("gemini", None)] = provider  # pre-seed the per-choice provider cache
    return svc


GOOD_DAWN = {
    "narration": "Rain on the window; Ayo lingers.",
    "summary": "A careful clerk who loves maps.",
    "plan": [
        {"at": "06:00", "place": "home", "kind": "eat", "activity": "eats toast"},
        {"at": "09:00", "place": "library", "kind": "study", "activity": "pores over atlases"},
        {"at": "13:00", "place": "cafe", "kind": "eat", "activity": "has soup"},
        {"at": "22:30", "place": "home", "kind": "sleep", "activity": "goes to bed"},
    ],
}


# ----------------------------------------------------------------------------- the rule-based demi-god
@pytest.mark.parametrize("mood,expected", [
    ("benevolent", "benevolent"), ("Wrathful and cold", "wrathful"), ("a bit mischievous", "mischievous"),
    ("melancholy", "melancholic"), ("bored", "indifferent"), ("???", "neutral"),
])
def test_mood_classification(mood, expected):
    assert mood_class(mood) == expected


def divine(state, kind, text, seed=0):
    return RuleDemigod().decide(DecisionRequest("divine", state, random.Random(seed), {"kind": kind, "text": text}))


def test_event_in_a_benevolent_mood_softens_bad_news_and_wrathful_deepens_it(make_state):
    kind_ = divine(make_state(mood="benevolent"), "event", "Ayo loses a lot of money in a theft")
    cruel = divine(make_state(mood="wrathful"), "event", "Ayo loses a lot of money in a theft")
    assert kind_.effects.money < 0 and cruel.effects.money < kind_.effects.money
    assert kind_.comment != cruel.comment


def test_good_news_pays_less_when_the_demigod_is_wrathful(make_state):
    warm = divine(make_state(mood="benevolent"), "event", "Ayo wins a prize")
    grim = divine(make_state(mood="wrathful"), "event", "Ayo wins a prize")
    assert warm.effects.money > grim.effects.money > 0


def test_event_naming_a_place_sends_the_person_there(make_state):
    d = divine(make_state(), "event", "An invitation to the temple arrives")
    assert d.plan and d.plan[0].place == "temple" and d.plan[0].at == "now"


def test_nudge_is_usually_followed(make_state):
    followed = sum(bool(divine(make_state(), "nudge", "go to the library", seed=i).plan) for i in range(40))
    assert followed >= 20
    d = divine(make_state(), "nudge", "go to the library", seed=0)
    if d.plan:
        assert d.plan[0].place == "library" and d.plan[0].kind == "study"


def test_death_decree_requires_an_explicit_subject(make_state):
    s = make_state()
    assert divine(s, "event", "Ayo dies").death
    assert divine(s, "event", "kill him").death
    assert divine(s, "event", "He finds a dead bird by the river").death is None
    assert divine(s, "event", "Mara dies").death is None  # someone else's death is not the person's


def test_objectives_steer_the_evening(make_state):
    s = make_state(objectives=["study the old maps"], seed=3)
    d = RuleDemigod().decide(DecisionRequest("dawn", s, random.Random(1)))
    assert any(e.place == "library" and e.kind == "study" for e in d.plan)


def test_melancholy_avoids_company_in_the_evening(make_state):
    s = make_state(mood="melancholic", traits=__import__("mortal.core.models", fromlist=["Traits"]).Traits(extraversion=9))
    for seed in range(10):
        d = RuleDemigod().decide(DecisionRequest("dawn", s, random.Random(seed)))
        evening = [e for e in d.plan if e.at == "17:30"][0]
        assert evening.place in {"river", "temple", "library", "park", "home"}


# ----------------------------------------------------------------------------- the LLM-backed demi-god
async def test_llm_decision_is_applied(make_engine, make_state, settings):
    provider = FakeProvider([GOOD_DAWN])
    engine = make_engine(make_state(provider="gemini"), service_with(provider, settings))
    await engine.step()
    s = engine.state
    assert provider.calls == 1 and s.last_source == "llm"
    assert s.summary.startswith("A careful clerk")
    assert [i.place for i in s.plan] == ["home", "library", "cafe", "home"]
    assert any(e.text == "Rain on the window; Ayo lingers." for e in s.log)


async def test_prompt_carries_mood_objectives_traits_and_gods_words(make_engine, make_state, settings):
    provider = FakeProvider([GOOD_DAWN])
    state = make_state(provider="gemini", mood="wistful but strict", objectives=["teach patience"],
                       description="Collects old maps and avoids crowds.")
    engine = make_engine(state, service_with(provider, settings))
    engine.queue_divine("nudge", "Call your sister.")
    await engine.step()
    divine_prompt = provider.prompts[-1][1]
    for needle in ("wistful but strict", "teach patience", "Collects old maps", "NUDGE", "Call your sister.", "extraversion"):
        assert needle in divine_prompt
    assert "ONE JSON object" in SYSTEM_PROMPT and "home, work, cafe" in SYSTEM_PROMPT


async def test_unusable_replies_fall_back_to_the_rule_demigod(make_engine, make_state, settings):
    for bad in ("I'm sorry, I cannot help with that.", "{broken", LLMError("HTTP 500")):
        provider = FakeProvider([bad])
        engine = make_engine(make_state(provider="gemini"), service_with(provider, settings))
        await engine.step()
        s = engine.state
        assert s.last_source == "rule" and len(s.plan) >= 6  # the day still got planned
        assert any(e.kind == "system" and "falls silent" in e.text for e in s.log)


async def test_circuit_breaker_stops_hammering_a_dead_endpoint(make_engine, make_state, settings):
    provider = FakeProvider([LLMError("down")])
    engine = make_engine(make_state(provider="gemini"), service_with(provider, settings))
    s = engine.state
    await engine.step()  # dawn: failure 1
    engine.queue_divine("event", "Ayo finds a coin")
    await engine.process_pending()  # failure 2 -> breaker opens
    assert provider.calls == 2 and s.llm_cooldown == BREAKER_COOLDOWN
    for i in range(BREAKER_COOLDOWN):
        engine.queue_divine("event", f"something {i}")
        await engine.process_pending()
    assert provider.calls == 2  # not called during the cool-down
    engine.queue_divine("event", "retry now")
    await engine.process_pending()
    assert provider.calls == 3


async def test_llm_budget_limits_automatic_calls_but_never_gods_words(make_engine, make_state):
    cfg = Settings.from_env({"MORTAL_MAX_DECISIONS_PER_DAY": "3", "MORTAL_FATE_EVENTS_PER_DAY": "40"})
    provider = FakeProvider([GOOD_DAWN, {"narration": "ok"}])
    engine = make_engine(make_state(provider="gemini"), service_with(provider, cfg), cfg)
    await run_ticks(engine, 90)  # morning to early afternoon, with fate firing constantly
    spent = provider.calls
    assert spent <= 3 + 1  # dawn (always) + at most the daily budget of automatic decisions
    engine.queue_divine("event", "A letter arrives")
    await engine.process_pending()
    assert provider.calls == spent + 1  # god is always heard by the LLM


async def test_llm_effects_are_clamped_and_unknown_places_dropped(make_engine, make_state, settings):
    reply = {"narration": "chaos", "effects": {"money": 10**9, "mood": -500},
             "plan": [{"at": "now", "place": "the moon", "kind": "relax"}]}
    engine = make_engine(make_state(provider="gemini"), service_with(FakeProvider([GOOD_DAWN, reply]), settings))
    await engine.step()
    before = engine.state.needs.money
    engine.queue_divine("event", "wild")
    await engine.process_pending()
    s = engine.state
    assert s.needs.money - before <= 500 and s.needs.mood >= 0
    assert all(i.place != "the moon" for i in s.plan)


async def test_llm_can_add_people_and_relationships(make_engine, make_state, settings):
    reply = {"narration": "A new face.", "new_npcs": [{"name": "Zara", "role": "cartographer", "place": "library"}],
             "relationships": [{"name": "Zara", "delta": 5}]}
    engine = make_engine(make_state(provider="gemini"), service_with(FakeProvider([GOOD_DAWN, reply]), settings))
    await engine.step()
    engine.queue_divine("event", "Someone new arrives")
    await engine.process_pending()
    zara = engine.state.npc_by_name("zara")
    assert zara is not None and zara.role == "cartographer" and zara.affinity == 15
