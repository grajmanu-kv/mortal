"""Read-only projections of a life for the UI: the ASCII scene and the frame sent each tick."""

from __future__ import annotations

from typing import Any

from mortal.core.clock import hhmm, phase, story_day, weekday
from mortal.core.constants import PLACE_LABELS
from mortal.core.models import LifeState
from mortal.core.townmap import TownMap, get_town


def render_scene(state: LifeState, town: TownMap | None = None) -> list[str]:
    """The town as rows of text: `@` is the person, lowercase letters are townsfolk."""
    town = town or get_town()
    grid = [list(row) for row in town.rows]
    used: dict[str, int] = {}
    for npc in state.npcs:
        if npc.place is None:
            continue
        slots = town.slots(npc.place)
        index = used.get(npc.place, 0) + 1  # slot 0 (the anchor) belongs to the person
        used[npc.place] = index
        if index < len(slots):
            x, y = slots[index]
            grid[y][x] = npc.glyph
    x, y = state.pos
    grid[y][x] = "@" if state.alive else "X"
    return ["".join(row) for row in grid]


def location_label(state: LifeState, town: TownMap | None = None) -> str:
    town = town or get_town()
    here = town.place_at(state.pos)
    return PLACE_LABELS[here] if here else "on the road"


def frame(state: LifeState, *, runtime: dict[str, Any], since_seq: int, tail: int | None = None) -> tuple[dict, int]:
    """Build one UI frame. Returns (frame, highest log seq included)."""
    town = get_town()
    n = state.needs
    entries = [e for e in state.log if e.seq > since_seq]
    if tail is not None:
        entries = entries[-tail:]
    log = [{"seq": e.seq, "time": f"D{story_day(e.t) + 1} {hhmm(e.t)}", "kind": e.kind, "text": e.text}
           for e in entries]
    age = state.person.age + max(0, story_day(state.t)) // 365
    data = {
        "id": state.id,
        "clock": {"day": story_day(state.t) + 1, "time": hhmm(state.t), "weekday": weekday(state.t),
                  "phase": phase(state.t), "weather": state.weather, "t": state.t},
        "scene": render_scene(state, town),
        "person": {
            "name": state.person.name, "age": age, "occupation": state.person.occupation,
            "description": state.person.description,
            "traits": state.person.traits.model_dump(),
            "activity": state.activity, "kind": state.kind, "location": location_label(state, town),
            "thought": state.thought, "alive": state.alive, "cause_of_death": state.cause_of_death,
            "summary": state.summary,
            "needs": {k: round(getattr(n, k)) for k in ("energy", "hunger", "social", "fun", "health", "mood", "money")},
        },
        "people": [{"name": x.name, "role": x.role, "glyph": x.glyph, "affinity": x.affinity,
                    "where": PLACE_LABELS[x.place] if x.place else "away"} for x in state.npcs],
        "god": {"mood": state.god.mood, "objectives": list(state.god.objectives)},
        "llm": {"provider": state.llm.provider, "model": state.llm.model},
        "runtime": runtime,
        "log": log,
    }
    return data, (log[-1]["seq"] if log else since_seq)
