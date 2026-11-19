"""Per-tick body and money model. Pure functions on `LifeState`; no randomness, no I/O.

All numbers are per tick (10 simulated minutes). They are deliberately gentle: a healthy
person who follows a sensible routine stays healthy; neglect compounds slowly, never instantly.
"""

from __future__ import annotations

from mortal.core.constants import TICK_MINUTES
from mortal.core.models import LifeState

# energy, hunger, social, fun, health deltas per tick while doing this kind of activity
KIND_DELTAS: dict[str, tuple[float, float, float, float, float]] = {
    "travel":    (-0.6, 1.5, -0.4, -0.3, 0.0),
    "sleep":     (2.2, 0.5, -0.1, 0.0, 0.0),
    "eat":       (0.2, -12.0, 0.3, 0.5, 0.0),
    "work":      (-0.9, 1.5, 0.0, -0.7, 0.0),
    "socialize": (-0.3, 1.4, 3.0, 1.0, 0.0),
    "relax":     (0.2, 1.4, -0.3, 2.0, 0.0),
    "exercise":  (-1.4, 2.0, -0.2, 0.8, 0.15),
    "explore":   (-0.7, 1.5, -0.2, 1.6, 0.0),
    "study":     (-0.8, 1.5, -0.3, 0.4, 0.0),
    "errand":    (-0.6, 1.5, -0.1, -0.2, 0.0),
    "reflect":   (0.1, 1.2, -0.3, 0.8, 0.0),
    "idle":      (-0.35, 1.4, -0.4, -0.4, 0.0),
}

MEAL_COST = {"home": 1.0, "cafe": 3.5, "tavern": 3.0, "market": 2.0}
OUTING_COST = {"cafe": 1.2, "tavern": 1.5}  # drinks while socialising there


def social_scale(extraversion: int) -> float:
    """Introverts lose and gain social energy more slowly; extraverts faster."""
    return 0.5 + extraversion / 10


def tick_cost(kind: str, place: str | None) -> float:
    """Money spent this tick (positive = spent)."""
    if kind == "eat" and place is not None:
        return MEAL_COST.get(place, 2.0)
    if kind == "socialize" and place in OUTING_COST:
        return OUTING_COST[place]
    if kind == "errand" and place == "market":
        return 1.0
    return 0.0


def tick_income(state: LifeState, kind: str, place: str | None) -> float:
    if kind != "work":
        return 0.0
    per_tick = state.person.wage_per_hour * TICK_MINUTES / 60
    return per_tick if place == "work" else per_tick * 0.5  # working from elsewhere pays half


def apply_tick(state: LifeState, kind: str, place: str | None, *, company: bool = False) -> None:
    """Advance the person's body and purse by one tick of `kind` at `place`."""
    n = state.needs
    d_energy, d_hunger, d_social, d_fun, d_health = KIND_DELTAS[kind]
    if kind == "sleep" and place != "home":
        d_energy *= 0.5  # sleeping rough restores less
    if company and kind in {"eat", "relax", "socialize", "reflect"}:
        d_social += 1.0
    d_social *= social_scale(state.person.traits.extraversion)

    if kind == "eat" and n.hunger <= 3:
        spend = 0.0  # already full: no point paying
    else:
        spend = tick_cost(kind, place)

    n.energy += d_energy
    n.hunger += d_hunger
    n.social += d_social
    n.fun += d_fun
    n.health += d_health
    n.money += tick_income(state, kind, place) - spend

    # health: neglect hurts, comfort heals
    if n.hunger >= 95:
        n.health -= 0.4
    if n.energy <= 3:
        n.health -= 0.3
    if n.mood <= 5:
        n.health -= 0.05
    if n.hunger < 70 and n.energy > 30 and n.health < 100:
        n.health += 0.05

    # mood drifts toward overall wellbeing, with a nudge from money trouble
    target = (n.energy + (100 - n.hunger) + n.social + n.fun + n.health) / 5
    n.mood += (target - n.mood) * 0.03
    if n.money < 0:
        n.mood -= 0.1
    n.clamp()


def cause_of_death(state: LifeState) -> str:
    n = state.needs
    if n.hunger >= 90:
        return "of starvation"
    if n.energy <= 5:
        return "of exhaustion"
    if n.mood <= 10:
        return "of a broken spirit"
    return "of misfortune"
