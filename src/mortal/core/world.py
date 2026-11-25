"""The world of one life: an ASCII map, its legend and its places.

A world is *data*, not code. It is drawn by the demi-god (an LLM) from a description, or is the
built-in town when no LLM is available, and it can change while the life goes on (`apply_patch`).

Conventions
-----------
* ``rows`` is a rectangle of printable ASCII. Every glyph is described by the ``legend`` (name,
  walkable or not, display style). A few conventional glyphs have defaults (`_CONVENTIONS`);
  unlisted capital letters and digits count as scenery lettering; any other unlisted glyph blocks.
* A **place** is where people stand to do something. LLM-drawn places are anchored by a *marker*:
  a unique glyph drawn on the map (a digit or capital letter). Markers are walkable by definition
  and stay visible, so the legend reads "1 = the salt market". Counting columns is something
  language models do badly; drawing a glyph in the right spot is something they do well.
* A place ``affords`` activity kinds (`KINDS`); this is the only thing the simulation needs to know
  about what a place *is*. A place also sets what a meal or a visit costs.
* Instances are treated as immutable: a change builds a new `World` (fresh path caches).
"""

from __future__ import annotations

import random
import re
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator

from mortal.core.constants import KINDS

Cell = tuple[int, int]  # (x, y): column, row

GLYPH_STYLES: tuple[str, ...] = (
    "water", "foliage", "grass", "path", "floor", "wall", "door", "rock", "sand", "snow", "fire", "void", "other",
)
PREPOSITIONS = ("at", "in", "on", "by", "near", "outside", "inside", "under", "beside")
SIZES: dict[str, tuple[int, int]] = {"small": (40, 14), "medium": (56, 20), "large": (72, 26)}
MIN_WIDTH, MAX_WIDTH, MIN_HEIGHT, MAX_HEIGHT = 20, 80, 8, 30
MIN_PLACES, MAX_PLACES = 3, 24
MIN_WALKABLE_CELLS = 15
MAX_CHANGED_FRACTION = 0.6  # a patch may rewrite at most this share of the map
DEFAULT_SEASONS = ("spring", "summer", "autumn", "winter")

# glyph -> (style, walkable, name): meaning assumed when the legend does not say
_CONVENTIONS: dict[str, tuple[str, bool, str]] = {
    ".": ("path", True, "path"), ":": ("path", True, "trodden ground"),
    ",": ("grass", True, "grass"), '"': ("grass", True, "grass"), "'": ("grass", True, "grass"),
    "~": ("water", False, "water"), "#": ("wall", False, "wall"), "+": ("wall", False, "wall"),
    "-": ("wall", False, "wall"), "|": ("wall", False, "wall"), "=": ("door", False, "door"),
    "*": ("foliage", False, "trees"), "^": ("rock", False, "rock"),
}
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG.sub("_", text.strip().lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"p_{slug}" if slug else "place"
    return slug[:24].rstrip("_") or "place"


def _printable(ch: str) -> bool:
    return len(ch) == 1 and 32 <= ord(ch) <= 126


class GlyphSpec(BaseModel):
    ch: str = Field(min_length=1, max_length=1)
    name: str = Field("", max_length=40)
    walkable: bool = False
    style: str = "other"

    @field_validator("ch")
    @classmethod
    def _ascii(cls, v: str) -> str:
        if not _printable(v):
            raise ValueError("glyphs must be printable ASCII")
        return v

    @field_validator("style")
    @classmethod
    def _style(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in GLYPH_STYLES else "other"


class Place(BaseModel):
    key: str = Field(min_length=1, max_length=24)
    name: str = Field(min_length=1, max_length=60)  # a noun phrase, article included: "the salt market"
    label: str = Field("", max_length=60)  # display form; derived from `name` when empty
    prep: str = "at"
    marker: str | None = Field(None, max_length=1)
    anchor: Cell = (0, 0)
    affords: list[str] = Field(default_factory=lambda: ["relax"])
    meal_cost: float = Field(2.0, ge=0, le=10)  # coins per tick of eating here
    visit_cost: float = Field(0.0, ge=0, le=5)  # coins per tick of socialising or running errands here

    def display(self) -> str:
        if self.label:
            return self.label
        text = re.sub(r"^(?:the|a|an)\s+", "", self.name.strip(), flags=re.IGNORECASE)
        return text[:1].upper() + text[1:]

    @property
    def default_kind(self) -> str:
        return self.affords[0] if self.affords else "relax"


class WorldPatch(BaseModel):
    """A change to the map, as the demi-god writes it. Untrusted until `World.apply_patch` accepts it."""

    rows: dict[int, str] = Field(default_factory=dict)  # row index -> the whole new row
    legend: list[dict[str, Any]] = Field(default_factory=list)
    places: list[dict[str, Any]] = Field(default_factory=list)
    remove_places: list[str] = Field(default_factory=list)
    summary: str = Field("", max_length=300)

    def is_empty(self) -> bool:
        return not (self.rows or self.legend or self.places or self.remove_places)


class World(BaseModel):
    name: str = Field("the town", max_length=60)
    description: str = Field("", max_length=1200)
    climate: str = Field("", max_length=300)
    rows: list[str]
    legend: list[GlyphSpec] = Field(default_factory=list)
    places: dict[str, Place]
    home: str
    seasons: list[str] = Field(default_factory=lambda: list(DEFAULT_SEASONS))
    season_days: int = Field(91, ge=5, le=400)
    source: Literal["builtin", "llm"] = "builtin"
    revision: int = 0
    changed_day: int = 0  # story day of the last visible change

    _glyphs: dict[str, GlyphSpec] = PrivateAttr(default_factory=dict)
    _markers: dict[str, str] = PrivateAttr(default_factory=dict)  # marker glyph -> place key
    _anchor_cells: dict[Cell, str] = PrivateAttr(default_factory=dict)
    _marker_cells: set[Cell] = PrivateAttr(default_factory=set)
    _path_cache: dict[tuple[Cell, Cell], tuple[Cell, ...]] = PrivateAttr(default_factory=dict)
    _slot_cache: dict[str, tuple[Cell, ...]] = PrivateAttr(default_factory=dict)
    _main: set[Cell] | None = PrivateAttr(default=None)

    def model_post_init(self, _context: Any) -> None:
        self._glyphs = {g.ch: g for g in self.legend}
        self._markers = {p.marker: p.key for p in self.places.values() if p.marker}
        self._anchor_cells = {p.anchor: p.key for p in self.places.values()}
        self._marker_cells = {p.anchor for p in self.places.values() if p.marker}

    # ----------------------------------------------------------------- geometry
    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    @property
    def height(self) -> int:
        return len(self.rows)

    def in_bounds(self, cell: Cell) -> bool:
        x, y = cell
        return 0 <= y < self.height and 0 <= x < self.width

    def glyph(self, ch: str) -> GlyphSpec:
        """What a map character means (legend, else convention, else scenery)."""
        known = self._glyphs.get(ch)
        if known is not None:
            return known
        if ch in self._markers:
            return GlyphSpec(ch=ch, name=self.places[self._markers[ch]].display(), walkable=True, style="door")
        if ch == " ":
            return GlyphSpec(ch=ch, name="", walkable=False, style="void")
        if ch in _CONVENTIONS:
            style, walkable, name = _CONVENTIONS[ch]
            return GlyphSpec(ch=ch, name=name, walkable=walkable, style=style)
        if ch.isalnum():
            return GlyphSpec(ch=ch, name="lettering", walkable=False, style="other")
        return GlyphSpec(ch=ch, name="obstacle", walkable=False, style="other")

    def terrain_walkable(self, cell: Cell) -> bool:
        return self.in_bounds(cell) and self.glyph(self.rows[cell[1]][cell[0]]).walkable

    def walkable(self, cell: Cell) -> bool:
        return self.in_bounds(cell) and (cell in self._marker_cells or self.terrain_walkable(cell))

    def neighbours(self, cell: Cell) -> list[Cell]:
        x, y = cell
        return [c for c in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)) if self.walkable(c)]

    def place_at(self, cell: Cell) -> str | None:
        return self._anchor_cells.get(cell)

    def anchor(self, key: str) -> Cell:
        return self.places[key].anchor

    def path(self, start: Cell, goal: Cell) -> tuple[Cell, ...]:
        """Cells to walk through to reach `goal` (start excluded). Empty when already there."""
        if start == goal:
            return ()
        hit = self._path_cache.get((start, goal))
        if hit is not None:
            return hit
        prev: dict[Cell, Cell | None] = {start: None}
        queue: deque[Cell] = deque([start])
        while queue:
            cur = queue.popleft()
            if cur == goal:
                break
            for nxt in self.neighbours(cur):
                if nxt not in prev:
                    prev[nxt] = cur
                    queue.append(nxt)
        if goal not in prev:
            raise ValueError(f"no walkable path from {start} to {goal}")
        steps: list[Cell] = []
        node: Cell | None = goal
        while node is not None and node != start:
            steps.append(node)
            node = prev[node]
        result = tuple(reversed(steps))
        self._path_cache[(start, goal)] = result
        return result

    def reachable(self, start: Cell) -> set[Cell]:
        if not self.walkable(start):
            return set()
        seen = {start}
        queue: deque[Cell] = deque([start])
        while queue:
            cur = queue.popleft()
            for nxt in self.neighbours(cur):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def main_area(self) -> set[Cell]:
        """The walkable region that contains the person's home: where the story can happen."""
        if self._main is None:
            self._main = self.reachable(self.anchor(self.home))
        return self._main

    def nearest_in_main(self, cell: Cell) -> Cell:
        """The closest cell of the main area (used when a change strands someone)."""
        main = self.main_area()
        if cell in main or not main:
            return cell if cell in main else self.anchor(self.home)
        x, y = cell
        return min(main, key=lambda c: (abs(c[0] - x) + abs(c[1] - y), c[1], c[0]))

    def slots(self, key: str, count: int = 8) -> tuple[Cell, ...]:
        """Cells near a place's anchor, nearest first (the anchor itself is slot 0)."""
        cached = self._slot_cache.get(key)
        if cached is not None:
            return cached
        origin = self.anchor(key)
        seen = {origin}
        order = [origin]
        queue: deque[Cell] = deque([origin])
        while queue and len(order) < count:
            cur = queue.popleft()
            for nxt in self.neighbours(cur):
                if nxt not in seen:
                    seen.add(nxt)
                    order.append(nxt)
                    queue.append(nxt)
        result = tuple(order[:count])
        self._slot_cache[key] = result
        return result

    # ----------------------------------------------------------------- places
    def has(self, key: str | None) -> bool:
        return key is not None and key in self.places

    def lookup(self, text: str | None) -> str | None:
        """Resolve a key, a name, a label or a marker as an LLM might write it."""
        if not text:
            return None
        raw = text.strip()
        low = raw.lower()
        if low in self.places:
            return low
        if raw in self._markers:
            return self._markers[raw]
        if slugify(low) in self.places:
            return slugify(low)
        for key, place in self.places.items():
            names = {place.name.lower(), place.display().lower(),
                     re.sub(r"^(?:the|a|an)\s+", "", place.name.lower())}
            if low in names:
                return key
        return None

    def label(self, key: str | None) -> str:
        return self.places[key].display() if key in self.places else "somewhere"

    def at_phrase(self, key: str | None) -> str:
        """'at home', 'in the park': for sentences about being somewhere."""
        if key not in self.places:
            return "in town"
        if key == self.home:
            return "at home"
        place = self.places[key]
        return f"{place.prep} {place.name}"

    def to_phrase(self, key: str) -> str:
        """'home', 'the market': for sentences about going somewhere."""
        if key == self.home:
            return "home"
        return self.places[key].name if key in self.places else "somewhere"

    def places_affording(self, kind: str) -> list[str]:
        return [k for k, p in self.places.items() if kind in p.affords]

    def pick(self, kind: str, rng: random.Random | None = None, *, avoid: str | None = None) -> str | None:
        """A place that suits `kind` (None if the world has none)."""
        options = [k for k in self.places_affording(kind) if k != avoid] or self.places_affording(kind)
        if not options:
            return None
        return rng.choice(options) if rng else options[0]

    def default_kind(self, key: str) -> str:
        return self.places[key].default_kind if key in self.places else "relax"

    def season_at(self, story_day: int) -> str:
        return self.seasons[(max(0, story_day) // self.season_days) % len(self.seasons)]

    # ----------------------------------------------------------------- change
    def apply_patch(self, patch: WorldPatch, *, story_day: int = 0) -> tuple[World | None, list[str]]:
        """Return (new world, []) or (None, problems). Never mutates `self`."""
        errors: list[str] = []
        rows = list(self.rows)
        for idx, text in patch.rows.items():
            if not 0 <= idx < self.height:
                errors.append(f"row {idx} does not exist (rows are numbered 0..{self.height - 1})")
                continue
            problem = _row_problem(text)
            if problem:
                errors.append(f"row {idx}: {problem}")
            elif len(text.rstrip()) > self.width:
                errors.append(f"row {idx} is {len(text.rstrip())} characters but the map is {self.width} wide; "
                              "every replacement row must fit the map's width")
            else:
                rows[idx] = text.ljust(self.width)[: self.width]

        legend = {g.ch: g for g in self.legend}
        for raw in patch.legend:
            glyph = parse_glyph(raw)
            if glyph is not None:
                legend[glyph.ch] = glyph

        places = dict(self.places)
        for key in patch.remove_places:
            slug = self.lookup(key) or slugify(str(key))
            if slug == self.home:
                errors.append(f"place '{slug}' is the person's home and cannot be removed")
            elif slug in places:
                del places[slug]
        for raw in patch.places:
            if not isinstance(raw, dict):
                continue
            existing = places.get(slugify(str(raw.get("key") or raw.get("name") or "")))
            place, problems = parse_place(raw, existing)
            errors.extend(problems)
            if place is not None:
                places[place.key] = place
        if errors:
            return None, errors

        changed = sum(a != b for old, new in zip(self.rows, rows, strict=True) for a, b in zip(old, new, strict=True))
        if changed > MAX_CHANGED_FRACTION * self.width * self.height:
            return None, [(f"the patch rewrites {changed} of {self.width * self.height} cells; change at most "
                          f"{int(MAX_CHANGED_FRACTION * 100)}% of the map at once")]

        candidate = self.model_copy(update={
            "rows": rows, "legend": list(legend.values()), "places": places,
            "revision": self.revision + 1, "changed_day": story_day,
        })
        errors = resolve_anchors(candidate)
        errors += check_world(candidate)
        if errors:
            return None, errors
        return candidate, []


# ======================================================================= parsing and checking
def _row_problem(text: Any) -> str | None:
    if not isinstance(text, str):
        return "is not text"
    if any(not _printable(c) for c in text):
        return "contains characters other than printable ASCII (no tabs, no unicode)"
    return None


def parse_glyph(raw: Any) -> GlyphSpec | None:
    if not isinstance(raw, dict):
        return None
    ch = raw.get("ch", raw.get("glyph", raw.get("char", "")))
    style, walkable, name = _CONVENTIONS.get(ch, ("other", False, "")) if isinstance(ch, str) else ("other", False, "")
    try:  # whatever the entry leaves out falls back to the glyph's conventional meaning
        return GlyphSpec.model_validate({
            "ch": ch,
            "name": str(raw.get("name") or name)[:40],
            "walkable": bool(raw["walkable"]) if "walkable" in raw else walkable,
            "style": str(raw.get("style") or style),
        })
    except ValidationError:
        return None


def parse_place(raw: dict[str, Any], existing: Place | None = None) -> tuple[Place | None, list[str]]:
    """Build a Place from an LLM's dict. The anchor is filled in later from the marker."""
    base = existing.model_dump() if existing else {}
    name = str(raw.get("name") or base.get("name") or raw.get("key") or "").strip()
    key = slugify(str(raw.get("key") or base.get("key") or name))
    if not name:
        return None, [f"a place needs a name (got {raw!r})"[:120]]
    affords_raw = raw.get("affords", base.get("affords"))
    affords: list[str] = []
    for kind in affords_raw if isinstance(affords_raw, list) else []:
        k = str(kind).strip().lower()
        if k in KINDS and k not in affords:
            affords.append(k)
    marker = raw.get("marker", base.get("marker"))
    marker = marker if isinstance(marker, str) and len(marker) == 1 and _printable(marker) and marker != " " else None
    prep = str(raw.get("prep", base.get("prep", "at"))).strip().lower()

    def number(field: str, default: float, hi: float) -> float:
        try:
            return max(0.0, min(hi, float(raw.get(field, base.get(field, default)))))
        except (TypeError, ValueError):
            return default

    try:
        place = Place(
            key=key, name=name[:60], label=str(raw.get("label", base.get("label", "")))[:60],
            prep=prep if prep in PREPOSITIONS else "at", marker=marker,
            anchor=tuple(base["anchor"]) if "anchor" in base else (0, 0),  # type: ignore[arg-type]
            affords=affords or list(base.get("affords") or ["relax"]),
            meal_cost=number("meal_cost", 2.0, 10), visit_cost=number("visit_cost", 0.0, 5),
        )
    except ValidationError as exc:
        return None, [f"place '{key}' is invalid: {exc.errors()[0]['msg']}"]
    return place, []


def resolve_anchors(world: World) -> list[str]:
    """Set every marker place's anchor from the map. Returns problems (and rebuilds the lookup tables)."""
    errors: list[str] = []
    found: dict[str, list[Cell]] = {}
    for y, row in enumerate(world.rows):
        for x, ch in enumerate(row):
            found.setdefault(ch, []).append((x, y))
    for key, place in list(world.places.items()):
        if not place.marker:
            continue
        cells = found.get(place.marker, [])
        if len(cells) != 1:
            errors.append(
                f"place '{key}': its marker '{place.marker}' "
                + ("is not on the map" if not cells else f"appears {len(cells)} times (it must appear exactly once)")
                + "; draw it once on a spot where people can stand")
            continue
        world.places[key] = place.model_copy(update={"anchor": cells[0]})
    world.model_post_init(None)
    # fresh containers, never `.clear()`: a `model_copy` shares its private dicts with the original
    world._path_cache, world._slot_cache, world._main = {}, {}, None
    return errors


def size_problem(width: int, height: int) -> str | None:
    if MIN_WIDTH <= width <= MAX_WIDTH and MIN_HEIGHT <= height <= MAX_HEIGHT:
        return None
    return f"the map is {width}x{height}; it must be between {MIN_WIDTH}x{MIN_HEIGHT} and {MAX_WIDTH}x{MAX_HEIGHT}"


def check_world(world: World) -> list[str]:
    """Everything that must hold for a world to be playable. Returns human-readable problems."""
    errors: list[str] = []
    problem = size_problem(world.width, world.height)
    if problem:
        return [problem]
    if len({len(r) for r in world.rows}) != 1:
        errors.append("the map rows have different lengths")
        return errors
    n = len(world.places)
    if not MIN_PLACES <= n <= MAX_PLACES:
        errors.append(f"the world has {n} places; it needs between {MIN_PLACES} and {MAX_PLACES}")
    if world.home not in world.places:
        errors.append(f"'home' names '{world.home}', which is not one of the places")
        return errors
    if "sleep" not in world.places[world.home].affords:
        errors.append("the home place must afford 'sleep'")
    if not world.places_affording("eat"):
        errors.append("no place affords 'eat'; the person must be able to get food somewhere")

    seen: dict[Cell, str] = {}
    for key, place in world.places.items():
        if not world.in_bounds(place.anchor):
            errors.append(f"place '{key}' lies outside the map")
        elif place.anchor in seen:
            errors.append(f"places '{seen[place.anchor]}' and '{key}' share one spot; each needs its own marker cell")
        elif not place.marker and not world.terrain_walkable(place.anchor):
            errors.append(f"place '{key}' is anchored on a cell that cannot be walked on")
        seen.setdefault(place.anchor, key)
    if errors:
        return errors

    main = world.main_area()
    if len(main) < MIN_WALKABLE_CELLS:
        errors.append(f"only {len(main)} walkable cells are connected to home; draw roads, paths or floors "
                      f"(walkable glyphs in the legend) linking every place")
    cut_off = [k for k, p in world.places.items() if p.anchor not in main]
    if cut_off:
        walk = ", ".join(f"'{g.ch}'" for g in world.legend if g.walkable) or "none"
        where = "; ".join(f"'{k}' at x={world.places[k].anchor[0]},y={world.places[k].anchor[1]}" for k in cut_off)
        errors.append(f"these places cannot be reached on foot from home: {where}. Connect them with walkable "
                      f"cells. (Glyphs currently marked walkable in the legend: {walk}; markers are always walkable.)")
    return errors


def parse_world(obj: Any) -> tuple[World | None, list[str]]:
    """Build a World from an LLM's JSON. Returns (world, []) or (None, problems the LLM can fix)."""
    if not isinstance(obj, dict):
        return None, ["the reply must be one JSON object"]
    errors: list[str] = []

    raw_map = obj.get("map", obj.get("rows", obj.get("ascii")))
    if isinstance(raw_map, str):
        raw_map = raw_map.replace("\r", "").split("\n")
    if not isinstance(raw_map, list) or not raw_map or not all(isinstance(r, str) for r in raw_map):
        return None, ["'map' must be a list of strings, one per row of the ASCII map"]
    while raw_map and not raw_map[0].strip():
        raw_map.pop(0)
    while raw_map and not raw_map[-1].strip():
        raw_map.pop()
    for i, row in enumerate(raw_map):
        problem = _row_problem(row)
        if problem:
            errors.append(f"map row {i}: {problem}")
    if errors:
        return None, errors
    width = max((len(r) for r in raw_map), default=0)
    problem = size_problem(width, len(raw_map))
    if problem:  # report this first: markers and routes cannot be judged on a canvas of the wrong size
        return None, [problem]
    rows = [r.ljust(width) for r in raw_map]

    legend: list[GlyphSpec] = []
    raw_legend = obj.get("legend", [])
    if isinstance(raw_legend, dict):  # {"~": {"name": ..}} or {"~": "water"}
        raw_legend = [{"ch": k, **(v if isinstance(v, dict) else {"name": str(v)})} for k, v in raw_legend.items()]
    for entry in raw_legend if isinstance(raw_legend, list) else []:
        glyph = parse_glyph(entry)
        if glyph is not None:
            legend.append(glyph)

    places: dict[str, Place] = {}
    raw_places = obj.get("places", [])
    if not isinstance(raw_places, list) or not raw_places:
        return None, ["'places' must list the places people can go to (each with a marker drawn on the map)"]
    for raw in raw_places:
        if not isinstance(raw, dict):
            continue
        place, problems = parse_place(raw)
        errors.extend(problems)
        if place is None:
            continue
        if place.key in places:
            errors.append(f"two places share the key '{place.key}'")
        elif not place.marker:
            errors.append(f"place '{place.key}' has no marker; give it a unique single character drawn on the map")
        places[place.key] = place
    markers = [p.marker for p in places.values() if p.marker]
    if len(markers) != len(set(markers)):
        errors.append("two places use the same marker; every place needs its own")
    if not places or errors:
        return None, errors or ["no usable places"]

    named = str(obj.get("home") or "").strip()
    home = next((k for k in (named.lower(), slugify(named)) if k in places), "")
    if not home:
        home = next((k for k, p in places.items() if "sleep" in p.affords), "")
    if not home:
        return None, ["no place affords 'sleep'; name the person's home in 'home' and give it affords including 'sleep'"]

    seasons = [str(s).strip()[:24] for s in obj.get("seasons", []) if isinstance(s, str) and s.strip()][:8]
    try:
        season_days = max(5, min(400, int(obj.get("season_days", 91))))
    except (TypeError, ValueError):
        season_days = 91
    try:
        world = World(
            name=str(obj.get("name") or "the world").strip()[:60], description=str(obj.get("description") or "")[:1200],
            climate=str(obj.get("climate") or "")[:300], rows=rows, legend=legend, places=places, home=home,
            seasons=seasons or list(DEFAULT_SEASONS), season_days=season_days, source="llm",
        )
    except ValidationError as exc:
        return None, [f"the world is malformed: {exc.errors()[0]['msg']}"]
    errors = resolve_anchors(world)
    if errors:
        return None, errors
    errors = check_world(world)
    return (None, errors) if errors else (world, [])


# ======================================================================= the built-in town
_TOWN_W, _TOWN_H = 46, 14
_ROAD_BANK_Y, _ROAD_MAIN_Y = 2, 6

# key -> (label, left x, top y, door on the bottom wall?)
_BUILDINGS: dict[str, tuple[str, int, int, bool]] = {
    "home": ("HOME", 3, 3, True), "work": ("WORK", 13, 3, True), "cafe": ("CAFE", 23, 3, True),
    "library": ("LIBRARY", 33, 3, True), "market": ("MARKET", 3, 7, False),
    "tavern": ("TAVERN", 15, 7, False), "temple": ("TEMPLE", 27, 7, False),
}


def _put(rows: list[list[str]], x: int, y: int, text: str) -> None:
    for i, ch in enumerate(text):
        rows[y][x + i] = ch


def builtin_world() -> World:
    """The fixed town used when no LLM can draw a world (and for lives saved before worlds existed)."""
    decor = random.Random(7)  # fixed: the town looks the same in every life
    rows = [[" "] * _TOWN_W for _ in range(_TOWN_H)]
    for y in (0, 1):
        for x in range(_TOWN_W):
            rows[y][x] = "~"
    for x in range(1, _TOWN_W - 1):
        rows[_ROAD_BANK_Y][x] = "."
        rows[_ROAD_MAIN_Y][x] = "."
    for y in range(_ROAD_BANK_Y, 13):
        rows[y][1] = "."
        rows[y][_TOWN_W - 2] = "."
    for y in range(10, 13):
        for x in range(2, _TOWN_W - 2):
            rows[y][x] = '"'
    for y in (10, 12):
        for x in range(3, _TOWN_W - 3):
            if decor.random() < 0.2:
                rows[y][x] = "*"
    _put(rows, 16, 13, "- THE PARK -")
    for y in range(2, 13):
        for x in range(_TOWN_W):
            if rows[y][x] == " " and decor.random() < 0.12:
                rows[y][x] = ","

    anchors: dict[str, Cell] = {}
    for key, (label, x, top, door_bottom) in _BUILDINGS.items():
        width = len(label) + 4
        border = "+" + "-" * (width - 2) + "+"
        door_x = x + width // 2
        _put(rows, x, top, border)
        _put(rows, x, top + 1, f"| {label} |")
        _put(rows, x, top + 2, border)
        rows[top + 2 if door_bottom else top][door_x] = "="
        anchors[key] = (door_x, _ROAD_MAIN_Y)
    anchors["river"], anchors["park"] = (20, _ROAD_BANK_Y), (22, 11)

    def place(key: str, name: str, label: str, affords: list[str], *, prep: str = "at",
              meal: float = 2.0, visit: float = 0.0) -> Place:
        return Place(key=key, name=name, label=label, prep=prep, anchor=anchors[key], affords=affords,
                     meal_cost=meal, visit_cost=visit)

    places = [
        place("home", "home", "Home", ["relax", "sleep", "eat"], meal=1.0),
        place("work", "work", "Workplace", ["work"]),
        place("cafe", "the cafe", "Cafe", ["socialize", "eat"], meal=3.5, visit=1.2),
        place("library", "the library", "Library", ["study", "relax"]),
        place("market", "the market", "Market", ["errand", "eat"], meal=2.0, visit=1.0),
        place("tavern", "the tavern", "Tavern", ["socialize", "eat"], meal=3.0, visit=1.5),
        place("temple", "the temple", "Temple", ["reflect"]),
        place("park", "the park", "Park", ["relax", "exercise", "explore"], prep="in"),
        place("river", "the riverbank", "Riverbank", ["reflect", "explore", "relax"], prep="by"),
    ]
    legend = [
        GlyphSpec(ch="~", name="river", walkable=False, style="water"),
        GlyphSpec(ch=".", name="road", walkable=True, style="path"),
        GlyphSpec(ch='"', name="park grass", walkable=True, style="grass"),
        GlyphSpec(ch=",", name="grass", walkable=False, style="grass"),
        GlyphSpec(ch="*", name="tree", walkable=False, style="foliage"),
        GlyphSpec(ch="=", name="door", walkable=False, style="door"),
        GlyphSpec(ch="+", name="wall", walkable=False, style="wall"),
        GlyphSpec(ch="-", name="wall", walkable=False, style="wall"),
        GlyphSpec(ch="|", name="wall", walkable=False, style="wall"),
    ]
    return World(
        name="the town", description="A small riverside town of lanes, shops and a park.",
        climate="temperate", rows=["".join(r) for r in rows], legend=legend,
        places={p.key: p for p in places}, home="home", source="builtin",
    )
