from __future__ import annotations

import pytest

from mortal.core.clock import (
    hhmm,
    is_dawn,
    is_weekend,
    parse_clock,
    phase,
    story_day,
    weekday,
)
from mortal.core.constants import DAWN_MINUTE
from mortal.core.decision import Decision, PlanEntry, parse_decision
from mortal.core.models import LifeState, Needs, PlanItem
from mortal.core.plan import current_item, merge_plan, resolve_entries
from mortal.core.world import builtin_world


# ----------------------------------------------------------------------------- map
def test_every_place_is_reachable_from_home():
    town = builtin_world()
    home = town.anchor("home")
    for key in town.places:
        anchor = town.anchor(key)
        assert town.walkable(anchor), key
        path = town.path(home, anchor)
        assert path[-1] == anchor if path else key == "home"


def test_map_is_rectangular_and_anchors_are_unique():
    town = builtin_world()
    assert len({len(r) for r in town.rows}) == 1
    anchors = [town.anchor(k) for k in town.places]
    assert len(set(anchors)) == len(anchors)


def test_paths_only_use_walkable_cells_and_adjacent_steps():
    town = builtin_world()
    path = town.path(town.anchor("library"), town.anchor("park"))
    prev = town.anchor("library")
    for cell in path:
        assert town.walkable(cell)
        assert abs(cell[0] - prev[0]) + abs(cell[1] - prev[1]) == 1
        prev = cell


def test_slots_start_at_anchor_and_are_walkable():
    town = builtin_world()
    for key in town.places:
        slots = town.slots(key)
        assert slots[0] == town.anchor(key)
        assert all(town.walkable(c) for c in slots)


# ----------------------------------------------------------------------------- clock
def test_story_day_flips_at_dawn_not_midnight():
    assert story_day(DAWN_MINUTE) == 0
    assert story_day(1439) == 0
    assert story_day(1440 + 120) == 0  # 02:00 still belongs to day 1's night
    assert story_day(1440 + DAWN_MINUTE) == 1
    assert is_dawn(DAWN_MINUTE) and not is_dawn(DAWN_MINUTE + 10)


def test_clock_text_and_phase_and_weekend():
    assert hhmm(DAWN_MINUTE) == "06:00"
    assert phase(DAWN_MINUTE) == "dawn" and phase(13 * 60) == "afternoon" and phase(23 * 60) == "night"
    assert weekday(DAWN_MINUTE) == "Mon"
    assert is_weekend(5 * 1440 + DAWN_MINUTE) and not is_weekend(4 * 1440 + DAWN_MINUTE)


def test_parse_clock_resolves_small_hours_to_the_night_ahead():
    now = DAWN_MINUTE + 600  # 16:00 on day 1
    assert parse_clock("now", now) == now
    assert parse_clock("17:30", now) == 17 * 60 + 30
    assert parse_clock("01:30", now) == 1440 + 90  # tonight's small hours, not this morning's
    assert parse_clock("25:00", now) is None and parse_clock("soon", now) is None


# ----------------------------------------------------------------------------- plan
def entry(at, place="home", kind="relax", text=""):
    return PlanEntry(at=at, place=place, kind=kind, activity=text)


def test_resolve_drops_invalid_and_clamps_past_to_now():
    now = DAWN_MINUTE + 120
    items = resolve_entries([entry("06:00"), entry("09:00", "nowhere"), entry("10:00", kind="fly"),
                             entry("garbage"), entry("12:00", "cafe", "eat")], now, builtin_world())
    assert [(i.t, i.place) for i in items] == [(now, "home"), (12 * 60, "cafe")]


def test_resolve_drops_entries_beyond_the_story_day():
    items = resolve_entries([entry("05:00")], DAWN_MINUTE, builtin_world())  # 05:00 = the *next* morning, before dawn
    assert [i.t for i in items] == [1440 + 300]
    assert resolve_entries([entry("06:00")], DAWN_MINUTE + 1440 - 10, builtin_world())[0].t == DAWN_MINUTE + 1440 - 10


def test_current_item_is_last_started():
    plan = [PlanItem(t=100, place="home", kind="eat"), PlanItem(t=200, place="work", kind="work")]
    assert current_item(plan, 50) is None
    assert current_item(plan, 150).place == "home"
    assert current_item(plan, 200).place == "work"


def test_merge_dawn_replaces_future_keeps_past():
    old = [PlanItem(t=100, place="home", kind="eat"), PlanItem(t=500, place="work", kind="work")]
    new = [PlanItem(t=300, place="park", kind="relax")]
    merged = merge_plan(old, new, now=300, replace_future=True)
    assert [i.t for i in merged] == [100, 300]


def test_merge_interrupt_replaces_span_then_resumes_previous_activity():
    old = [PlanItem(t=360, place="work", kind="work"), PlanItem(t=720, place="cafe", kind="eat")]
    new = [PlanItem(t=500, place="library", kind="study")]
    merged = merge_plan(old, new, now=500, replace_future=False)
    assert [(i.t, i.place) for i in merged] == [(360, "work"), (500, "library"), (590, "work"), (720, "cafe")]


def test_merge_interrupt_does_not_resume_over_a_scheduled_item():
    old = [PlanItem(t=360, place="work", kind="work"), PlanItem(t=540, place="cafe", kind="eat")]
    new = [PlanItem(t=500, place="library", kind="study")]  # study lasts 90: would end at 590 > 540
    merged = merge_plan(old, new, now=500, replace_future=False)
    assert [(i.t, i.place) for i in merged] == [(360, "work"), (500, "library"), (540, "cafe")]


# ----------------------------------------------------------------------------- decisions
def test_parse_decision_clamps_effects():
    d = parse_decision({"narration": "x", "effects": {"mood": 999, "money": -99999, "energy": "5"}})
    assert d.effects.mood == 40 and d.effects.money == -500 and d.effects.energy == 5


def test_parse_decision_salvages_valid_parts():
    d = parse_decision({
        "narration": "ok",
        "plan": [{"at": "09:00", "place": "cafe", "kind": "eat"}, {"nonsense": True}, "junk"],
        "relationships": [{"name": "Mara", "delta": 500}, {"delta": 1}],
        "effects": {"mood": "not a number", "fun": 7},
        "life_event": {"text": "x", "importance": 9},  # out of range -> dropped
    })
    assert d.narration == "ok"
    assert len(d.plan) == 1 and d.plan[0].place == "cafe"
    assert [(r.name, r.delta) for r in d.relationships] == [("Mara", 30)]
    assert d.effects.fun == 7 and d.effects.mood == 0
    assert d.life_event is None


def test_parse_decision_rejects_non_objects():
    with pytest.raises(ValueError):
        parse_decision([1, 2, 3])
    assert parse_decision({}) == Decision()


def test_needs_clamp():
    n = Needs(energy=150, hunger=-5, money=2_000_000)
    n.clamp()
    assert (n.energy, n.hunger, n.money) == (100, 0, 1_000_000)


def test_state_roundtrips_through_json(make_state):
    s = make_state()
    s.add_log("system", "hello")
    s.add_memory("a thing", 4)
    again = LifeState.model_validate_json(s.model_dump_json())
    assert again == s
