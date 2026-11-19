"""The other people in the town: creation, glyphs and daily movement."""

from __future__ import annotations

import random

from mortal.core.clock import is_weekend, minute_of_day
from mortal.core.constants import PLACE_KEYS
from mortal.core.models import MAX_NPCS, Npc

NAME_POOL = (
    "Mara", "Ilya", "Tomas", "Noor", "Bea", "Kofi", "Lena", "Ravi", "Sora", "Hugo", "Anya", "Dev",
    "Mina", "Otto", "Zane", "Yusuf", "Elsa", "Kenji", "Rosa", "Luca", "Imani", "Piotr", "Wren", "Cato",
)

_ROLE_HAUNTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("coworker", "colleague", "boss", "manager", "employee"), ("work", "tavern", "cafe")),
    (("friend", "buddy", "pal"), ("cafe", "park", "tavern", "river")),
    (("family", "sister", "brother", "mother", "father", "parent", "child", "son", "daughter",
      "wife", "husband", "partner", "spouse", "uncle", "aunt", "cousin"), ("home", "market", "park")),
    (("shopkeeper", "vendor", "merchant", "baker", "grocer"), ("market",)),
    (("barista", "waiter", "waitress", "cook"), ("cafe",)),
    (("bartender", "innkeeper", "barkeep"), ("tavern",)),
    (("librarian", "scholar", "teacher", "professor", "student", "mentor"), ("library", "cafe")),
    (("priest", "monk", "nun", "healer", "elder"), ("temple", "park")),
    (("neighbour", "neighbor"), ("home", "market", "park")),
)

DEFAULT_CAST = (
    ("friend", 40, "An easy-going friend who always has time for a drink."),
    ("coworker", 15, "Works near you and knows the office gossip."),
    ("neighbour", 10, "Lives close by; nods hello most mornings."),
)


def haunts_for(role: str, rng: random.Random) -> list[str]:
    lowered = role.lower()
    for words, places in _ROLE_HAUNTS:
        if any(w in lowered for w in words):
            return list(places)
    return rng.sample([p for p in PLACE_KEYS if p != "work"], 3)


def assign_glyph(name: str, taken: set[str]) -> str:
    """A lowercase letter for the map: the name's own letters first, then any free one."""
    for ch in [c.lower() for c in name if c.isalpha()] + list("abcdefghijklmnopqrstuvwxyz"):
        if ch.isascii() and ch not in taken:
            return ch
    return "?"


def make_npc(name: str, role: str, note: str, affinity: int, place: str | None,
             taken: set[str], rng: random.Random, haunts: list[str] | None = None) -> Npc:
    glyph = assign_glyph(name, taken)
    taken.add(glyph)
    slug = "".join(c for c in name.lower() if c.isalnum()) or glyph
    return Npc(id=slug, name=name, role=role, note=note, affinity=affinity, place=place,
               glyph=glyph, haunts=haunts or haunts_for(role, rng))


def default_cast(person_name: str, rng: random.Random) -> list[Npc]:
    pool = [n for n in NAME_POOL if n.lower() != person_name.lower()]
    rng.shuffle(pool)
    taken: set[str] = set()
    cast = []
    for (role, affinity, note), name in zip(DEFAULT_CAST, pool, strict=False):
        cast.append(make_npc(name, role, note, affinity, None, taken, rng))
    return cast


def add_npc(npcs: list[Npc], name: str, role: str, note: str, affinity: int,
            place: str | None, rng: random.Random) -> Npc | None:
    """Add a newcomer (deduplicated by name, capped). Returns the NPC if one was added."""
    if len(npcs) >= MAX_NPCS or any(n.name.lower() == name.lower() for n in npcs):
        return None
    taken = {n.glyph for n in npcs}
    npc = make_npc(name, role, note, affinity, place if place in PLACE_KEYS else None, taken, rng)
    npcs.append(npc)
    return npc


def move_npcs(npcs: list[Npc], t: int, rng: random.Random) -> None:
    """Hourly drift: everyone sleeps at night, works by day, and otherwise visits their haunts."""
    if minute_of_day(t) % 60 != 0:
        return
    hour = minute_of_day(t) // 60
    for npc in npcs:
        if hour >= 22 or hour < 7:
            npc.place = None
            continue
        if npc.place is not None and rng.random() > 0.4:
            continue  # stays put
        working = not is_weekend(t) and 8 <= hour < 17 and "work" in npc.haunts
        options = ["work"] * 3 + npc.haunts if working else (npc.haunts or list(PLACE_KEYS))
        npc.place = rng.choice([p for p in options if p in PLACE_KEYS] or ["park"])
