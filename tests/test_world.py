"""Worlds: drawing, validating, changing and living in them."""

from __future__ import annotations

import copy
import json
import random

import pytest
from conftest import run_ticks

from mortal.config import Settings
from mortal.core.clock import story_day
from mortal.core.decision import DecisionRequest, PlanEntry, parse_decision
from mortal.core.models import LifeSpec, LifeState, Npc, PlanItem, WorldSpec
from mortal.core.plan import resolve_entries
from mortal.core.world import (
    World,
    WorldPatch,
    builtin_world,
    check_world,
    parse_world,
)
from mortal.engine.demigod import DemigodService
from mortal.engine.demigod_rule import RuleDemigod
from mortal.engine.life import LifeEngine
from mortal.engine.npcs import apply_npc_life, haunts_for, move_npcs, routine_place
from mortal.engine.prompts import build_prompts
from mortal.engine.registry import LifeRegistry
from mortal.engine.views import frame, legend_for, render_scene
from mortal.engine.worldgen import EXAMPLE_WORLD, build_world, world_prompts
from mortal.errors import LLMError


def doc(**changes) -> dict:
    d = json.loads(EXAMPLE_WORLD)
    d.update(changes)
    return d


def example_world() -> World:
    world, problems = parse_world(doc())
    assert world is not None, problems
    return world


class Scripted:
    """A fake LLM that answers by *kind of request*, so tests can script the world and the person apart."""

    name, model = "scripted", "s-1"

    def __init__(self, **queues):
        self.queues = {k: list(v if isinstance(v, (list, tuple)) else [v]) for k, v in queues.items()}
        self.seen: list[tuple[str, str, str]] = []

    @staticmethod
    def kind_of(system: str, user: str) -> str:
        if "drawing the WORLD in which" in system:
            return "worldgen"
        if "reshaping a world that already exists" in system:
            return "repair"
        if "TRIGGER: world." in user:
            return "world"
        if "TRIGGER: dawn" in user:
            return "dawn"
        if "GOD HAS SPOKEN" in user:
            return "divine"
        return "other"

    def complete(self, system: str, user: str) -> str:
        kind = self.kind_of(system, user)
        self.seen.append((kind, system, user))
        queue = self.queues.get(kind, [])
        if not queue:
            if kind == "worldgen":
                raise LLMError("nothing scripted")
            return "{}"
        reply = queue.pop(0) if len(queue) > 1 else queue[0]  # the last reply repeats
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    def count(self, kind: str) -> int:
        return sum(1 for k, _, _ in self.seen if k == kind)

    def prompts(self, kind: str) -> list[tuple[str, str]]:
        return [(s, u) for k, s, u in self.seen if k == kind]


def service(provider, settings) -> DemigodService:
    svc = DemigodService(settings)
    for long_reply in (False, True):
        svc._providers[("gemini", None, long_reply)] = provider
    return svc


def llm_engine(provider, settings, make_state, **kw) -> LifeEngine:
    state = make_state(provider="gemini", world=kw.pop("world", None) or example_world(), **kw)
    return LifeEngine(state, service(provider, settings), settings)


# ============================================================================ parsing and validation
def test_the_example_in_the_prompt_is_itself_a_valid_world():
    world = example_world()
    assert world.source == "llm" and (world.width, world.height) == (20, 8)
    assert world.home == "cottage" and world.anchor("cottage") == (1, 4)
    assert check_world(world) == []
    assert world.walkable(world.anchor("lamp")) and world.glyph("1").style == "door"  # markers are walkable


def test_the_builtin_town_is_a_valid_world():
    town = builtin_world()
    assert check_world(town) == [] and town.source == "builtin"
    assert town.at_phrase("park") == "in the park" and town.at_phrase("home") == "at home"
    assert town.to_phrase("river") == "the riverbank" and town.to_phrase("home") == "home"


def bad(mutate) -> list[str]:
    d = doc()
    mutate(d)
    world, problems = parse_world(d)
    assert world is None and problems
    return problems


def test_a_place_cut_off_from_home_is_reported_with_its_position():
    def cut(d):
        d["map"][5] = "#.....#............#"
        d["map"][6] = "####.#2#......######"

    problems = bad(cut)
    assert any("lamp" in p and "cannot be reached" in p and "x=6,y=6" in p for p in problems)
    assert any("walkable in the legend" in p for p in problems)  # tells the model what it can use


def test_markers_must_appear_exactly_once():
    assert any("marker '3' is not on the map" in p for p in bad(lambda d: d["map"].__setitem__(4, d["map"][4].replace("3", "."))))
    assert any("appears 2 times" in p for p in bad(lambda d: d["map"].__setitem__(5, "#..3...............#")))
    assert any("no marker" in p for p in bad(lambda d: d["places"][0].pop("marker")))
    assert any("same marker" in p for p in bad(lambda d: d["places"][1].__setitem__("marker", "1")))


def test_the_world_needs_a_home_to_sleep_in_and_somewhere_to_eat():
    assert any("afford 'sleep'" in p for p in bad(lambda d: d["places"][0].__setitem__("affords", ["relax", "eat"])))

    def starve(d):
        d["places"][0]["affords"] = ["relax", "sleep"]
        d["places"][3]["affords"] = ["socialize"]

    assert any("'eat'" in p for p in bad(starve))


def test_size_limits_and_ascii_only():
    assert any("between 20x8" in p for p in bad(lambda d: d.__setitem__("map", d["map"][:5])))
    assert any("ASCII" in p for p in bad(lambda d: d["map"].__setitem__(0, "~" * 19 + "é")))
    assert bad(lambda d: d.__setitem__("map", "just a sentence")) != []
    assert any("places" in p for p in bad(lambda d: d.__setitem__("places", [])))
    assert parse_world([1, 2])[0] is None and parse_world({"map": 3})[0] is None


def test_parsing_is_lenient_about_shape_but_not_about_playability():
    d = doc()
    d["map"] = "\n".join(d["map"]) + "\n\n"  # one string with newlines and trailing blank lines
    d["map"] = d["map"].replace("~~~~~~~~~~~~~~~~~~~~\n####", "~~~~~~~~~~~~~~~~~~~\n####")  # a short row is padded
    d["legend"] = {"~": {"name": "sea", "style": "water"}, ".": "cobbles"}  # dict-shaped legend
    d["places"][3]["affords"] = ["socialize", "eat", "fly"]  # unknown kinds are dropped
    d.pop("seasons")
    world, problems = parse_world(d)
    assert world is not None, problems
    assert world.places["inn"].affords == ["socialize", "eat"]
    assert world.seasons == ["spring", "summer", "autumn", "winter"] and len({len(r) for r in world.rows}) == 1
    assert world.glyph("~").style == "water" and world.glyph(".").name == "cobbles"
    assert world.glyph(".").walkable and world.glyph(".").style == "path"  # omitted fields fall back to convention


def test_conventional_glyphs_default_sensibly_when_the_legend_omits_them():
    d = doc(legend=[])
    world, problems = parse_world(d)
    assert world is not None, problems
    assert world.glyph(".").walkable and not world.glyph("#").walkable and not world.glyph("~").walkable
    assert world.glyph("Q").name == "lettering" and not world.glyph("Q").walkable


def test_lookup_resolves_keys_names_markers_and_labels():
    w = example_world()
    assert {w.lookup(x) for x in ("lamp", "The Lighthouse", "the lighthouse", "2", "Lighthouse")} == {"lamp"}
    assert w.lookup("the Drowned Rat inn") == "inn" and w.lookup("nowhere") is None and w.lookup(None) is None
    items = resolve_entries([PlanEntry(at="now", place="The Quay", kind="explore"),
                             PlanEntry(at="now", place="the moon", kind="explore")], 360, w)
    assert [i.place for i in items] == ["quay"]


def test_seasons_follow_the_worlds_own_calendar():
    w = example_world()
    assert w.seasons == ["thaw", "bright", "gale", "frost"] and w.season_days == 90
    assert [w.season_at(d) for d in (0, 89, 90, 270, 360)] == ["thaw", "thaw", "bright", "frost", "thaw"]


# ============================================================================ changing a world
BAKERY = {"rows": {5: "#....5.............#"},
          "places": [{"key": "bakery", "name": "the bakery", "marker": "5", "affords": ["errand", "eat"], "meal_cost": 2}],
          "summary": "A bakery opens by the cottage."}


def test_a_valid_patch_builds_a_new_world_and_leaves_the_old_one_alone():
    old = example_world()
    old.path(old.anchor("cottage"), old.anchor("inn"))  # warm the caches
    new, problems = old.apply_patch(WorldPatch.model_validate(BAKERY), story_day=4)
    assert problems == [] and new is not None
    assert new.anchor("bakery") == (5, 5) and new.revision == 1 and new.changed_day == 4
    assert "bakery" not in old.places and old.revision == 0 and old.rows[5] == "#" + "." * 18 + "#"
    assert new.path(new.anchor("cottage"), new.anchor("bakery"))  # reachable in the new world
    assert old.place_at((5, 5)) is None and new.place_at((5, 5)) == "bakery"  # lookup tables are not shared


def test_a_patch_that_strands_a_place_is_rejected_with_reasons():
    cut = WorldPatch(rows={5: "#.....#............#", 6: "####.#2#......######"})
    new, problems = example_world().apply_patch(cut)
    assert new is None and any("lamp" in p and "cannot be reached" in p for p in problems)


@pytest.mark.parametrize("patch,needle", [
    (WorldPatch(remove_places=["cottage"]), "cannot be removed"),
    (WorldPatch(rows={99: "x"}), "does not exist"),
    (WorldPatch(rows={4: "#" * 30}), "fit the map's width"),
    (WorldPatch(rows={0: "é" * 20}), "ASCII"),
    (WorldPatch(rows={i: "." * 20 for i in range(8)}), "at most 60%"),
    (WorldPatch(rows={6: "####..........######"}), "marker '2' is not on the map"),  # redrew over a place's marker
])
def test_patch_rejections(patch, needle):
    new, problems = example_world().apply_patch(patch)
    assert new is None and any(needle in p for p in problems), problems


def test_a_patch_can_remove_a_place_and_redefine_the_legend():
    w = example_world()
    patch = WorldPatch(rows={6: "####..........######", 5: "#%%%%%%%%%%%%%%%%%%#"}, remove_places=["lamp"],
                       legend=[{"ch": "%", "name": "fresh snow", "walkable": True, "style": "snow"}])
    new, problems = w.apply_patch(patch)
    assert new is not None, problems
    assert "lamp" not in new.places and new.glyph("%").style == "snow" and new.walkable((3, 5))
    assert [g.ch for g in new.legend].count("%") == 1


def test_world_update_parsing_keeps_what_is_valid():
    d = parse_decision({
        "narration": "x",
        "world": {"weather": "  pale frost  ", "patch": {"rows": {"5": "#....5.............#"}, "summary": "s"},
                  "npcs": [{"name": "Mara", "doing": "nets", "routine": [{"at": "07:00", "place": "quay"}, "junk"]},
                           "junk", {"nodoing": 1}],
                  "arrivals": [{"name": "Odo", "role": "ferryman"}, {"role": "no name"}], "departures": ["Tomas", 3]},
    })
    w = d.world
    assert w.weather == "pale frost" and w.patch.rows == {5: "#....5.............#"}
    assert [n.name for n in w.npcs] == ["Mara"] and len(w.npcs[0].routine) == 1
    assert [a.name for a in w.arrivals] == ["Odo"] and w.departures == ["Tomas"]
    assert parse_decision({"world": "nonsense"}).world is None
    assert parse_decision({"world": {"patch": {"rows": "bad"}}}).world.patch is None


# ============================================================================ townsfolk
def test_roles_find_their_haunts_by_what_a_place_affords():
    w, rng = example_world(), random.Random(1)
    assert set(haunts_for("friend", w, rng)) == {"quay", "cottage", "inn"}
    assert haunts_for("coworker", w, rng)[0] == "lamp"  # work first
    assert set(haunts_for("tinker", w, rng)) <= set(w.places) - {"lamp"}  # unknown roles: any non-work place


def test_a_routine_puts_someone_where_the_day_says():
    w = example_world()
    npc = Npc(id="m", name="Mara", glyph="m")
    life = parse_decision({"world": {"npcs": [{"name": "Mara", "doing": " mending nets ", "routine": [
        {"at": "20:00", "place": "The Drowned Rat inn"}, {"at": "07:00", "place": "quay"},
        {"at": "00:00", "place": None}, {"at": "25:00", "place": "inn"}, {"at": "09:00", "place": "atlantis"}]}]}}).world.npcs[0]
    apply_npc_life(npc, life, w)
    assert npc.doing == "mending nets"
    assert [(r.minute, r.place) for r in npc.routine] == [(0, None), (420, "quay"), (540, None), (1200, "inn")]
    at = lambda h, m=0: routine_place(npc, h * 60 + m, w)
    assert (at(3), at(7), at(8, 59), at(12), at(21), at(23, 50)) == (None, "quay", "quay", None, "inn", "inn")
    move_npcs([npc], 7 * 60 + 10, random.Random(0), w)  # routines are followed every tick, not only on the hour
    assert npc.place == "quay"
    move_npcs([npc], 9 * 60 + 30, random.Random(0), w)
    assert npc.place is None


def test_townsfolk_without_a_routine_still_wander_between_haunts():
    w = example_world()
    npc = Npc(id="b", name="Bea", glyph="b", haunts=["quay", "inn", "gone"])
    rng, seen = random.Random(3), set()
    for hour in range(8, 20):
        move_npcs([npc], hour * 60, rng, w)
        seen.add(npc.place)
    assert seen <= {"quay", "inn"} and seen  # stale haunts are ignored
    move_npcs([npc], 23 * 60, rng, w)
    assert npc.place is None
    npc.place = "lamp"
    w2, _ = w.apply_patch(WorldPatch(rows={6: "####..........######"}, remove_places=["lamp"]))
    move_npcs([npc], 23 * 60 + 10, rng, w2)
    assert npc.place is None  # their place was swept away


# ============================================================================ drawing a world at creation
def spec_for(description="a harbour village under a lighthouse", **kw) -> LifeSpec:
    return LifeSpec.model_validate({"person": {"name": "Ayo", "occupation": "keeper"}, "seed": 5,
                                    "world": {"description": description, **kw}, "llm": {"provider": "gemini"}})


async def test_the_world_is_drawn_from_gods_description(settings):
    llm = Scripted(worldgen=doc())
    result = await build_world(llm, spec_for(size="small"), settings)
    assert result.drawn and result.world.name == "Gullhaven" and result.notes == ["The demi-god draws the world: Gullhaven."]
    system, user = llm.prompts("worldgen")[0]
    assert "a harbour village under a lighthouse" in user and "about 40 columns by 14 rows" in user
    assert "Mood: benevolent" in user and "MARKER" in system and "ASCII" in system


async def test_the_demigods_mood_and_objectives_reach_the_world_prompt(settings):
    spec = spec_for("")
    spec.god.mood, spec.god.objectives = "wrathful", ["teach patience"]
    _, user = world_prompts(spec)
    assert "Mood: wrathful" in user and "teach patience" in user and "invent a world that suits the mortal" in user


async def test_rejected_worlds_go_back_to_the_demigod_with_the_problems(settings):
    broken = doc()
    broken["map"][4] = broken["map"][4].replace("3", ".")
    llm = Scripted(worldgen=[broken, doc()])
    result = await build_world(llm, spec_for(), settings)
    assert result.drawn and result.notes == ["The demi-god draws the world: Gullhaven (it took 2 attempts)."]
    _, second = llm.prompts("worldgen")[1]
    assert "REJECTED" in second and "marker '3' is not on the map" in second and "Gullhaven" in second  # problem + its own reply


async def test_a_world_that_never_validates_falls_back_to_the_builtin_town(settings):
    broken = doc(map=["~" * 20] * 8)
    llm = Scripted(worldgen=[broken, "not json at all", broken])
    result = await build_world(llm, spec_for(), settings)
    assert not result.drawn and result.world.source == "builtin" and llm.count("worldgen") == settings.world_attempts
    assert "could not be made playable after 3 attempts" in result.notes[0] and "built-in town" in result.notes[0]


async def test_an_unreachable_backend_falls_back_after_one_try(settings):
    llm = Scripted(worldgen=[LLMError("HTTP 503")])
    result = await build_world(llm, spec_for(), settings)
    assert result.world.source == "builtin" and llm.count("worldgen") == 1 and "HTTP 503" in result.notes[0]


async def test_offline_creation_says_so_only_when_a_world_was_asked_for(settings):
    asked = await build_world(None, spec_for(), settings)
    assert asked.world.source == "builtin" and "described world cannot be drawn" in asked.notes[0]
    assert (await build_world(None, spec_for(""), settings)).notes == []


async def test_registry_creates_a_life_in_the_drawn_world(settings):
    llm = Scripted(worldgen=doc())
    registry = LifeRegistry(settings, persist=False, demigod=service(llm, settings))
    runner = await registry.create(spec_for())
    s = runner.state
    assert s.world.name == "Gullhaven" and s.pos == s.world.anchor("cottage")
    assert [e.text for e in s.log] == ["A life begins: Ayo, 30, keeper.", "The demi-god draws the world: Gullhaven."]
    assert all(h in s.world.places for n in s.npcs for h in n.haunts)
    scene = render_scene(s)
    assert len(scene) == 8 and sum(r.count("@") for r in scene) == 1
    data = runner.frame_for()
    assert data["world"]["name"] == "Gullhaven" and data["terrain"][4].startswith("#1")
    assert {"chars": "3", "name": "Quay", "style": "marker"} in data["legend"]


async def test_state_round_trips_with_its_world(make_state):
    s = make_state(world=example_world())
    back = LifeState.model_validate_json(s.model_dump_json())
    assert back.world.rows == s.world.rows and back.world.anchor("inn") == (14, 4)
    assert back.world.path(back.world.anchor("cottage"), back.world.anchor("inn"))  # private tables rebuilt
    legacy = json.loads(s.model_dump_json())
    legacy.pop("world")
    legacy.pop("season")
    assert LifeState.model_validate(legacy).world.source == "builtin"  # saves from before worlds existed


def test_frame_and_legend_describe_any_world(make_state):
    s = make_state(world=example_world())
    styles, legend = legend_for(s.world)
    assert styles["~"] == "water" and styles["1"] == "marker" and styles["#"] == "wall"
    assert {"chars": "1", "name": "Keeper's cottage", "style": "marker"} in legend or any(
        item["style"] == "marker" and item["chars"] == "1" for item in legend)
    assert {"chars": "~", "name": "sea", "style": "water"} in legend
    f, _ = frame(s, runtime={}, since_seq=0)
    assert f["terrain"] == s.world.rows and f["entities"][-1]["kind"] == "me"
    s.alive = False
    assert frame(s, runtime={}, since_seq=0)[0]["entities"][-1] == {"x": 1, "y": 4, "ch": "X", "kind": "dead"}


# ============================================================================ living in a drawn world
async def test_the_offline_demigod_lives_a_drawn_world_without_ever_leaving_it(make_state, settings):
    s = make_state(world=example_world(), seed=9)
    engine = LifeEngine(s, DemigodService(settings), settings)
    await run_ticks(engine, 144 * 3)
    world = s.world
    assert s.alive and world.place_at(s.pos) is not None or s.pos in world.main_area()
    assert all(i.place in world.places for i in s.plan)
    texts = " ".join(e.text for e in s.log)
    assert "sets off for the lighthouse" in texts or "at the lighthouse" in texts  # the world's own names are used
    assert s.needs.money > 150 - 3 * 25 - 100  # wages came in at the one place that affords work


async def test_without_a_workplace_the_person_works_from_home_for_half_pay(make_state, settings):
    d = doc()
    d["places"][1]["affords"] = ["reflect"]
    world, problems = parse_world(d)
    assert world is not None, problems
    s = make_state(world=world)
    plan = RuleDemigod().decide(DecisionRequest("dawn", s, random.Random(1)))
    assert {(e.place, e.kind) for e in plan.plan if e.kind == "work"} == {("cottage", "work")}


def test_prices_and_pay_belong_to_the_places_of_the_world(make_state):
    from mortal.engine.physics import apply_tick

    s = make_state(world=example_world(), wage=12)
    before = s.needs.money
    apply_tick(s, "work", "lamp")
    assert s.needs.money - before == pytest.approx(2.0)  # full wage: the lighthouse affords work
    before = s.needs.money
    apply_tick(s, "work", "quay")
    assert s.needs.money - before == pytest.approx(1.0)  # half wage elsewhere
    s.needs.hunger = 80
    before = s.needs.money
    apply_tick(s, "eat", "inn")
    assert before - s.needs.money == pytest.approx(3.0)  # the inn's own meal price


def test_rule_demigod_reads_gods_words_in_the_worlds_own_vocabulary(make_state):
    s = make_state(world=example_world())
    go = lambda text: RuleDemigod().decide(DecisionRequest("divine", s, random.Random(2), {"kind": "event", "text": text}))
    assert go("Someone waits at the lighthouse").plan[0].place == "lamp"
    assert go("An old friend is at the inn").plan[0].place == "inn"
    assert go("Go home").plan[0].place == "cottage"
    assert go("A quiet day").plan == []


# ============================================================================ the world step
NO_QUAY_ROW = example_world().rows[4].replace("3", ".")  # the quay's marker painted over


def world_reply(**world) -> dict:
    return {"narration": "Gulls wheel over a slack tide.", "world": world}


async def test_the_daily_world_step_sets_weather_routines_and_comings_and_goings(settings, make_state):
    probe = make_state(world=example_world())
    mara, tomas, tomas_role = probe.npcs[0].name, probe.npcs[1].name, probe.npcs[1].role
    llm = Scripted(world=world_reply(
        weather="pale frost",
        npcs=[{"name": mara, "doing": "mending nets", "routine": [{"at": "00:00", "place": None}, {"at": "07:00", "place": "quay"}]},
              {"name": "Nobody", "doing": "ignored"}],
        arrivals=[{"name": "Odo", "role": "ferryman", "note": "Rows the tide.", "place": "The Quay", "affinity": 5}],
        departures=[tomas]))
    engine = llm_engine(llm, settings, make_state)
    s = engine.state
    await engine.step()
    assert s.weather == "pale frost" and s.season == "thaw"
    assert s.npc_by_name(mara).doing == "mending nets" and s.npc_by_name(tomas) is None
    odo = s.npc_by_name("Odo")
    assert odo is not None and odo.note == "Rows the tide." and odo.affinity == 5
    assert "Odo (ferryman) arrives in Gullhaven." in [e.text for e in s.log]
    assert f"{tomas} ({tomas_role}) leaves Gullhaven." in [e.text for e in s.log]
    assert "Gulls wheel over a slack tide." in [e.text for e in s.log if e.kind == "event"]
    assert llm.count("world") == 1 and [k for k, *_ in llm.seen][:2] == ["world", "dawn"]  # the world is tended first
    await run_ticks(engine, 6)  # 07:00 -> Mara is where her routine says
    assert s.npc_by_name(mara).place == "quay"


async def test_a_world_decision_cannot_touch_the_persons_life(settings, make_state):
    reply = {**world_reply(weather="hail"), "effects": {"money": 400, "mood": 40}, "plan": [
        {"at": "now", "place": "inn", "kind": "eat"}], "death": "by hail", "profile": {"occupation": ""}}
    engine = llm_engine(Scripted(world=reply), settings, make_state)
    s = engine.state
    await engine._tend_world(0)
    assert s.alive and s.needs.money == 150 and s.plan == [] and s.person.occupation == "clerk" and s.weather == "hail"


async def test_the_season_turning_is_announced_and_passed_to_the_demigod(settings, make_state):
    w = example_world().model_copy(update={"seasons": ["thaw", "frost"], "season_days": 5})
    llm = Scripted(world=world_reply())
    engine = llm_engine(llm, settings, make_state, world=w)
    await engine._tend_world(0)
    await engine._tend_world(5)
    assert engine.state.season == "frost" and "The season turns: frost." in [e.text for e in engine.state.log]
    assert "(it has just turned" in llm.prompts("world")[1][1] and "Season: thaw." in llm.prompts("world")[0][1]


async def test_the_llm_tends_the_world_only_every_nth_day_but_weather_still_moves(tmp_path, make_state):
    cfg = Settings.from_env({"MORTAL_DATA_DIR": str(tmp_path), "MORTAL_WORLD_UPDATE_DAYS": "3"})
    llm = Scripted(world=world_reply(weather="gale"))
    engine = llm_engine(llm, cfg, make_state)
    weathers = []
    for day in range(4):
        await engine._tend_world(day)
        weathers.append(engine.state.weather)
    assert llm.count("world") == 2 and weathers[0] == weathers[3] == "gale"  # days 0 and 3
    assert weathers[1] in {"sunny", "cloudy", "rain", "fog", "storm"} and weathers[2] in {"sunny", "cloudy", "rain", "fog", "storm"}


async def test_the_map_prompt_is_shown_only_to_decisions_that_may_change_it(make_state):
    s = make_state(world=example_world())
    rng = random.Random(0)
    world_sys, world_user = build_prompts(DecisionRequest("world", s, rng, {"season": "thaw"}))
    assert "THE MAP" in world_user and "4|#1.....3......4....#|" in world_user and "patch" in world_sys
    assert "'~' sea (blocks, style water)" in world_user and "- lamp: the lighthouse" in world_user
    _, divine_user = build_prompts(DecisionRequest("divine", s, rng, {"kind": "world", "text": "A flood"}))
    assert "WORLD DECREE" in divine_user and "THE MAP" in divine_user
    dawn_sys, dawn_user = build_prompts(DecisionRequest("dawn", s, rng))
    assert "THE MAP" not in dawn_user and "patch" not in dawn_sys.split("Reply with ONE JSON")[1]


# ============================================================================ the world changes
async def test_a_map_patch_from_the_world_step_is_applied(settings, make_state):
    llm = Scripted(world=world_reply(patch=BAKERY))
    engine = llm_engine(llm, settings, make_state)
    s = engine.state
    await engine._tend_world(0)
    assert "bakery" in s.world.places and s.world.revision == 1
    assert "A bakery opens by the cottage." in [e.text for e in s.log if e.kind == "event"]
    s.pos = s.world.anchor("bakery")
    assert render_scene(s)[5][5] == "@"


async def test_an_invalid_patch_is_sent_back_for_correction_then_applied(settings, make_state):
    bad_patch = {"rows": {5: "#.....#............#", 6: "####.#2#......######"}, "summary": "A wall rises."}
    fixed = {"patch": {"rows": {5: "#....5.............#"}, "places": BAKERY["places"], "summary": "A bakery instead."}}
    llm = Scripted(world=world_reply(patch=bad_patch), repair=fixed)
    engine = llm_engine(llm, settings, make_state)
    await engine._tend_world(0)
    s = engine.state
    assert llm.count("repair") == 1 and "bakery" in s.world.places and "lamp" in s.world.places
    _, repair_user = llm.prompts("repair")[0]
    assert "cannot be reached" in repair_user and "A wall rises." in repair_user and "THE MAP AS IT IS NOW" in repair_user
    assert "A bakery instead." in [e.text for e in s.log]


async def test_a_patch_that_cannot_be_fixed_leaves_the_world_as_it_was(settings, make_state):
    bad_patch = {"rows": {5: "#.....#............#", 6: "####.#2#......######"}}
    for repair in ({"patch": None}, "garbage", {"patch": bad_patch}):
        llm = Scripted(world=world_reply(patch=bad_patch), repair=repair)
        engine = llm_engine(llm, settings, make_state)
        rows = list(engine.state.world.rows)
        await engine._tend_world(0)
        s = engine.state
        assert s.world.rows == rows and s.world.revision == 0
        assert any(e.kind == "system" and "could not be made to hold" in e.text for e in s.log)
        assert llm.count("repair") <= settings.world_attempts - 1


async def test_a_change_that_swallows_the_ground_underfoot_carries_the_person_to_safety(settings, make_state):
    flood = {"rows": {6: "~" * 20}, "remove_places": ["lamp"], "summary": "The tide takes the point."}
    engine = llm_engine(Scripted(world=world_reply(patch=flood)), settings, make_state)
    s = engine.state
    s.pos = s.world.anchor("lamp")
    s.plan = [PlanItem(t=s.t + 60, place="lamp", kind="work"), PlanItem(t=s.t + 120, place="quay", kind="relax")]
    s.npcs[0].place = "lamp"
    await engine._tend_world(0)
    assert "lamp" not in s.world.places and s.world.walkable(s.pos) and s.pos in s.world.main_area()
    assert [i.place for i in s.plan] == ["cottage", "quay"]  # work moves home: nowhere else affords it
    assert s.npcs[0].place is None
    assert any("swept to safety" in e.text for e in s.log)
    await run_ticks(engine, 20)  # and life goes on in the changed world
    assert s.alive and s.pos in s.world.main_area()


async def test_gods_world_decree_reshapes_the_world_and_the_person_feels_it(settings, make_state):
    decree = {"narration": "The quay shakes loose and slides into the harbour.", "comment": "As you wish.",
              "effects": {"mood": -10},
              "world": {"weather": "storm", "patch": {"rows": {4: "#1.....3......4....#"}, "summary": "unused"},
                        "departures": []}}
    # a decree that removes the quay's marker without removing the place is rejected...
    reject = dict(decree, world=dict(decree["world"], patch={"rows": {4: NO_QUAY_ROW}}))
    # ...one that removes the place as well is accepted
    accept = dict(decree, world=dict(decree["world"], patch={"rows": {4: NO_QUAY_ROW},
                                                             "remove_places": ["quay"], "summary": "The quay is gone."}))
    for reply, quay_survives in ((reject, True), (accept, False)):
        llm = Scripted(divine=reply)
        engine = llm_engine(llm, settings, make_state)
        engine.queue_divine("world", "An earthquake takes the quay")
        await engine.process_pending()
        s = engine.state
        assert ("quay" in s.world.places) is quay_survives
        assert "The quay shakes loose and slides into the harbour." in [e.text for e in s.log]
        assert s.weather == "storm" and s.needs.mood < 70
        assert "WORLD DECREE" in llm.prompts("divine")[0][1]
    assert "The quay is gone." in [e.text for e in s.log]


async def test_the_offline_demigod_admits_it_cannot_redraw_the_world(make_engine, make_state):
    engine = make_engine(make_state(world=example_world()))
    engine.queue_divine("world", "A great flood")
    await engine.process_pending()
    s = engine.state
    assert s.world.revision == 0 and any("Only an LLM demi-god can redraw the world" in e.text for e in s.log)


# ============================================================================ the API
def test_creating_a_life_with_a_world_description_offline_uses_the_builtin_town_and_says_so(settings):
    from fastapi.testclient import TestClient

    from mortal.api.app import create_app

    body = {"person": {"name": "Ayo", "occupation": "clerk"}, "llm": {"provider": "stub"}, "seed": 1,
            "world": {"description": "a desert caravan", "size": "small"}}
    with TestClient(create_app(settings, persist=False)) as c:
        life = c.post("/api/lives", json=body)
        assert life.status_code == 201
        data = c.get(f"/api/lives/{life.json()['id']}").json()
        assert data["world"]["source"] == "builtin"
        assert any("described world cannot be drawn" in e["text"] for e in data["log"])
        assert c.post("/api/lives", json={**body, "world": {"size": "huge"}}).status_code == 422
        assert c.post("/api/lives", json={**body, "world": {"description": "x" * 2001}}).status_code == 422
        god = c.post(f"/api/lives/{life.json()['id']}/god", json={"kind": "world", "text": "A river appears"})
        assert god.status_code == 202
        log = c.get(f"/api/lives/{life.json()['id']}").json()["log"]
        assert any(e["kind"] == "divine" and e["text"].startswith("[world]") for e in log)
        assert c.post(f"/api/lives/{life.json()['id']}/god", json={"kind": "bogus", "text": "x"}).status_code == 422


def test_workspace_spec_defaults(settings):
    spec = LifeSpec.model_validate({"person": {"name": "Ayo"}})
    assert spec.world == WorldSpec(description="", size="medium") and spec.world.dimensions == (56, 20)
    assert copy.deepcopy(builtin_world()).rows == builtin_world().rows  # worlds deep-copy cleanly (default factory)
    assert story_day(360) == 0
