"""FastAPI application: JSON control endpoints, an SSE stream, and the static web UI."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from mortal import __version__
from mortal.api.schemas import DemigodUpdate, GodMessage, SpeedUpdate
from mortal.config import Settings
from mortal.core.constants import KINDS, MOOD_PRESETS, PLACE_KEYS, PLACE_LABELS
from mortal.core.models import LifeSpec, LLMChoice
from mortal.engine.registry import LifeRegistry
from mortal.errors import NotFoundError
from mortal.llm.factory import available_providers

log = logging.getLogger(__name__)
STATIC = resources.files("mortal") / "web" / "static"
KEEPALIVE_SECONDS = 15.0


def create_app(settings: Settings | None = None, *, persist: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    registry = LifeRegistry(settings, persist=persist)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        count = registry.load()
        if count:
            log.info("restored %d saved life/lives (paused)", count)
        yield
        await registry.shutdown()

    app = FastAPI(title="Mortal", version=__version__, lifespan=lifespan)
    app.state.registry = registry

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/meta")
    async def meta() -> dict:
        return {
            "places": [{"key": k, "label": PLACE_LABELS[k]} for k in PLACE_KEYS],
            "kinds": list(KINDS),
            "moods": list(MOOD_PRESETS),
            "providers": available_providers(settings),
            "speeds": [0.5, 1, 2, 5, 10, 30],
        }

    # ------------------------------------------------------------------ lives
    @app.get("/api/lives")
    async def list_lives() -> list[dict]:
        return registry.summaries()

    @app.post("/api/lives", status_code=201)
    async def create_life(spec: LifeSpec) -> dict:
        runner = registry.create(spec)
        return runner.summary()

    @app.get("/api/lives/{life_id}")
    async def get_life(life_id: str) -> dict:
        return registry.get(life_id).frame_for(tail=200)

    @app.delete("/api/lives/{life_id}", status_code=204)
    async def delete_life(life_id: str) -> None:
        await registry.delete(life_id)

    @app.get("/api/lives/{life_id}/export")
    async def export_life(life_id: str) -> JSONResponse:
        state = registry.get(life_id).state
        return JSONResponse(json.loads(state.model_dump_json()),
                            headers={"Content-Disposition": f'attachment; filename="mortal-{life_id}.json"'})

    # ------------------------------------------------------------------ time controls
    @app.post("/api/lives/{life_id}/play")
    async def play(life_id: str) -> dict:
        runner = registry.get(life_id)
        runner.play()
        return runner.runtime()

    @app.post("/api/lives/{life_id}/pause")
    async def pause(life_id: str) -> dict:
        runner = registry.get(life_id)
        runner.pause()
        return runner.runtime()

    @app.post("/api/lives/{life_id}/step")
    async def step(life_id: str) -> dict:
        runner = registry.get(life_id)
        await runner.step_once()
        return runner.runtime()

    @app.put("/api/lives/{life_id}/speed")
    async def speed(life_id: str, body: SpeedUpdate) -> dict:
        runner = registry.get(life_id)
        runner.set_speed(body.speed)
        return runner.runtime()

    # ------------------------------------------------------------------ god and the demi-god
    @app.post("/api/lives/{life_id}/god", status_code=202)
    async def god_speaks(life_id: str, body: GodMessage) -> dict:
        runner = registry.get(life_id)
        await runner.submit_divine(body.kind, body.text)
        return runner.runtime()

    @app.put("/api/lives/{life_id}/demigod")
    async def set_demigod(life_id: str, body: DemigodUpdate) -> dict:
        runner = registry.get(life_id)
        runner.set_god(body.to_settings())
        return {"mood": runner.state.god.mood, "objectives": runner.state.god.objectives}

    @app.put("/api/lives/{life_id}/llm")
    async def set_llm(life_id: str, body: LLMChoice) -> dict:
        runner = registry.get(life_id)
        runner.set_llm(body)
        return runner.runtime()

    # ------------------------------------------------------------------ live stream
    @app.get("/api/lives/{life_id}/stream")
    async def stream(life_id: str, request: Request) -> StreamingResponse:
        runner = registry.get(life_id)

        async def events() -> AsyncIterator[str]:
            sub = runner.subscribe()
            try:
                yield f"data: {json.dumps(runner.frame_for(sub), separators=(',', ':'))}\n\n"
                while True:
                    try:
                        await asyncio.wait_for(sub.event.wait(), timeout=KEEPALIVE_SECONDS)
                    except TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": keepalive\n\n"
                        continue
                    sub.event.clear()
                    yield f"data: {json.dumps(runner.frame_for(sub), separators=(',', ':'))}\n\n"
            finally:
                runner.unsubscribe(sub)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ web UI
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(str(STATIC / "index.html"), headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app
