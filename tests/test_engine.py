from __future__ import annotations

import copy

from conftest import run_ticks

from mortal.core.clock import hhmm
from mortal.core.constants import DAWN_MINUTE
from mortal.core.models import PlanItem
from mortal.core.world import builtin_world
from mortal.engine.physics import apply_tick
from mortal.engine.views import render_scene

DAY = 144  # ticks


async def test_a_week_passes_sanely(make_engine):
    engine = make_engine()
    s = engine.state
    home = builtin_world().anchor("home")
    slept_at_home = 0
    for _day in range(7):
        for tick in range(DAY):
            await engine.step()
            for gauge in ("energy", "hunger", "social", "fun", "health", "mood"):
                assert 0 <= getattr(s.needs, gauge) <= 100
            if tick == 100 and s.pos == home:  # ~22:40 + 1h: the person should be home in bed
                slept_at_home += 1
    assert s.alive
    assert slept_at_home >= 6
    assert s.needs.money > 0
    assert sum(1 for e in s.log if e.kind == "narration" and e.text.startswith("Day ")) == 7


async def test_same_seed_same_story(make_engine, make_state):
    base = make_state(seed=42, mood="mischievous", objectives=["learn deeply"])
    a, b = make_engine(copy.deepcopy(base)), make_engine(copy.deepcopy(base))
    for engine in (a, b):
        engine.queue_divine("event", "A stranger leaves a gift at the door")
        await run_ticks(engine, DAY * 3)
    assert a.state.model_dump_json() == b.state.model_dump_json()


async def test_different_seeds_diverge(make_engine, make_state):
    a = make_engine(make_state(seed=1))
    b = make_engine(make_state(seed=2))
    await run_ticks(a, DAY * 3)
    await run_ticks(b, DAY * 3)
    assert [e.text for e in a.state.log] != [e.text for e in b.state.log]


async def test_dawn_builds_a_full_day(make_engine):
    engine = make_engine()
    await engine.step()
    items = engine.state.plan
    assert len(items) >= 6
    assert items[-1].kind == "sleep" and items[0].t == DAWN_MINUTE
    assert any(i.kind == "work" for i in items)


async def test_weekend_has_no_work(make_engine):
    engine = make_engine()
    await run_ticks(engine, DAY * 5)  # advance to Saturday dawn
    await engine.step()
    assert hhmm(engine.state.t) == "06:10"
    assert not any(i.kind == "work" for i in engine.state.plan if i.t >= engine.state.t)


async def test_job_loss_decree_ends_income_and_work(make_engine):
    engine = make_engine()
    await engine.step()
    engine.queue_divine("event", "Ayo loses his job")
    await engine.process_pending()
    s = engine.state
    assert s.person.occupation == "" and s.person.wage_per_hour == 0
    assert not any(i.kind == "work" for i in s.plan if i.t >= s.t)
    money = s.needs.money
    await run_ticks(engine, 60)
    assert not any(e.text.endswith("settles in and gets to work.") for e in s.log)
    assert s.needs.money <= money  # never earns again


async def test_promotion_raises_the_wage(make_engine):
    engine = make_engine()
    await engine.step()
    before = engine.state.person.wage_per_hour
    engine.queue_divine("event", "Ayo is promoted")
    await engine.process_pending()
    assert engine.state.person.wage_per_hour > before


async def test_death_decree_ends_the_story(make_engine):
    engine = make_engine()
    await engine.step()
    engine.queue_divine("event", "Ayo dies")
    await engine.process_pending()
    s = engine.state
    assert not s.alive and s.cause_of_death == "by divine decree"
    t = s.t
    await engine.step()
    assert s.t == t  # time stops for the dead
    scene = render_scene(s)
    assert sum(row.count("X") for row in scene) == 1 and not any("@" in row for row in scene)
    assert any("dies" in e.text for e in s.log)


async def test_a_dead_nudge_or_event_is_harmless(make_engine):
    engine = make_engine()
    engine.state.alive = False
    engine.queue_divine("event", "anything")
    await engine.process_pending()  # must not raise or revive
    assert not engine.state.alive


async def test_hunger_reflex_overrides_an_empty_plan(make_engine):
    engine = make_engine()
    s = engine.state
    s.plan = [PlanItem(t=s.t, place="home", kind="idle")]
    s.needs.hunger = 95
    s.flags["hunger"] = 10**9  # silence the crisis path so only the body's reflex acts
    await run_ticks(engine, 8)
    assert s.needs.hunger < 40
    assert any("overrules" in e.text for e in s.log)


async def test_exhaustion_collapses_into_sleep(make_engine):
    engine = make_engine()
    s = engine.state
    s.plan = [PlanItem(t=s.t, place="home", kind="work")]
    s.needs.energy = 4
    s.flags["energy"] = 10**9
    await run_ticks(engine, 6)
    assert s.kind == "sleep" and s.needs.energy > 10


async def test_crisis_triggers_demigod_once_per_cooldown(make_engine):
    engine = make_engine()
    s = engine.state
    await engine.step()
    s.needs.hunger = 85
    s.plan = [PlanItem(t=s.t, place="work", kind="work")]
    await run_ticks(engine, 3)
    assert sum("Hunger interrupts" in e.text for e in s.log) == 1


def test_physics_sleep_at_home_restores_more_than_outside(make_state):
    a, b = make_state(), make_state()
    a.needs.energy = b.needs.energy = 20
    apply_tick(a, "sleep", "home")
    apply_tick(b, "sleep", "park")
    assert a.needs.energy > b.needs.energy > 20


def test_physics_work_pays_less_away_from_the_workplace(make_state):
    a, b = make_state(wage=12), make_state(wage=12)
    apply_tick(a, "work", "work")
    apply_tick(b, "work", "home")
    assert a.needs.money - 150 > (b.needs.money - 150) > 0


def test_physics_unemployed_earn_nothing(make_state):
    s = make_state(wage=0)
    apply_tick(s, "work", "work")
    assert s.needs.money == 150


def test_scene_marks_person_and_places_townsfolk(make_state):
    s = make_state()
    s.npcs[0].place = "home"
    scene = render_scene(s)
    assert sum(row.count("@") for row in scene) == 1
    assert sum(row.count(s.npcs[0].glyph) for row in scene) >= 1
    assert len({len(r) for r in scene}) == 1


async def test_gods_words_before_the_first_dawn_amend_the_day_instead_of_preceding_it(make_engine):
    engine = make_engine()
    s = engine.state
    assert s.dawned_day == -1 and not s.plan
    engine.queue_divine("event", "An invitation to the temple arrives")
    await engine.process_pending()
    texts = [e.text for e in s.log]
    dawn_at = next(i for i, t in enumerate(texts) if t.startswith("Day 1"))
    comment_at = next(i for i, e in enumerate(s.log) if e.kind == "demigod")
    assert dawn_at < comment_at  # the day was planned first
    assert any(i.place == "temple" and i.t == s.t for i in s.plan)  # and the decree's schedule survived it
    await engine.step()
    assert sum(t.startswith("Day 1") for t in (e.text for e in s.log)) == 1  # dawn is never repeated
