"""The demi-god's verdict: a strictly bounded description of what happens next.

Both demi-gods (rule-based and LLM) emit a `Decision`. Anything an LLM returns is parsed
leniently by `parse_decision`: unusable parts are dropped instead of failing the whole turn.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mortal.core.constants import TRIGGERS
from mortal.core.models import LifeState
from mortal.core.world import WorldPatch


def _bounded(limit: float):
    def check(v: float) -> float:
        return max(-limit, min(limit, float(v)))

    return check


class Effects(BaseModel):
    """Deltas applied to the person's gauges. Bounded so one reply cannot wreck a life."""

    model_config = ConfigDict(extra="ignore")
    energy: float = 0.0
    hunger: float = 0.0
    social: float = 0.0
    fun: float = 0.0
    health: float = 0.0
    mood: float = 0.0
    money: float = 0.0

    @field_validator("energy", "hunger", "social", "fun", "health", "mood", mode="after")
    @classmethod
    def _need_bounds(cls, v: float) -> float:
        return _bounded(40)(v)

    @field_validator("money", mode="after")
    @classmethod
    def _money_bounds(cls, v: float) -> float:
        return _bounded(500)(v)


class RelChange(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(max_length=60)
    delta: int = 0

    @field_validator("delta", mode="after")
    @classmethod
    def _bounds(cls, v: int) -> int:
        return max(-30, min(30, v))


class NewNpc(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=60)
    role: str = Field("acquaintance", max_length=60)
    note: str = Field("", max_length=300)
    place: str | None = None
    affinity: int = Field(10, ge=-100, le=100)


class PlanEntry(BaseModel):
    """One scheduled activity. `at` is "HH:MM" (24h) or "now"."""

    model_config = ConfigDict(extra="ignore")
    at: str
    place: str
    kind: str
    activity: str = Field("", max_length=200)


class LifeEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(max_length=300)
    importance: int = Field(2, ge=1, le=5)


class Profile(BaseModel):
    """A change to the person's livelihood (a job lost, a promotion). Either field may be omitted."""

    model_config = ConfigDict(extra="ignore")
    occupation: str | None = Field(None, max_length=80)
    wage_per_hour: float | None = None

    @field_validator("wage_per_hour", mode="after")
    @classmethod
    def _wage_bounds(cls, v: float | None) -> float | None:
        return None if v is None else max(0.0, min(1000.0, float(v)))


class RoutineEntry(BaseModel):
    """From `at` ("HH:MM") a townsperson is at `place` (a place key, or null for away/asleep)."""

    model_config = ConfigDict(extra="ignore")
    at: str
    place: str | None = None


class NpcLife(BaseModel):
    """What one townsperson does today. Replaces their routine; omitted people carry on as before."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(max_length=60)
    doing: str = Field("", max_length=120)
    routine: list[RoutineEntry] = Field(default_factory=list)


class WorldUpdate(BaseModel):
    """Everything that happens to the world itself (as opposed to the person): weather, terrain,
    buildings, and the townsfolk's own lives."""

    model_config = ConfigDict(extra="ignore")
    weather: str | None = Field(None, max_length=40)
    patch: WorldPatch | None = None
    npcs: list[NpcLife] = Field(default_factory=list)
    arrivals: list[NewNpc] = Field(default_factory=list)
    departures: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.weather or (self.patch and not self.patch.is_empty()) or self.npcs
                    or self.arrivals or self.departures)


class Decision(BaseModel):
    model_config = ConfigDict(extra="ignore")
    narration: str = Field("", max_length=600)
    thought: str = Field("", max_length=300)
    comment: str = Field("", max_length=300)  # the demi-god's own voice, in character
    effects: Effects = Field(default_factory=Effects)
    relationships: list[RelChange] = Field(default_factory=list)
    new_npcs: list[NewNpc] = Field(default_factory=list)
    plan: list[PlanEntry] = Field(default_factory=list)
    life_event: LifeEvent | None = None
    profile: Profile | None = None
    summary: str | None = Field(None, max_length=1200)
    death: str | None = Field(None, max_length=200)
    world: WorldUpdate | None = None


_LIST_FIELDS: dict[str, type[BaseModel]] = {
    "relationships": RelChange,
    "new_npcs": NewNpc,
    "plan": PlanEntry,
}
_LIST_CAPS = {"relationships": 6, "new_npcs": 3, "plan": 14}
_SCALAR_CAPS = {"narration": 600, "thought": 300, "comment": 300, "summary": 1200, "death": 200}


def _parse_list(raw: Any, model: type[BaseModel], cap: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in raw[:cap] if isinstance(raw, list) else []:
        try:
            items.append(model.model_validate(item).model_dump())
        except ValidationError:
            continue
    return items


def _parse_npcs(raw: Any) -> list[dict[str, Any]]:
    """Townsfolk lives, one bad stop never costing the whole person."""
    out: list[dict[str, Any]] = []
    for item in raw[:8] if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        routine = _parse_list(item.get("routine"), RoutineEntry, 12)
        try:
            out.append(NpcLife.model_validate({**item, "routine": routine}).model_dump())
        except ValidationError:
            continue
    return out


def _parse_world_update(raw: Any) -> dict[str, Any] | None:
    """Keep every valid part of an untrusted world update; drop the rest."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    weather = raw.get("weather")
    if isinstance(weather, str) and weather.strip():
        out["weather"] = weather.strip()[:40]
    patch = raw.get("patch")
    if isinstance(patch, dict):
        try:
            out["patch"] = WorldPatch.model_validate(patch).model_dump()
        except ValidationError:
            pass
    out["npcs"] = _parse_npcs(raw.get("npcs"))
    out["arrivals"] = _parse_list(raw.get("arrivals"), NewNpc, 2)
    departures = raw.get("departures")
    out["departures"] = [d.strip()[:60] for d in departures[:2] if isinstance(d, str) and d.strip()] \
        if isinstance(departures, list) else []
    return out


def parse_decision(obj: Any) -> Decision:
    """Build a Decision from an untrusted JSON value, keeping whatever parts are valid."""
    if not isinstance(obj, dict):
        raise ValueError("decision must be a JSON object")  # noqa: TRY004 - callers catch ValueError
    clean: dict[str, Any] = {}
    for key, cap in _SCALAR_CAPS.items():
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            clean[key] = value.strip()[:cap]
    for key, model in _LIST_FIELDS.items():
        raw = obj.get(key)
        if not isinstance(raw, list):
            continue
        items = []
        for item in raw[: _LIST_CAPS[key]]:
            try:
                items.append(model.model_validate(item).model_dump())
            except ValidationError:
                continue
        clean[key] = items
    update = _parse_world_update(obj.get("world"))
    if update is not None:
        clean["world"] = update
    for key, model in (("effects", Effects), ("life_event", LifeEvent), ("profile", Profile)):
        raw = obj.get(key)
        if isinstance(raw, dict):
            try:
                clean[key] = model.model_validate(raw).model_dump()
            except ValidationError:
                if key == "effects":  # keep the numeric fields that do parse
                    partial = {}
                    for field_name in Effects.model_fields:
                        try:
                            partial[field_name] = Effects.model_validate({field_name: raw[field_name]}).model_dump()[field_name]
                        except (ValidationError, KeyError):
                            continue
                    clean[key] = partial
    return Decision.model_validate(clean)


@dataclass
class DecisionRequest:
    """Everything a demi-god needs to decide: why it was asked, and the world as it stands."""

    trigger: str
    state: LifeState
    rng: random.Random
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger {self.trigger!r}")
