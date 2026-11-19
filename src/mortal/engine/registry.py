"""All lives of a server process, with persistence."""

from __future__ import annotations

from mortal.config import Settings
from mortal.core.models import LifeSpec
from mortal.engine.demigod import DemigodService
from mortal.engine.factory import new_life_state
from mortal.engine.runner import LifeRunner
from mortal.engine.store import LifeStore
from mortal.errors import NotFoundError


class LifeRegistry:
    def __init__(self, settings: Settings, *, persist: bool = True, demigod: DemigodService | None = None):
        self.settings = settings
        self.demigod = demigod or DemigodService(settings)
        self.store = LifeStore(settings.data_dir) if persist else None
        self._runners: dict[str, LifeRunner] = {}

    def load(self) -> int:
        """Restore saved lives (paused). Returns how many were loaded."""
        if self.store is None:
            return 0
        for state in self.store.load_all():
            self._runners[state.id] = LifeRunner(state, self.demigod, self.settings, self.store)
        return len(self._runners)

    def create(self, spec: LifeSpec) -> LifeRunner:
        state = new_life_state(spec)
        runner = LifeRunner(state, self.demigod, self.settings, self.store)
        self._runners[state.id] = runner
        runner.checkpoint()
        return runner

    def get(self, life_id: str) -> LifeRunner:
        try:
            return self._runners[life_id]
        except KeyError:
            raise NotFoundError(f"no such life: {life_id}") from None

    async def delete(self, life_id: str) -> None:
        runner = self.get(life_id)
        await runner.stop()
        del self._runners[life_id]
        if self.store is not None:
            self.store.delete(life_id)

    def summaries(self) -> list[dict]:
        return sorted((r.summary() for r in self._runners.values()), key=lambda s: s["created_at"], reverse=True)

    async def shutdown(self) -> None:
        for runner in list(self._runners.values()):
            await runner.stop()
