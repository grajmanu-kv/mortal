"""Applying a demi-god's `WorldUpdate` to a life: the weather, the townsfolk's days, and changes to the map.

A map change is the risky part. It is applied to a *copy* of the world and kept only if the whole
world still validates; when it does not, the demi-god is shown the problems and asked to correct its
patch (a bounded number of rounds). If that fails the world simply stays as it was.
"""

from __future__ import annotations

import logging
import random

from mortal.core.clock import story_day
from mortal.core.decision import WorldUpdate
from mortal.core.models import LifeState
from mortal.core.world import World, WorldPatch
from mortal.engine.npcs import add_npc, apply_npc_life
from mortal.engine.worldgen import repair_patch
from mortal.llm.base import Provider

log = logging.getLogger(__name__)


def apply_town_life(s: LifeState, update: WorldUpdate, rng: random.Random) -> None:
    """Weather, townsfolk routines, arrivals and departures. Cheap, local, cannot fail the world."""
    if update.weather:
        s.weather = update.weather.strip()[:40]
    for life in update.npcs:
        npc = s.npc_by_name(life.name)
        if npc is not None:
            apply_npc_life(npc, life, s.world)
    for newcomer in update.arrivals:
        added = add_npc(s.npcs, newcomer.name, newcomer.role, newcomer.note, newcomer.affinity,
                        newcomer.place, rng, s.world)
        if added is not None:
            s.add_log("event", f"{added.name} ({added.role}) arrives in {s.world.name}.")
    for name in update.departures:
        npc = s.npc_by_name(name)
        if npc is not None:
            s.npcs.remove(npc)
            s.add_log("event", f"{npc.name} ({npc.role}) leaves {s.world.name}.")


def commit_world(s: LifeState, new: World) -> None:
    """Swap in a changed world and make everything standing in it consistent with the new map."""
    s.world = new
    home = new.home
    if s.pos not in new.main_area():  # the ground gave way under them (or the area was cut off)
        s.pos = new.nearest_in_main(s.pos)
        s.add_log("event", f"{s.person.name} is swept to safety as the ground changes.")
    for item in s.plan:  # scheduled places that no longer exist
        if not new.has(item.place):
            item.place = new.pick(item.kind, avoid=home) or home
    for npc in s.npcs:
        if npc.place is not None and not new.has(npc.place):
            npc.place = None
    s.current_key = ""  # re-announce the walk: the route may be different now


async def try_patch(s: LifeState, patch: WorldPatch, *, provider: Provider | None, attempts: int,
                    intent: str) -> str | None:
    """Apply `patch`, asking the demi-god to correct it if the validator objects.

    Returns the change's summary on success, or None (the world is left untouched) after logging why.
    """
    problems: list[str] = []
    for attempt in range(max(1, attempts)):
        new, problems = s.world.apply_patch(patch, story_day=story_day(s.t))
        if new is not None:
            commit_world(s, new)
            return patch.summary.strip() or "The shape of the world shifts."
        log.info("world patch rejected (try %d): %s", attempt + 1, "; ".join(problems[:3]))
        if provider is None or attempt + 1 >= attempts:
            break
        fixed = await repair_patch(provider, s.world, patch, problems, intent)
        if fixed is None:
            break
        patch = fixed
    s.add_log("system", f"The demi-god's change to the world could not be made to hold "
                        f"({(problems[0] if problems else 'unknown problem')[:140]}); the world stays as it was.")
    return None
