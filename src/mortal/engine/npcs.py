"""The other people in the town: creation, glyphs, daily routines and movement.

A townsperson either follows a *routine* written by the demi-god (hour-by-hour places for the
day) or, lacking one, wanders between their *haunts*: places chosen by what the role does
(a barista haunts somewhere that affords eating; a friend, somewhere to socialise).
"""

from __future__ import annotations

import random

from mortal.core.clock import is_weekend, minute_of_day, parse_minute_of_day
from mortal.core.decision import NpcLife
from mortal.core.models import MAX_NPCS, Npc, RoutineStop
from mortal.core.world import World

NAME_POOL = (
    "Mara", "Ilya", "Tomas", "Noor", "Bea", "Kofi", "Lena", "Ravi", "Sora", "Hugo", "Anya", "Dev",
    "Mina", "Otto", "Zane", "Yusuf", "Elsa", "Kenji", "Rosa", "Luca", "Imani", "Piotr", "Wren", "Cato",
)

# role words -> the kinds of activity such a person tends to be found doing (resolved to places by `World`)
_ROLE_KINDS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("coworker", "colleague", "boss", "manager", "employee"), ("work", "socialize", "eat")),
    (("friend", "buddy", "pal"), ("socialize", "relax", "exercise", "explore")),
    (("family", "sister", "brother", "mother", "father", "parent", "child", "son", "daughter",
      "wife", "husband", "partner", "spouse", "uncle", "aunt", "cousin"), ("sleep", "errand", "relax")),
    (("shopkeeper", "vendor", "merchant", "baker", "grocer"), ("errand",)),
    (("barista", "waiter", "waitress", "cook"), ("eat", "socialize")),
    (("bartender", "innkeeper", "barkeep"), ("socialize",)),
    (("librarian", "scholar", "teacher", "professor", "student", "mentor"), ("study", "socialize")),
    (("priest", "monk", "nun", "healer", "elder"), ("reflect", "relax")),
    (("neighbour", "neighbor"), ("sleep", "errand", "relax")),
)

DEFAULT_CAST = (
    ("friend", 40, "An easy-going friend who always has time for a drink."),
    ("coworker", 15, "Works near you and knows the office gossip."),
    ("neighbour", 10, "Lives close by; nods hello most mornings."),
)


def haunts_for(role: str, world: World, rng: random.Random) -> list[str]:
    """Place keys where someone of this role is likely to be found in this world."""
    lowered = role.lower()
    for words, kinds in _ROLE_KINDS:
        if any(w in lowered for w in words):
            keys = [k for kind in kinds for k in world.places_affording(kind)]
            keys = list(dict.fromkeys(keys))
            if keys:
                return keys
            break
    pool = [k for k, p in world.places.items() if "work" not in p.affords] or list(world.places)
    return rng.sample(pool, min(3, len(pool)))


def assign_glyph(name: str, taken: set[str]) -> str:
    """A lowercase letter for the map: the name's own letters first, then any free one."""
    for ch in [c.lower() for c in name if c.isalpha()] + list("abcdefghijklmnopqrstuvwxyz"):
        if ch.isascii() and ch not in taken:
            return ch
    return "?"


def make_npc(name: str, role: str, note: str, affinity: int, place: str | None,
             taken: set[str], rng: random.Random, world: World, haunts: list[str] | None = None) -> Npc:
    glyph = assign_glyph(name, taken)
    taken.add(glyph)
    slug = "".join(c for c in name.lower() if c.isalnum()) or glyph
    return Npc(id=slug, name=name, role=role, note=note, affinity=affinity, place=place,
               glyph=glyph, haunts=haunts or haunts_for(role, world, rng))


def default_cast(person_name: str, rng: random.Random, world: World) -> list[Npc]:
    pool = [n for n in NAME_POOL if n.lower() != person_name.lower()]
    rng.shuffle(pool)
    taken: set[str] = set()
    cast = []
    for (role, affinity, note), name in zip(DEFAULT_CAST, pool, strict=False):
        cast.append(make_npc(name, role, note, affinity, None, taken, rng, world))
    return cast


def add_npc(npcs: list[Npc], name: str, role: str, note: str, affinity: int,
            place: str | None, rng: random.Random, world: World) -> Npc | None:
    """Add a newcomer (deduplicated by name, capped). Returns the NPC if one was added."""
    if len(npcs) >= MAX_NPCS or any(n.name.lower() == name.lower() for n in npcs):
        return None
    taken = {n.glyph for n in npcs}
    npc = make_npc(name, role, note, affinity, world.lookup(place), taken, rng, world)
    npcs.append(npc)
    return npc


def apply_npc_life(npc: Npc, life: NpcLife, world: World) -> None:
    """Install what the demi-god wrote about this person's day."""
    stops = []
    for entry in life.routine[:10]:
        minute = parse_minute_of_day(entry.at)
        if minute is not None:
            stops.append(RoutineStop(minute=minute, place=world.lookup(entry.place) if entry.place else None))
    stops.sort(key=lambda s: s.minute)
    if stops:
        npc.routine = stops
    if life.doing.strip():
        npc.doing = life.doing.strip()[:120]


def routine_place(npc: Npc, t: int, world: World) -> str | None:
    """Where a routine puts the person at time `t` (the last stop at or before now, wrapping past midnight)."""
    now = minute_of_day(t)
    current = None
    for stop in npc.routine:
        if stop.minute <= now:
            current = stop
    if current is None:
        current = npc.routine[-1]  # before the first stop: still where yesterday's last stop left them
    return current.place if world.has(current.place) else None


def move_npcs(npcs: list[Npc], t: int, rng: random.Random, world: World) -> None:
    """Townsfolk with a routine follow it; the rest drift hourly between their haunts."""
    hourly = minute_of_day(t) % 60 == 0
    hour = minute_of_day(t) // 60
    for npc in npcs:
        if npc.place is not None and not world.has(npc.place):
            npc.place = None  # their place was swept away
        if npc.routine:
            npc.place = routine_place(npc, t, world)
            continue
        if not hourly:
            continue
        if hour >= 22 or hour < 7:
            npc.place = None
            continue
        if npc.place is not None and rng.random() > 0.4:
            continue  # stays put
        haunts = [h for h in npc.haunts if world.has(h)] or list(world.places)
        work = [h for h in haunts if "work" in world.places[h].affords]
        working = not is_weekend(t) and 8 <= hour < 17 and bool(work)
        options = work * 3 + haunts if working else haunts
        npc.place = rng.choice(options)
