"""Command line: `mortal serve` (web UI) and `mortal simulate` (headless, prints the story)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from mortal.config import Settings
from mortal.core.models import GodSettings, LifeSpec, LLMChoice, Person, WorldSpec
from mortal.engine.registry import LifeRegistry


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    if args.data_dir:
        os.environ["MORTAL_DATA_DIR"] = args.data_dir
    from mortal.api.app import create_app

    app = create_app(Settings.from_env())
    print(f"Mortal is listening on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


async def _simulate(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    registry = LifeRegistry(settings, persist=False)
    spec = LifeSpec(
        person=Person(name=args.name, age=args.age, occupation=args.occupation, description=args.description),
        world=WorldSpec(description=args.world, size=args.world_size),
        god=GodSettings(mood=args.mood, objectives=args.objective),
        llm=LLMChoice(provider=args.provider),
        seed=args.seed,
    )
    runner = await registry.create(spec)
    print(f"World: {runner.state.world.name} ({runner.state.world.source})")
    if args.map:
        print("\n".join(runner.state.world.rows))
    for message in args.say:
        await runner.submit_divine("event", message)
    shown = 0
    for _ in range(args.days * 144):
        await runner.engine.step()
        log = runner.state.log
        for entry in log[shown:]:
            day, minute = entry.t // 1440 + 1, entry.t % 1440
            print(f"D{day} {minute // 60:02d}:{minute % 60:02d}  {entry.kind:<9} {entry.text}")
        shown = len(log)
        if not runner.state.alive:
            break
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mortal", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--data-dir", help="where lives are saved (default: ./mortal_data or $MORTAL_DATA_DIR)")
    serve.set_defaults(func=_serve)

    sim = sub.add_parser("simulate", help="run a life headlessly and print the story")
    sim.add_argument("--days", type=int, default=2)
    sim.add_argument("--name", default="Ayo")
    sim.add_argument("--age", type=int, default=34)
    sim.add_argument("--occupation", default="clerk")
    sim.add_argument("--description", default="")
    sim.add_argument("--world", default="", help="describe the world; the LLM demi-god draws it (offline: built-in town)")
    sim.add_argument("--world-size", choices=["small", "medium", "large"], default="medium")
    sim.add_argument("--map", action="store_true", help="print the world's ASCII map before the story")
    sim.add_argument("--mood", default="benevolent")
    sim.add_argument("--objective", action="append", default=[])
    sim.add_argument("--provider", choices=["auto", "stub", "gemini", "openai"], default="auto")
    sim.add_argument("--seed", type=int, default=None)
    sim.add_argument("--say", action="append", default=[], help="god's event, spoken at the start (repeatable)")
    sim.set_defaults(func=lambda a: asyncio.run(_simulate(a)))

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
