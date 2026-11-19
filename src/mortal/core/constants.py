"""Shared vocabulary of the simulation."""

from __future__ import annotations

TICK_MINUTES = 10  # one simulation tick
DAY_MINUTES = 1440
DAWN_MINUTE = 360  # a "story day" runs 06:00 -> 06:00
WALK_CELLS_PER_TICK = 6
DAILY_RENT = 25.0
TICKS_PER_DAY = DAY_MINUTES // TICK_MINUTES

PLACE_KEYS: tuple[str, ...] = (
    "home", "work", "cafe", "library", "market", "tavern", "temple", "park", "river",
)
PLACE_LABELS: dict[str, str] = {
    "home": "Home",
    "work": "Workplace",
    "cafe": "Cafe",
    "library": "Library",
    "market": "Market",
    "tavern": "Tavern",
    "temple": "Temple",
    "park": "Park",
    "river": "Riverbank",
}
PLACE_AT: dict[str, str] = {  # "... {at}" / "{At}, ..."
    "home": "at home", "work": "at work", "cafe": "at the cafe", "library": "at the library",
    "market": "at the market", "tavern": "at the tavern", "temple": "at the temple",
    "park": "in the park", "river": "by the river",
}
PLACE_TO: dict[str, str] = {  # "sets off for {to}"
    "home": "home", "work": "work", "cafe": "the cafe", "library": "the library",
    "market": "the market", "tavern": "the tavern", "temple": "the temple",
    "park": "the park", "river": "the riverbank",
}
FOOD_PLACES: tuple[str, ...] = ("home", "cafe", "tavern", "market")

# Activity kinds the engine knows how to simulate. "travel" is internal (set while walking).
KINDS: tuple[str, ...] = (
    "sleep", "eat", "work", "socialize", "relax", "exercise",
    "explore", "study", "errand", "reflect", "idle",
)
DEFAULT_KIND_FOR_PLACE: dict[str, str] = {
    "home": "relax", "work": "work", "cafe": "socialize", "library": "study",
    "market": "errand", "tavern": "socialize", "temple": "reflect",
    "park": "relax", "river": "reflect",
}
KIND_PHRASE: dict[str, str] = {  # default wording when a plan item has no description
    "sleep": "sleeps", "eat": "eats", "work": "works", "socialize": "socializes", "relax": "relaxes",
    "exercise": "exercises", "explore": "explores", "study": "studies", "errand": "runs errands",
    "reflect": "reflects", "idle": "idles",
}
# How long (minutes) an activity of this kind naturally lasts. Used to bracket interruptions.
DEFAULT_DURATION: dict[str, int] = {
    "sleep": 480, "eat": 40, "work": 240, "socialize": 90, "relax": 60,
    "exercise": 60, "explore": 90, "study": 90, "errand": 60, "reflect": 45, "idle": 30,
}

# When an interruption is the last thing scheduled, how long it lasts before the person resumes.
INTERRUPT_DURATION: dict[str, int] = {**DEFAULT_DURATION, "sleep": 90, "work": 120}

NEED_NAMES: tuple[str, ...] = ("energy", "hunger", "social", "fun", "health", "mood")
WEATHERS: tuple[str, ...] = ("sunny", "cloudy", "rain", "fog", "storm")
MOOD_PRESETS: tuple[str, ...] = (
    "benevolent", "mischievous", "wrathful", "melancholic", "indifferent",
)

LOG_KINDS: tuple[str, ...] = (
    "action", "narration", "thought", "divine", "demigod", "event", "system",
)
TRIGGERS: tuple[str, ...] = ("dawn", "divine", "crisis", "encounter", "fate", "death")
