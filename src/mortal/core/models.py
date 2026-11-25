"""Persistent state of one life. Everything here is JSON-serialisable pydantic."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from mortal.core.constants import DAWN_MINUTE
from mortal.core.world import SIZES, World, builtin_world

MAX_LOG = 1000
MAX_MEMORIES = 200
MAX_NPCS = 12


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else min(value, hi)


class Traits(BaseModel):
    """Five-factor personality, 0-10 each (5 = average)."""

    openness: int = Field(5, ge=0, le=10)
    conscientiousness: int = Field(5, ge=0, le=10)
    extraversion: int = Field(5, ge=0, le=10)
    agreeableness: int = Field(5, ge=0, le=10)
    neuroticism: int = Field(5, ge=0, le=10)


class Person(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    age: int = Field(30, ge=0, le=120)
    occupation: str = Field("", max_length=80)
    description: str = Field("", max_length=2000)
    traits: Traits = Field(default_factory=Traits)
    wage_per_hour: float = Field(12.0, ge=0, le=1000)


class Needs(BaseModel):
    """0-100 gauges (hunger: 0 = full, 100 = starving). `money` is unbounded above."""

    energy: float = 85.0
    hunger: float = 20.0
    social: float = 55.0
    fun: float = 55.0
    health: float = 100.0
    mood: float = 60.0
    money: float = 150.0

    def clamp(self) -> None:
        for name in ("energy", "hunger", "social", "fun", "health", "mood"):
            setattr(self, name, _clamp(getattr(self, name), 0.0, 100.0))
        self.money = _clamp(self.money, -10_000.0, 1_000_000.0)


class RoutineStop(BaseModel):
    """From `minute` (of the day, 0-1439) the townsperson is at `place`; None = away or asleep."""

    minute: int = Field(ge=0, lt=1440)
    place: str | None = None


class Npc(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=60)
    role: str = Field("acquaintance", max_length=60)
    note: str = Field("", max_length=300)
    place: str | None = None  # None = away from the map (asleep, elsewhere)
    affinity: int = Field(10, ge=-100, le=100)
    glyph: str = "?"
    haunts: list[str] = Field(default_factory=list)
    last_met_t: int = -100_000
    doing: str = Field("", max_length=120)  # what they are up to, as the demi-god last described it
    routine: list[RoutineStop] = Field(default_factory=list)  # empty = wander between haunts


class PlanItem(BaseModel):
    t: int  # absolute start minute
    place: str
    kind: str
    activity: str = ""


class LogEntry(BaseModel):
    seq: int
    t: int
    kind: str
    text: str


class Memory(BaseModel):
    t: int
    text: str
    importance: int = Field(2, ge=1, le=5)


class GodSettings(BaseModel):
    """What the user (god) tells the demi-god: its temper and its long-range aims."""

    mood: str = Field("benevolent", max_length=80)
    objectives: list[str] = Field(default_factory=list, max_length=8)


class LLMChoice(BaseModel):
    provider: Literal["auto", "stub", "gemini", "openai"] = "auto"
    model: str | None = Field(None, max_length=120)


class PendingDivine(BaseModel):
    kind: Literal["event", "nudge", "world"]
    text: str = Field(min_length=1, max_length=600)


class LifeState(BaseModel):
    id: str
    seed: int
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    person: Person
    needs: Needs = Field(default_factory=Needs)
    npcs: list[Npc] = Field(default_factory=list)
    god: GodSettings = Field(default_factory=GodSettings)
    llm: LLMChoice = Field(default_factory=LLMChoice)

    t: int = DAWN_MINUTE
    pos: tuple[int, int] = (0, 0)
    kind: str = "idle"  # current activity kind ("travel" while walking)
    activity: str = ""
    thought: str = ""
    weather: str = "sunny"
    season: str = ""
    world: World = Field(default_factory=builtin_world)
    plan: list[PlanItem] = Field(default_factory=list)
    summary: str = ""
    memories: list[Memory] = Field(default_factory=list)
    log: list[LogEntry] = Field(default_factory=list)
    next_seq: int = 1
    pending: list[PendingDivine] = Field(default_factory=list)

    alive: bool = True
    cause_of_death: str | None = None
    reflex: str | None = None  # "eat" | "sleep" while the body overrules the plan
    dawned_day: int = -1  # story day whose dawn has been planned
    decisions_today: int = 0
    llm_failures: int = 0
    llm_cooldown: int = 0  # decisions left to serve from the offline demi-god after failures
    last_source: str = "rule"
    flags: dict[str, int] = Field(default_factory=dict)  # cooldown timestamps keyed by name
    current_key: str = ""  # identifies the plan item being performed (to log transitions)

    # ----- helpers -------------------------------------------------------------------
    def add_log(self, kind: str, text: str) -> LogEntry:
        entry = LogEntry(seq=self.next_seq, t=self.t, kind=kind, text=text.strip())
        self.next_seq += 1
        self.log.append(entry)
        if len(self.log) > MAX_LOG:
            del self.log[: len(self.log) - MAX_LOG]
        return entry

    def add_memory(self, text: str, importance: int = 2) -> None:
        self.memories.append(Memory(t=self.t, text=text.strip(), importance=max(1, min(5, importance))))
        if len(self.memories) > MAX_MEMORIES:
            # forget the least important of the older half first
            older = sorted(range(len(self.memories) // 2), key=lambda i: (self.memories[i].importance, i))
            del self.memories[older[0]]

    def npc_by_name(self, name: str) -> Npc | None:
        key = name.strip().lower()
        for npc in self.npcs:
            if npc.name.lower() == key or npc.id == key:
                return npc
        return None


class NpcSpec(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    role: str = Field("acquaintance", max_length=60)
    note: str = Field("", max_length=300)


class WorldSpec(BaseModel):
    """What god says about the world the person is born into. Blank = the demi-god invents one."""

    description: str = Field("", max_length=2000)
    size: Literal["small", "medium", "large"] = "medium"

    @property
    def dimensions(self) -> tuple[int, int]:
        return SIZES[self.size]


class LifeSpec(BaseModel):
    """What god provides to start a life."""

    person: Person
    world: WorldSpec = Field(default_factory=WorldSpec)
    npcs: list[NpcSpec] = Field(default_factory=list, max_length=8)
    god: GodSettings = Field(default_factory=GodSettings)
    llm: LLMChoice = Field(default_factory=LLMChoice)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
