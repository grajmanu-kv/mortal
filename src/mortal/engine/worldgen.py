"""Drawing worlds: the demi-god turns God's description into an ASCII world, the engine checks it,
and every problem found is sent back to the demi-god until the world is playable.

No repair is done by the engine itself: a world that fails every attempt is replaced wholesale by the
built-in town (and the chronicle says so). This keeps all invention with the LLM and all guarantees
(connectivity, anchors, bounds) with deterministic code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from mortal.config import Settings
from mortal.core.constants import KINDS
from mortal.core.models import LifeSpec
from mortal.core.world import (
    MAX_PLACES,
    MIN_PLACES,
    World,
    WorldPatch,
    builtin_world,
    parse_world,
)
from mortal.engine.worldtext import FORMAT_RULES, legend_lines, map_block, place_lines
from mortal.errors import LLMError
from mortal.llm.base import Provider
from mortal.llm.jsonutil import extract_json

log = logging.getLogger(__name__)

MAX_ECHO = 12_000  # characters of a rejected reply sent back for correction

EXAMPLE_WORLD = """{
  "name": "Gullhaven", "description": "A windy harbour village under a lighthouse.", "climate": "cool, damp, windy",
  "seasons": ["thaw", "bright", "gale", "frost"], "season_days": 90,
  "legend": [
    {"ch": "~", "name": "sea", "walkable": false, "style": "water"},
    {"ch": ".", "name": "cobbles", "walkable": true, "style": "path"},
    {"ch": "#", "name": "stone wall", "walkable": false, "style": "wall"}
  ],
  "map": [
    "~~~~~~~~~~~~~~~~~~~~",
    "~~~~~~~~~~~~~~~~~~~~",
    "~~~~~~~~~~~~~~~~~~~~",
    "####..........######",
    "#1.....3......4....#",
    "#..................#",
    "####..2.......######",
    "~~~~~~~~~~~~~~~~~~~~"
  ],
  "home": "cottage",
  "places": [
    {"key": "cottage", "name": "the keeper's cottage", "prep": "at", "marker": "1", "affords": ["relax", "sleep", "eat"], "meal_cost": 1, "visit_cost": 0},
    {"key": "lamp", "name": "the lighthouse", "prep": "at", "marker": "2", "affords": ["work", "reflect"]},
    {"key": "quay", "name": "the quay", "prep": "on", "marker": "3", "affords": ["explore", "errand", "socialize"], "visit_cost": 0.5},
    {"key": "inn", "name": "the Drowned Rat inn", "prep": "at", "marker": "4", "affords": ["socialize", "eat"], "meal_cost": 3, "visit_cost": 1}
  ]
}"""

WORLD_SYSTEM = f"""You are the DEMI-GOD, and you are now drawing the WORLD in which one mortal will live. GOD, the human user, has described it. You also have a MOOD and OBJECTIVES (set by God); they colour what you build without ever being announced: a wrathful demi-god draws a harsher, leaner world, a benevolent one a kinder and richer one, and objectives appear as features of the place (an objective of friendship might yield a crowded square and a well-lit tavern).

Draw the world as an ASCII map, a legend and a list of places. Reply with ONE JSON object and nothing else (no markdown):
{{
  "name": "short name of the world or settlement",
  "description": "the world in a nutshell, <=60 words",
  "climate": "<=20 words",
  "seasons": ["1 to 6 season names, in order, that fit the climate"], "season_days": 60,
  "legend": [{{"ch": ".", "name": "...", "walkable": true, "style": "path"}}],
  "map": ["row 0", "row 1", "..."],
  "home": "key of the mortal's home place",
  "places": [{{"key": "...", "name": "the ...", "prep": "at", "marker": "1", "affords": ["relax", "sleep", "eat"], "meal_cost": 1, "visit_cost": 0}}]
}}

{FORMAT_RULES}

Further rules
- Use the whole canvas you are given (a few rows or columns either way is fine). Draw terrain that fits the description: edges, water, cliffs, walls, streets, fields, rooms, whatever the world is made of. The mortal need not live in a town: a ship, a desert caravan, a castle or a forest camp are all valid worlds, as long as people can walk between {MIN_PLACES} to {MAX_PLACES} places (aim for 7-14).
- Make the world serve the mortal: somewhere their occupation can be practised, somewhere to meet people, somewhere quiet, somewhere to eat other than home. Keep the places' kinds varied: {", ".join(KINDS)}.
- Keep every walkable route at least one cell wide, and make sure the markers of all places are linked by walkable glyphs.
- If God's description is empty, invent a world that suits the mortal.

Format example only (tiny and unrelated to your world; draw your own):
{EXAMPLE_WORLD}"""


def _person_block(spec: LifeSpec) -> str:
    p, t = spec.person, spec.person.traits
    people = "; ".join(f"{n.name} ({n.role})" for n in spec.npcs) or "(none named)"
    return (f"{p.name}, age {p.age}. Occupation: {p.occupation or 'none'}.\n"
            f"Traits (0-10): openness {t.openness}, conscientiousness {t.conscientiousness}, "
            f"extraversion {t.extraversion}, agreeableness {t.agreeableness}, neuroticism {t.neuroticism}.\n"
            f"Background: {p.description or '(none given)'}\nPeople in their life: {people}")


def world_prompts(spec: LifeSpec, *, previous: str | None = None, problems: list[str] | None = None) -> tuple[str, str]:
    width, height = spec.world.dimensions
    objectives = "\n".join(f"- {o}" for o in spec.god.objectives) or "- (none set)"
    user = f"""=== GOD'S DESCRIPTION OF THE WORLD ===
{spec.world.description.strip() or "(none given: invent a world that suits the mortal below)"}
Canvas: about {width} columns by {height} rows.

=== THE DEMI-GOD (you) ===
Mood: {spec.god.mood}
Objectives:
{objectives}

=== THE MORTAL WHO WILL LIVE HERE ===
{_person_block(spec)}
"""
    if problems:
        user += ("\n=== YOUR PREVIOUS ATTEMPT WAS REJECTED ===\nThe world's validator found these problems:\n"
                 + "\n".join(f"- {p}" for p in problems[:12])
                 + f"\n\nYour previous reply (correct it; keep what was fine):\n{(previous or '')[:MAX_ECHO]}\n\n"
                   "Return the corrected, complete JSON object.\n")
    return WORLD_SYSTEM, user


@dataclass
class WorldResult:
    world: World
    notes: list[str] = field(default_factory=list)  # lines for the chronicle, in order
    drawn: bool = False  # True when the LLM's world is in use


async def build_world(provider: Provider | None, spec: LifeSpec, settings: Settings) -> WorldResult:
    """The world a new life begins in: the LLM's drawing if it can be made playable, else the built-in town."""
    described = bool(spec.world.description.strip())
    if provider is None:
        note = ["No LLM backend is configured, so the described world cannot be drawn; the built-in town stands in."
                ] if described else []
        return WorldResult(builtin_world(), note)

    problems: list[str] = []
    previous = ""
    for attempt in range(1, max(1, settings.world_attempts) + 1):
        system, user = world_prompts(spec, previous=previous, problems=problems)
        try:
            text = await asyncio.to_thread(provider.complete, system, user)
        except asyncio.CancelledError:
            raise
        except LLMError as exc:
            log.warning("world generation failed on attempt %d: %s", attempt, exc)
            return WorldResult(builtin_world(), [(f"The demi-god could not be reached to draw the world ({str(exc)[:120]}); "
                                                 "the built-in town stands in.")])
        previous = text
        try:
            world, problems = parse_world(extract_json(text))
        except LLMError:
            world, problems = None, ["the reply was not valid JSON; reply with one JSON object only"]
        if world is not None:
            tail = f" (it took {attempt} attempts)" if attempt > 1 else ""
            return WorldResult(world, [f"The demi-god draws the world: {world.name}{tail}."], drawn=True)
        log.info("world attempt %d rejected: %s", attempt, "; ".join(problems[:3]))
    reason = problems[0] if problems else "unknown problem"
    return WorldResult(builtin_world(), [
        (f"The demi-god's world could not be made playable after {settings.world_attempts} attempts "
        f"({reason[:140]}); the built-in town stands in.")])


# ---------------------------------------------------------------------- repairing a change in flight
PATCH_REPAIR_SYSTEM = f"""You are the DEMI-GOD, reshaping a world that already exists. A change you wrote for its map was rejected by the world's validator. Correct it so that it still achieves what you meant (or a smaller version of it), or give up by replying {{"patch": null}}.

Reply with ONE JSON object and nothing else: {{"patch": {{"rows": {{"5": "the WHOLE new text of row 5"}}, "legend": [], "places": [], "remove_places": [], "summary": "<=25 words"}}}}
Replace whole rows by number; each must be exactly as wide as the map. The first rule of every change: all remaining places must stay reachable on foot from home, and each place's marker must appear exactly once on the map.

{FORMAT_RULES}"""


def patch_repair_prompts(world: World, patch: WorldPatch, problems: list[str], intent: str) -> tuple[str, str]:
    user = f"""=== WHAT YOU MEANT TO HAPPEN ===
{intent or "(not stated)"}

=== THE MAP AS IT IS NOW (row number, then the row between | bars; {world.width} columns) ===
{map_block(world)}
Legend:
{legend_lines(world)}
Places and their markers (home is '{world.home}'):
{place_lines(world)}

=== YOUR REJECTED PATCH ===
{json.dumps(patch.model_dump(exclude_defaults=True), ensure_ascii=True)[:MAX_ECHO]}

=== PROBLEMS ===
{chr(10).join(f"- {p}" for p in problems[:12])}
"""
    return PATCH_REPAIR_SYSTEM, user


async def repair_patch(provider: Provider, world: World, patch: WorldPatch, problems: list[str],
                       intent: str) -> WorldPatch | None:
    """One round of asking the demi-god to fix a rejected patch. None = it gave up or failed."""
    system, user = patch_repair_prompts(world, patch, problems, intent)
    try:
        text = await asyncio.to_thread(provider.complete, system, user)
        raw = extract_json(text)
    except asyncio.CancelledError:
        raise
    except LLMError as exc:
        log.warning("patch repair failed: %s", exc)
        return None
    raw_patch = raw.get("patch") if isinstance(raw, dict) else None
    if not isinstance(raw_patch, dict):
        return None
    try:
        fixed = WorldPatch.model_validate(raw_patch)
    except ValueError:
        return None
    return None if fixed.is_empty() else fixed
