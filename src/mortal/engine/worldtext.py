"""Text a language model needs to understand (and edit) a world. Pure string building; no I/O."""

from __future__ import annotations

from mortal.core.constants import KINDS
from mortal.core.world import GLYPH_STYLES, PREPOSITIONS, World


def map_block(world: World) -> str:
    """The map with row numbers; each row sits between | bars so trailing spaces are visible."""
    width = len(str(world.height - 1))
    return "\n".join(f"{i:>{width}}|{row}|" for i, row in enumerate(world.rows))


def legend_lines(world: World) -> str:
    seen: dict[str, None] = {}
    for row in world.rows:
        for ch in row:
            seen.setdefault(ch)
    markers = {p.marker for p in world.places.values() if p.marker}
    lines = []
    for ch in seen:
        if ch == " " or ch in markers:
            continue
        g = world.glyph(ch)
        if g.name == "lettering":
            continue
        lines.append(f"'{ch}' {g.name or g.style} ({'walkable' if g.walkable else 'blocks'}, style {g.style})")
    return "\n".join(f"- {line}" for line in lines) or "- (nothing special)"


def place_lines(world: World, *, with_markers: bool = True) -> str:
    out = []
    for key, p in world.places.items():
        marker = f", marker '{p.marker}'" if with_markers and p.marker else ""
        prices = f", meal {p.meal_cost:g}" if "eat" in p.affords else ""
        prices += f", visit {p.visit_cost:g}" if p.visit_cost else ""
        home = " [the person's home]" if key == world.home else ""
        out.append(f"- {key}: {p.name} ({p.prep} it){home}; affords {', '.join(p.affords)}{prices}{marker}")
    return "\n".join(out)


FORMAT_RULES = f"""How a world is written
- The map is a rectangle of printable ASCII: no tabs, no unicode. Every glyph except spaces, digits and capital letters must be explained in the legend: {{"ch": "~", "name": "salt water", "walkable": false, "style": "water"}}. Styles: {", ".join(GLYPH_STYLES)}. Conventional glyphs (. path, , grass, ~ water, # + - | walls, * trees, ^ rock) default sensibly if you leave them out, but say so explicitly when in doubt. People can only walk on glyphs marked walkable; every place must be reachable on foot from home.
- A place is where people stand to do something. Each has a MARKER: one character (a digit or a capital letter) drawn exactly once on the map, on a walkable spot such as in front of a door or on a quay. Markers are walkable and stay visible; never use a marker character for anything else, and never write words or names into the map (the key lists the places).
- A place has: "key" (short lowercase id), "name" (a noun phrase with its article: "the salt market"), "prep" (one of {", ".join(PREPOSITIONS)}: "at the salt market", "on the quay"), "marker", "affords" (any of {", ".join(KINDS)}; the first is what people mainly do there), "meal_cost" (coins per 10-minute tick of eating, 0-10; home about 1, an inn 3), "visit_cost" (coins per tick of socialising or errands, 0-3).
- The person's home must afford sleep. Somewhere must afford eat. Give the occupation somewhere to afford work."""
