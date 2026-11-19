from __future__ import annotations

import pytest

from mortal.config import Settings
from mortal.core.models import GodSettings, LifeSpec, LifeState, LLMChoice, Person, Traits
from mortal.engine.demigod import DemigodService
from mortal.engine.factory import new_life_state
from mortal.engine.life import LifeEngine


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings.from_env({"MORTAL_DATA_DIR": str(tmp_path / "data")})


@pytest.fixture
def make_state():
    def factory(*, name="Ayo", occupation="clerk", mood="benevolent", objectives=(), seed=1,
                provider="stub", traits=None, wage=12.0, description="") -> LifeState:
        spec = LifeSpec(
            person=Person(name=name, age=34, occupation=occupation, description=description,
                          traits=traits or Traits(), wage_per_hour=wage),
            god=GodSettings(mood=mood, objectives=list(objectives)),
            llm=LLMChoice(provider=provider), seed=seed,
        )
        return new_life_state(spec)

    return factory


@pytest.fixture
def make_engine(settings, make_state):
    def factory(state: LifeState | None = None, service: DemigodService | None = None, cfg: Settings | None = None):
        state = state or make_state()
        cfg = cfg or settings
        return LifeEngine(state, service or DemigodService(cfg), cfg)

    return factory


async def run_ticks(engine: LifeEngine, ticks: int) -> None:
    for _ in range(ticks):
        await engine.step()
