"""Request bodies (responses are plain dicts built by the engine views)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from mortal.core.models import GodSettings


class GodMessage(BaseModel):
    kind: Literal["event", "nudge"] = "event"
    text: str = Field(min_length=1, max_length=600)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text must not be blank")
        return v


class SpeedUpdate(BaseModel):
    speed: float = Field(gt=0, le=60)


class DemigodUpdate(BaseModel):
    mood: str = Field(min_length=1, max_length=80)
    objectives: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("objectives")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        cleaned = [o.strip() for o in v if o and o.strip()]
        if any(len(o) > 160 for o in cleaned):
            raise ValueError("each objective must be 160 characters or fewer")
        return cleaned

    def to_settings(self) -> GodSettings:
        return GodSettings(mood=self.mood.strip(), objectives=self.objectives)
