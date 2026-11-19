"""Prompt construction for the LLM demi-god. Pure string building; no I/O."""

from __future__ import annotations

from mortal.core.clock import hhmm, phase, story_day, weekday
from mortal.core.constants import DAILY_RENT, KINDS, PLACE_KEYS, PLACE_LABELS
from mortal.core.decision import DecisionRequest
from mortal.core.models import LifeState
from mortal.core.townmap import get_town

SYSTEM_PROMPT = f"""You are the DEMI-GOD of one small mortal life in a tiny simulated town. You are not the person: you are the hand that shapes the person's circumstances and, through them, the person's decisions. A human user, GOD, sets your MOOD and your OBJECTIVES. Both must visibly colour everything you do.

How you work
- You are called at decision points: dawn (plan the whole day), divine (God has spoken), crisis (a need is critical), encounter (the person meets someone), fate (a small twist of fortune), death (write the ending).
- God's words arrive as an EVENT (something happens to the person) or a NUDGE (an inclination placed in the person's mind). Always honour the substance of what God said; never refuse it. Your MOOD decides HOW it comes about: gently, harshly, ironically, grudgingly. A nudge may be resisted if it contradicts the person's character, but rarely.
- OBJECTIVES are long-range aims you pursue patiently through small circumstances, never by announcing them.
- The person has traits, needs and a history. Keep every decision consistent with them and with the memories shown. People are not puppets: choices come from character.
- Be concrete, specific and restrained. No cliches, no melodrama, no moralising. Narration: present tense, third person, use the person's name, at most 45 words.
- Gauges energy/hunger/social/fun/health/mood run 0-100 (hunger: 100 = starving). Effects are small deltas, usually within +-15; money is in coins, usually within +-60. Routine activities already change gauges every tick, so do not double-count them.

The world
- Places (use exactly these keys): {", ".join(PLACE_KEYS)}.
- Activity kinds (use exactly these): {", ".join(KINDS)}.
- Walking between places takes 10-60 minutes; leave time for it. Movement starts at the scheduled time.
- Times are 24-hour "HH:MM", or "now" for something that must begin immediately. A story day runs 06:00 to 06:00; times from 00:00 to 05:59 mean the night ahead.
- A plan you give for an interruption REPLACES the schedule from its first to its last entry, after which the person returns to what they were doing. A dawn plan must be a complete day (waking, meals, work or leisure, bed), 6-12 entries, ending with sleep.

Reply with ONE JSON object and nothing else. Omit keys you do not need. No markdown.
{{
  "narration": "what happens, <=45 words",
  "thought": "the person's inner thought, <=25 words",
  "comment": "you, the demi-god, in character, <=25 words (use when answering God)",
  "effects": {{"energy": 0, "hunger": 0, "social": 0, "fun": 0, "health": 0, "mood": 0, "money": 0}},
  "relationships": [{{"name": "Mara", "delta": 3}}],
  "new_npcs": [{{"name": "...", "role": "...", "note": "...", "place": "cafe", "affinity": 0}}],
  "plan": [{{"at": "HH:MM", "place": "cafe", "kind": "socialize", "activity": "short verb phrase without the name, e.g. reads the paper over tea"}}],
  "life_event": {{"text": "worth remembering", "importance": 3}},
  "profile": {{"occupation": "new occupation, or empty string if the job is lost", "wage_per_hour": 12}},
  "summary": "dawn only: updated life summary, <=120 words",
  "death": null
}}
Use "profile" only when circumstances change the person's livelihood (a job lost, found or changed; a raise).
Set "death" to a short cause ONLY when God explicitly decrees the person's death."""


def _where(s: LifeState) -> str:
    here = get_town().place_at(s.pos)
    return PLACE_LABELS[here] if here else "walking between places"


def _trigger_block(req: DecisionRequest) -> str:
    s, d = req.state, req.detail
    name = s.person.name
    if req.trigger == "dawn":
        return ("TRIGGER: dawn. Plan the whole day starting now (06:00). Open with a short narration "
                "of the morning in your mood's voice, and return an updated 'summary'.")
    if req.trigger == "divine":
        kind = str(d.get("kind", "event")).upper()
        return (f'TRIGGER: GOD HAS SPOKEN ({kind}): "{d.get("text", "")}"\n'
                f"Make it so, in the manner your mood dictates. Include 'comment'. Add 'plan' entries if "
                f"{name} must go somewhere or do something, plus effects and a life_event.")
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
    people = "\n".join(
        f"- {x.name} ({x.role}), {'at ' + PLACE_LABELS[x.place] if x.place else 'away'}, "
        f"affinity {x.affinity}" + (f": {x.note}" if x.note else "")
        for x in s.npcs) or "- (nobody yet)"
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
Day {sday + 1} ({weekday(s.t)}), {hhmm(s.t)} ({phase(s.t)}), weather: {s.weather}.
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

=== SCHEDULE AHEAD ===
{chr(10).join(ahead) or "(nothing scheduled)"}
"""
    return SYSTEM_PROMPT, user
