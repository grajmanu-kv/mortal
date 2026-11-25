"""Read-only projections of a life for the UI: the ASCII scene and the frame sent each tick."""

from __future__ import annotations

from typing import Any

from mortal.core.clock import hhmm, phase, story_day, weekday
from mortal.core.models import LifeState
from mortal.core.world import World

LEGEND_HIDDEN = {"lettering"}  # glyphs that are just signage, not worth a legend line


def entities(state: LifeState) -> list[dict[str, Any]]:
    """Everyone standing on the map: townsfolk first, the person last (so they are drawn on top)."""
    world = state.world
    used: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for npc in state.npcs:
        if npc.place is None or not world.has(npc.place):
            continue
        slots = world.slots(npc.place)
        index = used.get(npc.place, 0) + 1  # slot 0 (the anchor) belongs to the person
        used[npc.place] = index
        if index < len(slots):
            x, y = slots[index]
            out.append({"x": x, "y": y, "ch": npc.glyph, "kind": "npc"})
    x, y = state.pos
    out.append({"x": x, "y": y, "ch": "@" if state.alive else "X", "kind": "me" if state.alive else "dead"})
    return out


def render_scene(state: LifeState) -> list[str]:
    """The world as rows of text: `@` is the person, lowercase letters are townsfolk."""
    grid = [list(row) for row in state.world.rows]
    for e in entities(state):
        grid[e["y"]][e["x"]] = e["ch"]
    return ["".join(row) for row in grid]


def location_label(state: LifeState) -> str:
    here = state.world.place_at(state.pos)
    return state.world.label(here) if here else "between places"


def legend_for(world: World) -> tuple[dict[str, str], list[dict[str, str]]]:
    """(glyph -> display style for every glyph on the map, the legend lines worth showing)."""
    present: dict[str, None] = {}
    for row in world.rows:
        for ch in row:
            if ch != " ":
                present.setdefault(ch)
    styles: dict[str, str] = {}
    groups: dict[tuple[str, str], str] = {}
    for ch in present:
        g = world.glyph(ch)
        styles[ch] = g.style
        if g.name and g.name not in LEGEND_HIDDEN and ch not in {p.marker for p in world.places.values()}:
            groups[(g.name, g.style)] = groups.get((g.name, g.style), "") + ch
    lines = [{"chars": chars, "name": name, "style": style} for (name, style), chars in groups.items()]
    for p in world.places.values():
        if p.marker:
            styles[p.marker] = "marker"
            lines.append({"chars": p.marker, "name": p.display(), "style": "marker"})
    return styles, lines


def frame(state: LifeState, *, runtime: dict[str, Any], since_seq: int, tail: int | None = None) -> tuple[dict, int]:
    """Build one UI frame. Returns (frame, highest log seq included)."""
    world = state.world
    n = state.needs
    entries = [e for e in state.log if e.seq > since_seq]
    if tail is not None:
        entries = entries[-tail:]
    log = [{"seq": e.seq, "time": f"D{story_day(e.t) + 1} {hhmm(e.t)}", "kind": e.kind, "text": e.text}
           for e in entries]
    age = state.person.age + max(0, story_day(state.t)) // 365
    styles, legend = legend_for(world)
    data = {
        "id": state.id,
        "clock": {"day": story_day(state.t) + 1, "time": hhmm(state.t), "weekday": weekday(state.t),
                  "phase": phase(state.t), "weather": state.weather, "season": state.season, "t": state.t},
        "world": {"name": world.name, "description": world.description, "source": world.source,
                  "revision": world.revision, "season": state.season},
        "terrain": world.rows, "styles": styles, "legend": legend, "entities": entities(state),
        "person": {
            "name": state.person.name, "age": age, "occupation": state.person.occupation,
            "description": state.person.description,
            "traits": state.person.traits.model_dump(),
            "activity": state.activity, "kind": state.kind, "location": location_label(state),
            "thought": state.thought, "alive": state.alive, "cause_of_death": state.cause_of_death,
            "summary": state.summary,
            "needs": {k: round(getattr(n, k)) for k in ("energy", "hunger", "social", "fun", "health", "mood", "money")},
        },
        "people": [{"name": x.name, "role": x.role, "glyph": x.glyph, "affinity": x.affinity, "doing": x.doing,
                    "where": world.label(x.place) if x.place else "away"} for x in state.npcs],
        "god": {"mood": state.god.mood, "objectives": list(state.god.objectives)},
        "llm": {"provider": state.llm.provider, "model": state.llm.model},
        "runtime": runtime,
        "log": log,
    }
    return data, (log[-1]["seq"] if log else since_seq)
