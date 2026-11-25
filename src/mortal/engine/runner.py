"""Real-time driver: runs a life's ticks on the event loop and fans frames out to viewers.

Concurrency model: one `asyncio.Lock` serialises everything that mutates the life (a tick, or
processing god's words). Viewers never read under the lock; they are woken by an `Event` and
build their own frame from the current state, so a slow browser can never stall the simulation
or lose log lines (each viewer keeps its own cursor into the log).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from mortal.config import Settings
from mortal.core.clock import story_day
from mortal.core.models import GodSettings, LifeState, LLMChoice
from mortal.engine.demigod import DemigodService
from mortal.engine.life import LifeEngine
from mortal.engine.store import LifeStore
from mortal.engine.views import frame

log = logging.getLogger(__name__)

MIN_SPEED, MAX_SPEED = 0.2, 60.0  # ticks per second


class Subscriber:
    def __init__(self, cursor: int):
        self.event = asyncio.Event()
        self.cursor = cursor


class LifeRunner:
    def __init__(self, state: LifeState, demigod: DemigodService, settings: Settings,
                 store: LifeStore | None = None, speed: float = 2.0):
        self.demigod = demigod
        self.store = store
        self.speed = speed
        self.running = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._subs: set[Subscriber] = set()
        self.engine = LifeEngine(state, demigod, settings, on_change=self.notify, on_checkpoint=self.checkpoint)

    @property
    def state(self) -> LifeState:
        return self.engine.state

    # ------------------------------------------------------------------ viewers
    def notify(self) -> None:
        for sub in self._subs:
            sub.event.set()

    def subscribe(self, history: int = 150) -> Subscriber:
        sub = Subscriber(cursor=max(0, self.state.next_seq - 1 - history))
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    def runtime(self) -> dict:
        s = self.state
        return {
            "running": self.running and s.alive, "speed": self.speed, "thinking": self.engine.thinking,
            "provider": self.demigod.provider_label(s), "last_source": s.last_source,
            "pending": len(s.pending), "breaker_open": s.llm_cooldown > 0,
        }

    def frame_for(self, sub: Subscriber | None = None, tail: int | None = None) -> dict:
        since = sub.cursor if sub else 0
        data, last = frame(self.state, runtime=self.runtime(), since_seq=since, tail=tail)
        if sub:
            sub.cursor = last
        return data

    def summary(self) -> dict:
        s = self.state
        return {"id": s.id, "name": s.person.name, "age": s.person.age, "day": story_day(s.t) + 1,
                "alive": s.alive, "running": self.running and s.alive, "mood": s.god.mood,
                "created_at": s.created_at}

    # ------------------------------------------------------------------ persistence
    def checkpoint(self) -> None:
        if self.store is None:
            return
        try:
            self.store.save(self.state)
        except OSError as exc:  # a full disk must not kill the story
            log.warning("could not save life %s: %s", self.state.id, exc)

    # ------------------------------------------------------------------ controls
    def play(self) -> None:
        if not self.state.alive:
            return
        self.running = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name=f"life-{self.state.id}")
        self.notify()

    def pause(self) -> None:
        self.running = False
        self.checkpoint()
        self.notify()

    def set_speed(self, speed: float) -> None:
        self.speed = max(MIN_SPEED, min(MAX_SPEED, float(speed)))
        self.notify()

    async def step_once(self) -> None:
        async with self._lock:
            await self.engine.step()
        self.notify()

    async def submit_divine(self, kind: str, text: str) -> None:
        """God speaks. Heard at once, even while time is paused (time itself does not advance)."""
        if not self.state.alive:
            return
        self.engine.queue_divine(kind, text)
        async with self._lock:
            await self.engine.process_pending()
        self.notify()

    def set_god(self, god: GodSettings) -> None:
        old = self.state.god
        self.state.god = god
        if god.mood != old.mood:
            self.state.add_log("system", f"God sets the demi-god's mood: {god.mood}.")
        if god.objectives != old.objectives:
            self.state.add_log("system", "God sets the demi-god's objectives: "
                               + ("; ".join(god.objectives) or "none") + ".")
        self.checkpoint()
        self.notify()

    def set_llm(self, choice: LLMChoice) -> None:
        self.state.llm = choice
        self.state.llm_failures = 0
        self.state.llm_cooldown = 0
        self.state.add_log("system", f"Demi-god backend: {self.demigod.provider_label(self.state)}.")
        self.checkpoint()
        self.notify()

    async def stop(self) -> None:
        self.running = False
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.checkpoint()

    # ------------------------------------------------------------------ the loop
    async def _loop(self) -> None:
        try:
            while self.running and self.state.alive:
                async with self._lock:
                    await self.engine.step()
                self.notify()
                await asyncio.sleep(1.0 / self.speed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("life %s crashed", self.state.id)
            self.state.add_log("system", "The simulation hit an internal error and paused. Check the server log.")
        finally:
            self.running = False
            self.checkpoint()
            self.notify()
