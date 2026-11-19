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


_LIST_FIELDS: dict[str, type[BaseModel]] = {
    "relationships": RelChange,
    "new_npcs": NewNpc,
    "plan": PlanEntry,
}
_LIST_CAPS = {"relationships": 6, "new_npcs": 3, "plan": 14}
_SCALAR_CAPS = {"narration": 600, "thought": 300, "comment": 300, "summary": 1200, "death": 200}


def parse_decision(obj: Any) -> Decision:
    """Build a Decision from an untrusted JSON value, keeping whatever parts are valid."""
    if not isinstance(obj, dict):
        raise ValueError("decision must be a JSON object")
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
