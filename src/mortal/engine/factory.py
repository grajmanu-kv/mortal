"""Turn god's description of a person (and of a world) into a living `LifeState`."""

from __future__ import annotations

import random
import secrets

from mortal.core.models import LifeSpec, LifeState, Needs
from mortal.core.world import World, builtin_world
from mortal.engine.npcs import default_cast, make_npc


def new_life_state(spec: LifeSpec, world: World | None = None) -> LifeState:
    """Build the initial state. `world` is the drawn world; without one the built-in town is used."""
    seed = spec.seed if spec.seed is not None else random.SystemRandom().randrange(1, 2**31)
    rng = random.Random(seed)
    world = world or builtin_world()
    person = spec.person

    if spec.npcs:
        taken: set[str] = set()
        npcs = [make_npc(x.name, x.role, x.note, 20, None, taken, rng, world) for x in spec.npcs
                if x.name.strip().lower() != person.name.strip().lower()]
    else:
        npcs = default_cast(person.name, rng, world)

    # temperament sets the starting mood: calm people start a little brighter
    needs = Needs(mood=max(20.0, min(90.0, 60.0 + (5 - person.traits.neuroticism) * 2)))
    state = LifeState(
        id=secrets.token_hex(4), seed=seed, person=person, needs=needs, npcs=npcs,
        god=spec.god, llm=spec.llm, pos=world.anchor(world.home), world=world,
    )
    role = f", {person.occupation}" if person.occupation else ""
    state.add_log("system", f"A life begins: {person.name}, {person.age}{role}.")
    return state
