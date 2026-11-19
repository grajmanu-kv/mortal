"""The ASCII town: static geometry, walkability, anchors and shortest paths.

Legend of the base map:  ``.`` road   ``"`` park grass   ``*`` tree   ``~`` water
``+ - |`` building walls   ``=`` door   ``,`` decorative grass.  Only ``.`` and ``"`` are walkable.
Each place owns one *anchor* cell on a walkable tile; a person "is at" a place when standing on it.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass
from functools import lru_cache

Cell = tuple[int, int]  # (x, y)

WIDTH, HEIGHT = 46, 14
ROAD_BANK_Y = 2
ROAD_MAIN_Y = 6
LANE_LEFT_X, LANE_RIGHT_X = 1, WIDTH - 2

# label, left x, top y, door on the bottom wall (True) or the top wall (False)
_BUILDINGS: dict[str, tuple[str, int, int, bool]] = {
    "home": ("HOME", 3, 3, True),
    "work": ("WORK", 13, 3, True),
    "cafe": ("CAFE", 23, 3, True),
    "library": ("LIBRARY", 33, 3, True),
    "market": ("MARKET", 3, 7, False),
    "tavern": ("TAVERN", 15, 7, False),
    "temple": ("TEMPLE", 27, 7, False),
}
_OPEN_AIR = {"river": (20, ROAD_BANK_Y), "park": (22, 11)}
WALKABLE_CHARS = {".", '"'}


@dataclass(frozen=True)
class Place:
    key: str
    anchor: Cell


class TownMap:
    def __init__(self, rows: list[str], places: dict[str, Place]):
        self.rows = rows
        self.height = len(rows)
        self.width = len(rows[0])
        self.places = places
        self._anchor_to_place = {p.anchor: p.key for p in places.values()}
        self._path_cache: dict[tuple[Cell, Cell], tuple[Cell, ...]] = {}
        self._slot_cache: dict[str, tuple[Cell, ...]] = {}

    def walkable(self, cell: Cell) -> bool:
        x, y = cell
        return 0 <= y < self.height and 0 <= x < self.width and self.rows[y][x] in WALKABLE_CHARS

    def neighbours(self, cell: Cell) -> list[Cell]:
        x, y = cell
        out = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        return [c for c in out if self.walkable(c)]

    def place_at(self, cell: Cell) -> str | None:
        return self._anchor_to_place.get(cell)

    def anchor(self, key: str) -> Cell:
        return self.places[key].anchor

    def path(self, start: Cell, goal: Cell) -> tuple[Cell, ...]:
        """Cells to walk through to reach `goal` (start excluded). Empty when already there."""
        if start == goal:
            return ()
        cache_key = (start, goal)
        hit = self._path_cache.get(cache_key)
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
        self._path_cache[cache_key] = result
        return result

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


def _put(rows: list[list[str]], x: int, y: int, text: str) -> None:
    for i, ch in enumerate(text):
        rows[y][x + i] = ch


def _build() -> TownMap:
    rows = [[" "] * WIDTH for _ in range(HEIGHT)]
    decor = random.Random(7)  # fixed: the town looks the same in every life

    for y in (0, 1):  # river
        for x in range(WIDTH):
            rows[y][x] = "~"
    for x in range(1, WIDTH - 1):  # bank road and main street
        rows[ROAD_BANK_Y][x] = "."
        rows[ROAD_MAIN_Y][x] = "."
    for y in range(ROAD_BANK_Y, 13):  # side lanes
        rows[y][LANE_LEFT_X] = "."
        rows[y][LANE_RIGHT_X] = "."
    for y in range(10, 13):  # park
        for x in range(2, WIDTH - 2):
            rows[y][x] = '"'
    for y in (10, 12):  # trees, keeping the middle row clear so the park stays connected
        for x in range(3, WIDTH - 3):
            if decor.random() < 0.2:
                rows[y][x] = "*"
    _put(rows, 16, 13, "- THE PARK -")
    for y in range(2, 13):  # decorative grass in the gaps; buildings are drawn over it afterwards
        for x in range(WIDTH):
            if rows[y][x] == " " and decor.random() < 0.12:
                rows[y][x] = ","

    places: dict[str, Place] = {}
    for key, (label, x, top, door_bottom) in _BUILDINGS.items():
        width = len(label) + 4
        border = "+" + "-" * (width - 2) + "+"
        door_x = x + width // 2
        wall_y = top + 2 if door_bottom else top
        _put(rows, x, top, border)
        _put(rows, x, top + 1, f"| {label} |")
        _put(rows, x, top + 2, border)
        rows[wall_y][door_x] = "="
        places[key] = Place(key, (door_x, ROAD_MAIN_Y))
    for key, anchor in _OPEN_AIR.items():
        places[key] = Place(key, anchor)

    return TownMap(["".join(r) for r in rows], places)


@lru_cache(maxsize=1)
def get_town() -> TownMap:
    return _build()
