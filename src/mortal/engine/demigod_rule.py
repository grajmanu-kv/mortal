"""The offline demi-god: no network, no key, deterministic for a given seed.

It is a small rule system, not a language model. It reads the *same* inputs an LLM would
(mood, objectives, traits, needs, god's words) and produces the *same* `Decision` shape, so
the engine cannot tell the difference. Its understanding of god's free text is keyword-level;
for real comprehension, switch the life to Gemini or a local model.
"""

from __future__ import annotations

import random
import re

from mortal.core.clock import is_weekend, phase, story_day, weekday
from mortal.core.constants import WEATHERS
from mortal.core.decision import (
    Decision,
    DecisionRequest,
    Effects,
    LifeEvent,
    NewNpc,
    PlanEntry,
    Profile,
    RelChange,
    WorldUpdate,
)
from mortal.core.models import LifeState
from mortal.core.world import World
from mortal.engine.npcs import NAME_POOL

# --------------------------------------------------------------------------- mood

_MOOD_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wrathful", ("wrath", "angry", "anger", "furious", "vengeful", "cruel", "harsh", "merciless", "stern")),
    ("mischievous", ("mischiev", "trick", "capricious", "chaos", "playful", "whimsical", "prank", "impish")),
    ("melancholic", ("melanchol", "sad", "sorrow", "brood", "gloom", "lonely", "wistful", "grief")),
    ("indifferent", ("indiffer", "bored", "apathet", "detached", "cold", "aloof", "distant")),
    ("benevolent", ("benevol", "kind", "gentle", "generous", "loving", "merciful", "warm", "joyful", "caring")),
)

# (good, bad, neutral, odd) event weights per mood
_EVENT_WEIGHTS = {
    "benevolent": (0.65, 0.05, 0.25, 0.05),
    "mischievous": (0.25, 0.25, 0.15, 0.35),
    "wrathful": (0.05, 0.65, 0.30, 0.0),
    "melancholic": (0.20, 0.30, 0.50, 0.0),
    "indifferent": (0.10, 0.10, 0.75, 0.05),
    "neutral": (0.25, 0.25, 0.45, 0.05),
}


def mood_class(mood: str) -> str:
    lowered = mood.lower()
    for cls, stems in _MOOD_WORDS:
        if any(stem in lowered for stem in stems):
            return cls
    return "neutral"


_OMENS = {
    "benevolent": "The demi-god looks on kindly; small mercies are being arranged.",
    "mischievous": "Something amused watches from just beyond the rooftops.",
    "wrathful": "The sky has a grievance and has not yet said what it is.",
    "melancholic": "The light arrives slowly, as if reluctant to be noticed.",
    "indifferent": "The day starts without ceremony; nobody is watching closely.",
    "neutral": "The day opens like any other, full of unwritten hours.",
}
_COMMENTS = {
    "benevolent": "I smooth the path where I can.",
    "mischievous": "Let us see what they make of this.",
    "wrathful": "Let them feel the weight of it.",
    "melancholic": "All things pass. Some pass slowly.",
    "indifferent": "Noted.",
    "neutral": "So it is written.",
}

# --------------------------------------------------------------------------- event pools

# text, effect deltas. {n} = the person's name.
_GOOD = (
    ("{n} finds a folded banknote under a bench; the world feels briefly generous.", {"money": 20, "mood": 8}),
    ("A stranger praises {n}'s work, unprompted.", {"mood": 10, "fun": 5, "social": 4}),
    ("A smell of fresh bread follows {n} down the street; hunger eases, spirits lift.", {"hunger": -10, "mood": 5}),
    ("A letter from an old friend arrives, long and funny.", {"social": 15, "mood": 8}),
    ("The light turns golden at exactly the right moment; {n} stops to watch.", {"fun": 12, "mood": 6}),
)
_BAD = (
    ("A pipe bursts at home; repairs eat into the savings.", {"money": -35, "mood": -8}),
    ("{n} loses an hour to a pointless argument over nothing.", {"energy": -8, "mood": -10, "social": -5}),
    ("Rain finds the hole in {n}'s coat.", {"mood": -6, "health": -2}),
    ("A small mistake is noticed by exactly the wrong person.", {"mood": -10, "social": -4}),
    ("A pickpocket's elbow; a lighter wallet.", {"money": -25, "mood": -8}),
)
_NEUTRAL = (
    ("Nothing happens, which is its own kind of event.", {}),
    ("A dog follows {n} for a block, then loses interest.", {"fun": 3}),
    ("Somewhere a clock runs fast; the town doesn't mind.", {}),
    ("{n} catches a stranger's eye, and both look away at once.", {"social": 2}),
)
_ODD = (
    ("A goat has got into the market and is negotiating with a vendor.", {"fun": 8}),
    ("Every bell in town rings once, for nobody's reason.", {"fun": 5, "mood": 2}),
    ("{n} swears the river hummed a tune just now.", {"fun": 6}),
)
_TWISTS = (
    "though not in the way {n} expected.",
    "and a pigeon seems to have noticed.",
    "with a flourish nobody asked for.",
)

# --------------------------------------------------------------------------- god's words

_POSITIVE = {"win", "wins", "won", "gift", "gifts", "find", "finds", "found", "love", "loves", "joy", "rich", "praise", "praised", "cure", "cured", "heal", "heals", "promotion", "promoted", "fortune", "lucky", "luck", "happy", "happiness", "reward", "rewarded", "inherit", "inherits", "inheritance", "friend", "meets", "romance", "success", "wealth", "party", "celebrate", "feast", "bonus", "raise", "kindness", "laugh", "laughs", "comfort", "peace"}
_NEGATIVE = {"lose", "loses", "lost", "theft", "steal", "steals", "stolen", "rob", "robbed", "fire", "sick", "sickness", "ill", "illness", "accident", "storm", "fail", "fails", "failure", "fight", "fired", "layoff", "betray", "betrays", "betrayal", "grief", "hurt", "injury", "injured", "flood", "debt", "scandal", "heartbreak", "breakup", "curse", "cursed", "plague", "famine", "starve", "hunger"}


def _is_death_decree(text: str, person_name: str) -> bool:
    """True only for an explicit decree that the *person* dies ("he dies", "kill Ayo")."""
    subject = "|".join(["he", "she", "they", "him", "her", "them", re.escape(person_name.lower())])
    pattern = (rf"\b(?:{subject})\s+(?:dies|die|is killed|passes away|perishes|drops dead)\b"
               rf"|\bkill\s+(?:{subject})\b")
    return re.search(pattern, text.lower()) is not None


_JOB_LOSS = re.compile(r"\b(?:fired|laid off|lays? off|unemployed|quits?|loses? (?:his|her|their|the|a) (?:job|work|position))\b")
_PROMOTION = re.compile(r"\b(?:promot\w+|raise|pay rise|bonus)\b")
# Words in God's text that point at a kind of place (resolved against the world's own places).
_KIND_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sleep", ("home", "house", "apartment", "bed")),
    ("work", ("work", "office", "job", "workplace")),
    ("socialize", ("cafe", "coffee", "tavern", "pub", "bar", "inn")),
    ("study", ("library", "books", "book")),
    ("errand", ("market", "shop", "store")),
    ("reflect", ("temple", "church", "shrine", "chapel", "mosque", "river", "riverbank", "bridge", "lake")),
    ("relax", ("park", "garden", "forest")),
)

_OBJECTIVE_ROUTES: tuple[tuple[str, tuple[str, str]], ...] = (
    (r"love|romance|partner|friend|social|connect|people|meet|belong",
     ("socialize", "seeks company over something warm")),
    (r"learn|study|read|knowledge|wisdom|skill|book|understand",
     ("study", "loses track of time among the shelves")),
    (r"rich|wealth|money|career|promot|success|ambition|business",
     ("work", "puts in extra hours, chasing something larger")),
    (r"peace|calm|spirit|faith|meaning|soul|god|forgive",
     ("reflect", "sits in the quiet and lets thoughts settle")),
    (r"health|fit|strong|run|exercise|body",
     ("exercise", "keeps moving until the lungs complain")),
    (r"explore|adventure|travel|discover|curious|art|creat|paint|write|music",
     ("explore", "wanders, collecting impressions")),
)

_WORK_LINES = ("settles in and gets to work", "works through the queue of the day", "takes on the day's tasks")
_BREAKFAST = ("makes breakfast and watches the street wake", "eats slowly, still half in a dream",
              "has tea and toast by the window")
_DINNER = ("cooks something simple for dinner", "eats dinner and tidies the kitchen", "has a quiet dinner")
_UNWIND = ("unwinds with no particular agenda", "lets the evening go slack", "puts the day down on the table")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


_NAME_STOPWORDS = {"the", "and", "old", "new", "big", "this", "that", "for", "with"}


def _name_words(world: World, key: str) -> set[str]:
    place = world.places[key]
    words = set(re.findall(r"[a-z]{3,}", f"{place.name} {place.display()} {key.replace('_', ' ')}".lower()))
    return words - _NAME_STOPWORDS


def _place_in(text: str, world: World) -> str | None:
    """The place God's words point at: by a name used in this world, else by what kind of place it sounds like."""
    tokens = _words(text)
    for key in world.places:
        for w in _name_words(world, key):
            if any(t == w or (len(t) >= 4 and (t.startswith(w) or w.startswith(t))) for t in tokens):
                return key
    for kind, words in _KIND_WORDS:
        if any(w in tokens for w in words):
            return world.home if kind == "sleep" else world.pick(kind, avoid=world.home)
    return None


def _scaled(effects: dict, factor: float) -> Effects:
    return Effects(**{k: v * factor for k, v in effects.items()})


def _current_place(state: LifeState) -> str | None:
    return state.world.place_at(state.pos)


class RuleDemigod:
    def decide(self, req: DecisionRequest) -> Decision:
        handler = {
            "world": self._world, "dawn": self._dawn, "divine": self._divine, "crisis": self._crisis,
            "encounter": self._encounter, "fate": self._fate, "death": self._death,
        }[req.trigger]
        return handler(req)

    # ------------------------------------------------------------------ world
    def _world(self, req: DecisionRequest) -> Decision:
        """Offline, the world only has weather: it cannot invent terrain, buildings or townsfolk lives."""
        weather = req.rng.choices(WEATHERS, weights=(40, 25, 20, 10, 5))[0]
        return Decision(world=WorldUpdate(weather=weather))

    # ------------------------------------------------------------------ dawn
    def _leisure(self, s: LifeState, rng: random.Random, salt: int) -> tuple[str, str, str]:
        """(place, kind, wording) for a free hour, drawn from what this world offers."""
        world, cls = s.world, mood_class(s.god.mood)
        matches = [route for obj in s.god.objectives for pattern, route in _OBJECTIVE_ROUTES
                   if re.search(pattern, obj.lower())]
        if matches:
            kind, text = matches[(story_day(s.t) + salt) % len(matches)]
            place = world.pick(kind, rng, avoid=world.home)
            if place:
                return place, kind, text
        tr = s.person.traits
        choices: list[tuple[str, str]] = []
        if tr.extraversion >= 6:
            choices += [("socialize", "trades stories with whoever is there"),
                        ("socialize", "lingers over a drink, open to company")]
        if tr.openness >= 6:
            choices += [("study", "follows a curiosity through the shelves"),
                        ("explore", "wanders, collecting impressions")]
        if tr.conscientiousness >= 6:
            choices += [("exercise", "does a disciplined round of exercise")]
        if tr.neuroticism >= 6:
            choices += [("reflect", "sits in the quiet, sorting worries"),
                        ("reflect", "watches the world go by and lets thoughts drift")]
        if tr.agreeableness >= 6:
            choices += [("errand", "runs errands, chatting with everyone on the way")]
        if cls == "melancholic":
            choices = [c for c in choices if c[0] in {"reflect", "study", "explore", "exercise", "relax"}]
        options = [(world.pick(kind, rng, avoid=world.home), kind, text) for kind, text in choices]
        options = [(p, k, t) for p, k, t in options if p]
        if not options:
            options = [(world.pick("relax", rng, avoid=world.home) or world.home, "relax", "strolls with no destination"),
                       (world.pick("reflect", rng, avoid=world.home) or world.home, "reflect",
                        "watches the world slide by"),
                       (world.home, "relax", "potters about at home")]
        return rng.choice(options)

    def _dawn(self, req: DecisionRequest) -> Decision:
        s, rng = req.state, req.rng
        p, tr, n = s.person, s.person.traits, s.needs
        cls = mood_class(s.god.mood)
        weekend = is_weekend(s.t)
        works = bool(p.occupation.strip()) and not weekend
        broke = n.money < 40
        world = s.world
        home = world.home
        workplace = world.pick("work") or home  # no workplace in this world: work from home (half pay)
        plan: list[tuple[str, str, str, str]] = []

        if n.energy < 35:
            plan.append(("06:00", home, "sleep", "sleeps in; the body is still owed rest"))
            up = "07:30" if works else "08:30"
        else:
            up = "06:00"
        plan.append((up, home, "eat", rng.choice(_BREAKFAST)))

        eat_out = [k for k in world.places_affording("eat") if k != home]
        lunch_place = rng.choice(eat_out) if eat_out and not broke and (tr.extraversion >= 6 or tr.openness >= 6) else home
        if works:
            plan += [("08:30", workplace, "work", rng.choice(_WORK_LINES)),
                     ("12:30", lunch_place, "eat", "breaks for lunch"),
                     ("13:30", workplace, "work", "returns to the afternoon's work")]
        else:
            place, kind, text = self._leisure(s, rng, 0)
            plan += [("09:00", place, kind, text), ("12:30", lunch_place, "eat", "has a slow lunch")]
            place, kind, text = self._leisure(s, rng, 1)
            plan.append(("14:00", place, kind, text))
        place, kind, text = self._leisure(s, rng, 2)
        plan.append(("17:30", place, kind, text))
        plan += [("19:30", home, "eat", rng.choice(_DINNER)),
                 ("20:45", home, "relax", rng.choice(_UNWIND)),
                 ("22:30", home, "sleep", "goes to bed")]

        day = story_day(s.t) + 1
        narration = f"Day {day}, {weekday(s.t)}. {s.weather.capitalize()}. {_OMENS[cls]}"
        return Decision(
            narration=narration,
            plan=[PlanEntry(at=at, place=pl, kind=k, activity=a) for at, pl, k, a in plan],
        )

    # ------------------------------------------------------------------ divine
    def _divine(self, req: DecisionRequest) -> Decision:
        s, rng = req.state, req.rng
        text = str(req.detail.get("text", "")).strip()
        kind = req.detail.get("kind", "event")
        cls = mood_class(s.god.mood)
        name = s.person.name
        tokens = _words(text)
        pos, neg = len(tokens & _POSITIVE), len(tokens & _NEGATIVE)
        sentiment = "good" if pos > neg else "bad" if neg > pos else "neutral"
        world = s.world
        place = _place_in(text, world)
        comment = _COMMENTS[cls]
        sentence = text.rstrip(".! ")

        if kind == "nudge":
            tr = s.person.traits
            chance = 0.6 + (tr.conscientiousness - 5) * 0.03
            chance += {"benevolent": 0.15, "mischievous": -0.1, "wrathful": -0.15}.get(cls, 0.0)
            if rng.random() < chance:
                quiet = "reflect" if sentiment == "neutral" and tr.neuroticism >= 6 else "relax"
                dest = place or world.pick(quiet, rng, avoid=world.home) or world.home
                return Decision(
                    narration=f"The idea takes root: {name} follows it.",
                    thought=f"A thought arrives, unbidden: {sentence}.",
                    comment=comment,
                    effects=Effects(fun=4),
                    plan=[PlanEntry(at="now", place=dest, kind=world.default_kind(dest), activity=sentence[:120])],
                    life_event=LifeEvent(text=f"Felt a sudden pull: {sentence}", importance=2),
                )
            return Decision(
                narration=f"The notion drifts through {name}'s mind and out again, unheeded.",
                thought="A stray idea. Not today.",
                comment="Even gods are sometimes ignored." if cls != "wrathful" else "Defiance. I will remember it.",
            )

        # an event
        base = {"good": {"money": 25, "mood": 12, "fun": 8}, "bad": {"money": -25, "mood": -12, "energy": -8},
                "neutral": {"fun": 6, "mood": 2}}[sentiment]
        factor = 1.0
        narration = f"{sentence}."
        if cls == "benevolent" and sentiment == "bad":
            factor, comment = 0.5, "I soften the blow."
        elif cls == "wrathful":
            factor = 1.4 if sentiment == "bad" else 0.6
        elif cls == "mischievous":
            narration = f"{sentence} {rng.choice(_TWISTS).format(n=name)}"
        death = "by divine decree" if _is_death_decree(text, name) else None
        profile = None
        person = s.person
        if person.occupation and _JOB_LOSS.search(text.lower()):
            profile = Profile(occupation="", wage_per_hour=0)
            place = None  # "job" in "loses his job" is not an instruction to go to work
        elif person.occupation and _PROMOTION.search(text.lower()):
            profile = Profile(wage_per_hour=round(person.wage_per_hour * 1.25, 2))
        plan = ([PlanEntry(at="now", place=place, kind=world.default_kind(place), activity="")]
                if place and not death else [])
        return Decision(
            narration=narration, comment=comment, effects=_scaled(base, factor), plan=plan, death=death,
            profile=profile,
            life_event=LifeEvent(text=f"By god's word: {sentence}", importance=3),
        )

    # ------------------------------------------------------------------ crisis
    def _crisis(self, req: DecisionRequest) -> Decision:
        s, rng = req.state, req.rng
        what = req.detail.get("kind", "hunger")
        name, now, tr = s.person.name, s.t, s.person.traits
        here = _current_place(s)
        cls = mood_class(s.god.mood)
        world, home = s.world, s.world.home
        if what == "hunger":
            spot = here if here in world.places_affording("eat") and s.needs.money > 10 else home
            return Decision(narration=f"Hunger interrupts everything. {name} turns toward food.",
                            plan=[PlanEntry(at="now", place=spot, kind="eat", activity="stops everything to eat")])
        if what == "energy":
            return Decision(
                narration=f"{name} is running on fumes and heads home to rest.",
                plan=[PlanEntry(at="now", place=home, kind="sleep", activity="naps to recover")])
        if what == "health":
            return Decision(narration=f"Aches pile up. {name} stops pushing and rests.",
                            plan=[PlanEntry(at="now", place=home, kind="relax", activity="rests and nurses the aches")],
                            effects=Effects(mood=-2))
        if what == "money":
            if not is_weekend(now) and s.person.occupation.strip():
                plan = [PlanEntry(at="now", place=world.pick("work") or home, kind="work",
                                  activity="picks up extra work to cover the shortfall")]
            else:
                plan = [PlanEntry(at="now", place=world.pick("errand") or home, kind="errand",
                                  activity="hunts for bargains and odd jobs")]
            return Decision(narration=f"The numbers do not add up. {name} sets about fixing that.",
                            plan=plan, thought="Money is tight; something has to give.")
        # mood
        social = world.pick("socialize", rng, avoid=home)
        if tr.extraversion >= 6 and social:
            plan = [PlanEntry(at="now", place=social, kind="socialize", activity="seeks company to lift the mood")]
        else:
            quiet = [k for k, p in world.places.items() if k != home and ({"reflect", "relax"} & set(p.affords))]
            plan = [PlanEntry(at="now", place=rng.choice(quiet) if quiet else home, kind="reflect",
                              activity="sits alone and breathes")]
        grit = "A hard push is the cure, the demi-god insists." if cls == "wrathful" else ""
        return Decision(narration=f"{name}'s spirits are low; a change of scene is needed. {grit}".strip(),
                        plan=plan, effects=Effects(mood=-3 if cls == "wrathful" else 4))

    # ------------------------------------------------------------------ encounter
    def _encounter(self, req: DecisionRequest) -> Decision:
        s, rng = req.state, req.rng
        npc = s.npc_by_name(str(req.detail.get("npc", "")))
        if npc is None:
            return Decision()
        cls = mood_class(s.god.mood)
        at = s.world.at_phrase(_current_place(s))
        name, other, aff = s.person.name, npc.name, npc.affinity
        if aff >= 40:
            text = f"{at.capitalize()}, {name} and {other} fall into easy talk; the hours loosen."
            eff, delta = {"social": 18, "fun": 8, "mood": 6}, 3
        elif aff >= 0:
            text = f"{name} exchanges pleasantries with {other} {at}."
            eff, delta = {"social": 8, "fun": 3}, 2
        else:
            text = f"A brief, stiff exchange with {other} {at}."
            eff, delta = {"social": 2, "mood": -3}, 1
        comment = ""
        if cls == "wrathful":
            delta, eff["mood"] = -3, eff.get("mood", 0) - 4
            text += " Old irritations resurface."
        elif cls == "mischievous" and rng.random() < 0.5:
            text += " A small misunderstanding turns out to be hilarious."
            eff["fun"] = eff.get("fun", 0) + 6
        elif cls == "benevolent":
            delta += 1
        return Decision(narration=text, comment=comment, effects=Effects(**eff),
                        relationships=[RelChange(name=other, delta=delta)])

    # ------------------------------------------------------------------ fate
    def _fate(self, req: DecisionRequest) -> Decision:
        s, rng = req.state, req.rng
        cls = mood_class(s.god.mood)
        good, bad, neutral, odd = _EVENT_WEIGHTS[cls]
        pool = rng.choices((_GOOD, _BAD, _NEUTRAL, _ODD), weights=(good, bad, neutral, odd))[0]
        text, eff = rng.choice(pool)
        text = text.format(n=s.person.name)
        effects = Effects(**eff)
        big = sum(abs(v) for v in eff.values()) >= 20
        decision = Decision(narration=text, effects=effects)
        if big:
            decision.life_event = LifeEvent(text=text, importance=2)
        # now and then a stranger wanders into the story
        if rng.random() < 0.08 and len(s.npcs) < 8:
            used = {n.name for n in s.npcs} | {s.person.name}
            fresh = [x for x in NAME_POOL if x not in used]
            if fresh:
                newcomer = rng.choice(fresh)
                decision.new_npcs = [NewNpc(name=newcomer, role="stranger", note="New in town.",
                                            place=rng.choice(list(s.world.places)), affinity=0)]
                decision.narration += f" A stranger, {newcomer}, is new in town."
        return decision

    # ------------------------------------------------------------------ death
    def _death(self, req: DecisionRequest) -> Decision:
        s = req.state
        cause = req.detail.get("cause", "of misfortune")
        day = story_day(s.t) + 1
        cls = mood_class(s.god.mood)
        tail = {
            "benevolent": "The demi-god lingers a moment, tenderly.",
            "mischievous": "Somewhere, a laugh catches in the throat of the world.",
            "wrathful": "The demi-god turns away, unmoved.",
            "melancholic": "The demi-god sighs; it always does.",
            "indifferent": "The demi-god has already looked elsewhere.",
            "neutral": "The town carries on.",
        }[cls]
        return Decision(narration=f"{s.person.name} dies {cause} on day {day}, in the {phase(s.t)}. {tail}",
                        life_event=LifeEvent(text=f"Died {cause}.", importance=5))
