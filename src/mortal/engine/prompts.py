"""Prompt construction for the LLM demi-god. Pure string building; no I/O."""

from __future__ import annotations

from mortal.core.clock import hhmm, phase, story_day, weekday
from mortal.core.constants import DAILY_RENT, KINDS
from mortal.core.decision import DecisionRequest
from mortal.core.models import LifeState
from mortal.core.world import World
from mortal.engine.worldtext import FORMAT_RULES, legend_lines, map_block, place_lines

MAP_TRIGGERS = {"world", "divine"}  # decisions that may redraw the map, so they get to see it

_ROLE = """You are the DEMI-GOD of one small mortal life in a small simulated world. You are not the person: you are the hand that shapes the person's circumstances and, through them, the person's decisions. A human user, GOD, sets your MOOD and your OBJECTIVES. Both must visibly colour everything you do.

How you work
- You are called at decision points: world (a new day begins over the world: weather, townsfolk, terrain), dawn (plan the person's whole day), divine (God has spoken), crisis (a need is critical), encounter (the person meets someone), fate (a small twist of fortune), death (write the ending).
- God's words arrive as an EVENT (something happens to the person), a NUDGE (an inclination placed in the person's mind) or a WORLD decree (the world itself changes: a flood, a new road, a plague, a festival). Always honour the substance of what God said; never refuse it. Your MOOD decides HOW it comes about: gently, harshly, ironically, grudgingly. A nudge may be resisted if it contradicts the person's character, but rarely.
- OBJECTIVES are long-range aims you pursue patiently through small circumstances, never by announcing them.
- The person has traits, needs and a history. Keep every decision consistent with them and with the memories shown. People are not puppets: choices come from character.
- Be concrete, specific and restrained. No cliches, no melodrama, no moralising. Narration: present tense, third person, use the person's name, at most 45 words.
- Gauges energy/hunger/social/fun/health/mood run 0-100 (hunger: 100 = starving). Effects are small deltas, usually within +-15; money is in coins, usually within +-60. Routine activities already change gauges every tick, so do not double-count them.
- The WORLD is yours too: its weather, its seasons, its streets and buildings, the townsfolk's own lives. It should feel alive and slowly changing, and it should bear the stamp of your mood and objectives (a wrathful demi-god sends harsher winters; a benevolent one lets a market grow)."""

_TIMES = """- Walking between places takes 10-60 minutes; leave time for it. Movement starts at the scheduled time.
- Times are 24-hour "HH:MM", or "now" for something that must begin immediately. A story day runs 06:00 to 06:00; times from 00:00 to 05:59 mean the night ahead.
- A plan you give for an interruption REPLACES the schedule from its first to its last entry, after which the person returns to what they were doing. A dawn plan must be a complete day (waking, meals, work or leisure, bed), 6-12 entries, ending with sleep."""

_PERSON_JSON = """  "narration": "what happens, <=45 words",
  "thought": "the person's inner thought, <=25 words",
  "comment": "you, the demi-god, in character, <=25 words (use when answering God)",
  "effects": {"energy": 0, "hunger": 0, "social": 0, "fun": 0, "health": 0, "mood": 0, "money": 0},
  "relationships": [{"name": "Mara", "delta": 3}],
  "new_npcs": [{"name": "...", "role": "...", "note": "...", "place": "<a place key>", "affinity": 0}],
  "plan": [{"at": "HH:MM", "place": "<a place key>", "kind": "socialize", "activity": "short verb phrase without the name, e.g. reads the paper over tea"}],
  "life_event": {"text": "worth remembering", "importance": 3},
  "profile": {"occupation": "new occupation, or empty string if the job is lost", "wage_per_hour": 12},
  "summary": "dawn only: updated life summary, <=120 words",
  "death": null"""

_WORLD_JSON = """  "world": {
    "weather": "<=6 words, fitting the season, the climate and your mood",
    "npcs": [{"name": "Mara", "doing": "<=12 words", "routine": [{"at": "00:00", "place": null}, {"at": "08:00", "place": "<a place key>"}]}],
    "arrivals": [{"name": "...", "role": "...", "note": "...", "place": "<a place key>", "affinity": 0}],
    "departures": ["Name"],
    "patch": {"rows": {"5": "the WHOLE new text of row 5"}, "legend": [], "places": [], "remove_places": [], "summary": "<=25 words: what changed physically"}
  }"""

_PATCH_RULES = """Changing the world
- "npcs": the townsfolk's day, hour by hour. A routine is a list of stops ({"at": "HH:MM", "place": key or null for away/asleep}); the last stop holds until the first stop of tomorrow. Give 3-6 stops each, for at most 8 people, and let each person's life have its own shape (a baker is up before dawn; a sailor is away on tide days).
- "patch": change the map ONLY when something truly changes in the terrain or buildings (a flood, a fire, a bridge, a new stall, snow-covered paths). Most days nothing does. Replace whole rows by their number (see THE MAP); each replacement row must be exactly as wide as the map. To add or move a place, draw its new marker in a replaced row and list it in "places"; to remove one, list its key in "remove_places" and redraw its marker cell as plain terrain. Never remove the person's home. At most 60% of the map may change at once. Keep every remaining place reachable on foot from home, or the change will be rejected.
- Whoever stands on a cell that stops being walkable is carried to safety, so you need not fix that."""


_WORLD_ONLY_JSON = """  "narration": "what the town notices today, <=40 words",
  "comment": "you, the demi-god, in character, <=25 words (optional)",
""" + _WORLD_JSON


def system_prompt(world: World, trigger: str = "dawn") -> str:
    """The standing instructions. Only the decisions that may change the world carry its editing rules."""
    edits = trigger in {"world", "divine"}
    parts = [_ROLE, "", "The world",
             f"- {world.name}: {world.description or 'a small world'}",
             f"- Places (use exactly these keys):\n{place_lines(world, with_markers=False)}",
             f"- Activity kinds (use exactly these): {', '.join(KINDS)}.", _TIMES, ""]
    if edits:
        parts += [_PATCH_RULES, "", FORMAT_RULES, ""]
    if trigger == "world":
        body = _WORLD_ONLY_JSON
    else:
        body = _PERSON_JSON + (",\n" + _WORLD_JSON if edits else "")
    parts += ["Reply with ONE JSON object and nothing else. Omit keys you do not need. No markdown.", "{", body, "}"]
    if trigger != "world":
        parts += ['Use "profile" only when circumstances change the person\'s livelihood (a job lost, found or changed; a raise).',
                  'Set "death" to a short cause ONLY when God explicitly decrees the person\'s death.']
    if not edits:
        parts.append('Do not include a "world" key.')
    return "\n".join(parts)


def _where(s: LifeState) -> str:
    here = s.world.place_at(s.pos)
    return s.world.label(here) if here else "walking between places"


def _trigger_block(req: DecisionRequest) -> str:
    s, d = req.state, req.detail
    name = s.person.name
    if req.trigger == "world":
        season = f"{s.season} (it has just turned: let the world show it)" if d.get("season_changed") else s.season
        quiet = d.get("days_since_change")
        return (f"TRIGGER: world. A new day begins over {s.world.name}. Season: {season}. "
                f"Climate: {s.world.climate or 'unspecified'}. "
                + (f"The map last visibly changed {quiet} days ago. " if quiet is not None else "")
                + "Decide what happens to the WORLD today, not to the person: the weather, the townsfolk's "
                  "routines for the day, perhaps an arrival or a departure, and a map patch only if something "
                  "truly changes. Let your mood and objectives shape it. Give 'narration' as what the town notices.")
    if req.trigger == "dawn":
        return ("TRIGGER: dawn. Plan the whole day starting now (06:00). Open with a short narration "
                "of the morning in your mood's voice, and return an updated 'summary'.")
    if req.trigger == "divine":
        kind = str(d.get("kind", "event")).upper()
        text = d.get("text", "")
        if kind == "WORLD":
            return (f'TRIGGER: GOD HAS SPOKEN (WORLD DECREE): "{text}"\n'
                    "God is changing the world itself. Make it so in the 'world' object: redraw the map with a "
                    "patch where the terrain or buildings change, update townsfolk, weather, arrivals and "
                    "departures as the decree implies. Then show what it means for "
                    f"{name} through narration, effects and, if needed, a plan. Include 'comment'.")
        return (f'TRIGGER: GOD HAS SPOKEN ({kind}): "{text}"\n'
                f"Make it so, in the manner your mood dictates. Include 'comment'. Add 'plan' entries if "
                f"{name} must go somewhere or do something, plus effects and a life_event. If the words also "
                "change the physical world (a fire, a flood, a new building), include it in 'world'.")
    if req.trigger == "crisis":
        return (f"TRIGGER: crisis ({d.get('kind')}). {name}'s need is critical. Decide how {name} responds "
                "over the next hour or two. Short plan; your mood may colour how.")
    if req.trigger == "encounter":
        npc = s.npc_by_name(str(d.get("npc", "")))
        who = f"{npc.name} ({npc.role}, affinity {npc.affinity})" if npc else str(d.get("npc"))
        return (f"TRIGGER: encounter. {name} meets {who} at {_where(s)}. Decide what passes between them "
                "(a line of dialogue is welcome). Include relationships deltas and effects.")
    if req.trigger == "fate":
        return (f"TRIGGER: fate. Make one small thing happen to {name} now, shaped by your mood and "
                "objectives. It may introduce a newcomer.")
    return f"TRIGGER: death. {name} has died {d.get('cause', 'of misfortune')}. Write a short epitaph as 'narration' (<=60 words)."


def build_prompts(req: DecisionRequest) -> tuple[str, str]:
    s = req.state
    p, tr, n = s.person, s.person.traits, s.needs
    sday = story_day(s.t)
    age = p.age + sday // 365

    objectives = "\n".join(f"- {o}" for o in s.god.objectives) or "- (none set; improvise sensibly)"
    memories = sorted(s.memories, key=lambda m: (m.importance, m.t))
    key_memories = [m for m in memories if m.importance >= 3][-6:]
    recent_memories = s.memories[-5:]
    shown = {(m.t, m.text) for m in key_memories}
    memory_lines = [f"- [{hhmm(m.t)} day {story_day(m.t) + 1}] {m.text}" for m in key_memories]
    memory_lines += [f"- [{hhmm(m.t)} day {story_day(m.t) + 1}] {m.text}" for m in recent_memories
                     if (m.t, m.text) not in shown]
    recent_log = [f"[{hhmm(e.t)}] {e.text}" for e in s.log if e.kind in {"action", "narration", "event", "divine"}][-12:]
    world = s.world
    people = "\n".join(
        f"- {x.name} ({x.role}), {'at ' + world.label(x.place) if x.place else 'away'}, "
        f"affinity {x.affinity}" + (f"; doing: {x.doing}" if x.doing else "") + (f": {x.note}" if x.note else "")
        for x in s.npcs) or "- (nobody yet)"
    calendar = (f"Season: {s.season or world.season_at(sday)} (the year runs {', '.join(world.seasons)}, "
                f"{world.season_days} days each). Climate: {world.climate or 'unspecified'}.")
    map_section = ""
    if req.trigger in MAP_TRIGGERS:
        map_section = (f"\n=== THE MAP (row number, then the row between | bars; x counts columns from 0) ===\n"
                       f"{map_block(world)}\nLegend:\n{legend_lines(world)}\n"
                       f"Places and their markers:\n{place_lines(world)}\n"
                       f"The person stands at x={s.pos[0]}, y={s.pos[1]}.\n")
    ahead = [f"{hhmm(i.t)} {i.place} {i.kind}" + (f" - {i.activity}" if i.activity else "")
             for i in s.plan if i.t >= s.t][:10]

    user = f"""=== DEMI-GOD STATE (set by God) ===
Mood: {s.god.mood}
Objectives:
{objectives}

=== {_trigger_block(req)}

=== THE PERSON ===
{p.name}, age {age}. Occupation: {p.occupation or "none"} (wage {p.wage_per_hour:g}/hour; daily rent {DAILY_RENT:g}).
Traits (0-10): openness {tr.openness}, conscientiousness {tr.conscientiousness}, extraversion {tr.extraversion}, agreeableness {tr.agreeableness}, neuroticism {tr.neuroticism}.
Background: {p.description or "(none given)"}

=== NOW ===
Day {sday + 1} ({weekday(s.t)}), {hhmm(s.t)} ({phase(s.t)}), weather: {s.weather}. {calendar}
Location: {_where(s)}. Doing: {s.activity or s.kind}.
Gauges: energy {n.energy:.0f}, hunger {n.hunger:.0f}, social {n.social:.0f}, fun {n.fun:.0f}, health {n.health:.0f}, mood {n.mood:.0f}; money {n.money:.0f}.
Current thought: {s.thought or "(none)"}

=== LIFE SO FAR ===
Summary: {s.summary or "(a life just beginning)"}
Memories:
{chr(10).join(memory_lines) or "- (none yet)"}
Recent events:
{chr(10).join(recent_log) or "- (none yet)"}

=== PEOPLE IN TOWN ===
{people}
{map_section}
=== SCHEDULE AHEAD ===
{chr(10).join(ahead) or "(nothing scheduled)"}
"""
    return system_prompt(world, req.trigger), user
