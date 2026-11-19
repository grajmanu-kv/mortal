"""The life engine: one tick = ten simulated minutes.

Each tick the person follows the current plan item (walking to its place, then doing it); the
body and purse change by `physics`; other townsfolk drift; and the engine decides whether the
demi-god needs to be consulted (dawn, god's words, a crisis, an encounter, a twist of fate).
Everything else is cheap local simulation, so an LLM demi-god is called a handful of times a day.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from mortal.config import Settings
from mortal.core.clock import hhmm, story_day
from mortal.core.constants import (
    DAILY_RENT, FOOD_PLACES, KIND_PHRASE, PLACE_AT, PLACE_TO, TICK_MINUTES, WALK_CELLS_PER_TICK, WEATHERS,
)
from mortal.core.decision import Decision, DecisionRequest
from mortal.core.models import LifeState, PendingDivine
from mortal.core.plan import current_item, merge_plan, resolve_entries
from mortal.core.townmap import get_town
from mortal.engine.demigod import DemigodService
from mortal.engine.npcs import add_npc, move_npcs
from mortal.engine.physics import apply_tick, cause_of_death

BUDGETED = {"crisis", "encounter", "fate"}  # triggers that stop using the LLM once the day's budget is spent


class LifeEngine:
    def __init__(self, state: LifeState, demigod: DemigodService, settings: Settings,
                 on_change: Callable[[], None] | None = None,
                 on_checkpoint: Callable[[], None] | None = None):
        self.state = state
        self.demigod = demigod
        self.settings = settings
        self.town = get_town()
        self.thinking: str | None = None  # trigger name while the demi-god is deliberating
        self._on_change = on_change or (lambda: None)
        self._on_checkpoint = on_checkpoint or (lambda: None)
        self.rng = random.Random(f"{state.seed}:{state.t}")

    # ------------------------------------------------------------------ public API
    def queue_divine(self, kind: str, text: str) -> None:
        """Record god's words; they are acted on by `process_pending`."""
        s = self.state
        s.pending.append(PendingDivine(kind=kind, text=text))  # type: ignore[arg-type]
        s.add_log("divine", f"[{kind}] {text}")
        self._on_change()

    async def _ensure_dawn(self) -> None:
        """Plan the day exactly once, before anything else happens in it."""
        if self.state.alive and self.state.dawned_day < story_day(self.state.t):
            await self._dawn()

    async def process_pending(self) -> None:
        s = self.state
        if s.pending:
            await self._ensure_dawn()  # god's words amend a planned day, never precede it
        while s.pending and s.alive:
            item = s.pending.pop(0)
            await self._decide("divine", {"kind": item.kind, "text": item.text})
        self._on_checkpoint()

    async def step(self) -> None:
        s = self.state
        if not s.alive:
            return
        await self._ensure_dawn()
        await self.process_pending()
        if not s.alive:
            return
        self._tick_person()
        move_npcs(s.npcs, s.t, self.rng)
        if s.needs.health <= 0:
            await self._die(cause_of_death(s))
            return
        await self._interrupts()
        if not s.alive:
            return
        s.t += TICK_MINUTES
        self._on_change()

    # ------------------------------------------------------------------ dawn
    async def _dawn(self) -> None:
        s = self.state
        sday = story_day(s.t)
        s.dawned_day = sday
        self.rng = random.Random(f"{s.seed}:{sday}")
        s.weather = self.rng.choices(WEATHERS, weights=(40, 25, 20, 10, 5))[0]
        s.decisions_today = 0
        s.reflex = None
        if sday > 0:
            s.needs.money -= DAILY_RENT
            s.add_log("system", f"Rent paid: -{DAILY_RENT:g}. Money: {s.needs.money:.0f}.")
            if sday % 365 == 0:
                s.add_log("event", f"{s.person.name} turns {s.person.age + sday // 365}.")
                s.add_memory(f"Turned {s.person.age + sday // 365}.", 3)
        await self._decide("dawn")
        if sum(1 for i in s.plan if i.t >= s.t) < 3:  # an unusable dawn plan: fall back to routine
            req = DecisionRequest("dawn", s, self.rng)
            fallback = self.demigod.rule.decide(req)
            self._apply_plan(fallback, "dawn")
        self._on_checkpoint()

    # ------------------------------------------------------------------ the person
    def _update_reflex(self) -> None:
        s, n = self.state, self.state.needs
        if (s.reflex == "sleep" and n.energy > 40) or (s.reflex == "eat" and n.hunger < 40):
            s.reflex = None
        if s.reflex is None:
            if n.energy <= 5:
                s.reflex = "sleep"
                s.add_log("event", f"{s.person.name}'s body gives out and falls asleep on the spot.")
            elif n.hunger >= 90:
                s.reflex = "eat"
                s.add_log("event", "Hunger overrules the plan.")

    def _tick_person(self) -> None:
        s = self.state
        here = self.town.place_at(s.pos)
        self._update_reflex()
        item = current_item(s.plan, s.t)

        if s.reflex == "sleep":
            kind, target, text, key = "sleep", here, "sleeps where exhaustion dropped them", "reflex:sleep"
        elif s.reflex == "eat":
            kind, target, key = "eat", (here if here in FOOD_PLACES else "home"), "reflex:eat"
            text = "eats at last"
        elif item is not None:
            kind, target, text, key = item.kind, item.place, item.activity, f"{item.t}:{item.place}:{item.kind}"
        else:
            kind, target, text, key = "idle", here, "idles", "idle"

        if target is not None and here != target:
            self._walk(target, key)
            return
        company = any(npc.place == here for npc in s.npcs) if here else False
        apply_tick(s, kind, here, company=company)
        text = text or f"{KIND_PHRASE[kind]} {PLACE_AT.get(here or '', '')}".strip()
        s.kind = kind
        s.activity = text
        if s.current_key != f"do:{key}":
            s.current_key = f"do:{key}"
            s.add_log("action", f"{s.person.name} {text}.")

    def _walk(self, target: str, key: str) -> None:
        s = self.state
        path = self.town.path(s.pos, self.town.anchor(target))
        s.pos = path[: WALK_CELLS_PER_TICK][-1]
        apply_tick(s, "travel", None)
        s.kind = "travel"
        s.activity = f"walking to {PLACE_TO[target]}"
        if s.current_key != f"go:{key}":
            s.current_key = f"go:{key}"
            s.add_log("action", f"{s.person.name} sets off for {PLACE_TO[target]}.")

    # ------------------------------------------------------------------ interrupts
    def _soon(self, kind: str, minutes: int) -> bool:
        s = self.state
        return any(i.kind == kind and s.t <= i.t <= s.t + minutes for i in s.plan)

    def _crisis_kind(self) -> str | None:
        s, n = self.state, self.state.needs
        checks = (
            ("hunger", n.hunger >= 80 and s.kind != "eat" and s.reflex != "eat" and not self._soon("eat", 40), 240),
            ("energy", n.energy <= 15 and s.kind != "sleep" and s.reflex != "sleep" and not self._soon("sleep", 60), 360),
            ("health", n.health <= 30, 360),
            ("money", n.money < 0, 1440),
            ("mood", n.mood <= 15, 720),
        )
        for key, condition, cooldown in checks:
            if condition and s.t - s.flags.get(key, -10**9) >= cooldown:
                s.flags[key] = s.t
                return key
        return None

    async def _interrupts(self) -> None:
        s = self.state
        crisis = self._crisis_kind()
        if crisis:
            await self._decide("crisis", {"kind": crisis})
            return
        here = self.town.place_at(s.pos)
        if here and s.kind not in {"sleep", "travel"}:
            present = [n for n in s.npcs if n.place == here and s.t - n.last_met_t >= 240]
            if present and self.rng.random() < 0.5:
                npc = self.rng.choice(present)
                npc.last_met_t = s.t
                await self._decide("encounter", {"npc": npc.id})
                return
        rate = self.settings.fate_events_per_day
        if rate > 0 and s.kind != "sleep" and self.rng.random() < rate / 96:
            await self._decide("fate")

    # ------------------------------------------------------------------ decisions
    async def _decide(self, trigger: str, detail: dict | None = None) -> Decision:
        s = self.state
        allow_llm = trigger not in BUDGETED or s.decisions_today < self.settings.max_decisions_per_day
        req = DecisionRequest(trigger, s, self.rng, detail or {})
        self.thinking = trigger
        self._on_change()
        try:
            decision, source = await self.demigod.decide(req, allow_llm=allow_llm)
        finally:
            self.thinking = None
        if source == "llm":
            s.decisions_today += 1
        s.last_source = source
        self._apply(decision, trigger)
        if decision.death and trigger == "divine" and s.alive:
            await self._die(decision.death)
        return decision

    def _apply_plan(self, d: Decision, trigger: str) -> None:
        s = self.state
        items = resolve_entries(d.plan, s.t)
        if items:
            s.plan = merge_plan(s.plan, items, s.t, replace_future=(trigger == "dawn"))

    def _apply(self, d: Decision, trigger: str) -> None:
        s, n = self.state, self.state.needs
        if d.comment:
            s.add_log("demigod", d.comment)
        if d.narration:
            s.add_log("event" if trigger in {"fate", "death"} else "narration", d.narration)
        if d.thought:
            s.thought = d.thought
            s.add_log("thought", d.thought)
        e = d.effects
        n.energy += e.energy
        n.hunger += e.hunger
        n.social += e.social
        n.fun += e.fun
        n.health += e.health
        n.mood += e.mood
        n.money += e.money
        n.clamp()
        for newcomer in d.new_npcs:
            added = add_npc(s.npcs, newcomer.name, newcomer.role, newcomer.note, newcomer.affinity,
                            newcomer.place, self.rng)
            if added is not None:
                s.add_log("event", f"{added.name} ({added.role}) enters the story.")
        for change in d.relationships:
            npc = s.npc_by_name(change.name)
            if npc is not None:
                npc.affinity = max(-100, min(100, npc.affinity + change.delta))
                npc.last_met_t = s.t
        if d.profile:
            self._apply_profile(d)
        self._apply_plan(d, trigger)
        if d.life_event:
            s.add_memory(d.life_event.text, d.life_event.importance)
        if d.summary:
            s.summary = d.summary

    def _apply_profile(self, d: Decision) -> None:
        s, person = self.state, self.state.person
        change = d.profile
        if change is None:
            return
        if change.occupation is not None:
            occupation = change.occupation.strip()
            lost = bool(person.occupation) and not occupation
            person.occupation = occupation
            if lost:  # no job to go to: today's remaining work blocks become time at home
                for item in s.plan:
                    if item.t >= s.t and item.kind == "work":
                        item.kind, item.place = "relax", "home"
                        item.activity = "stays home, unsure what to do with the hours"
        if change.wage_per_hour is not None:
            person.wage_per_hour = change.wage_per_hour
        s.add_log("system", f"Livelihood: {person.occupation or 'no work'}"
                            f"{f' at {person.wage_per_hour:g}/hour' if person.occupation else ''}.")

    async def _die(self, cause: str) -> None:
        s = self.state
        if not s.alive:
            return
        s.alive = False
        s.cause_of_death = cause
        s.kind = "dead"
        s.activity = "dead"
        await self._decide("death", {"cause": cause})
        s.add_log("system", f"The story ends. {s.person.name} lived {story_day(s.t) + 1} days.")
        self._on_change()
        self._on_checkpoint()

    # ------------------------------------------------------------------ misc
    def clock_text(self) -> str:
        return hhmm(self.state.t)
