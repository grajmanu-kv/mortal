"""Plan logic: turning a demi-god's schedule into absolute-time items and merging it in.

Semantics
---------
* The *current* item is the last one that started at or before now.
* A **dawn** plan replaces everything still ahead.
* Any other plan (interruption, divine word, crisis) replaces only the span from its first
  entry to its last; later items from the old plan survive. After the last new entry its
  activity runs for its natural duration, then the interrupted activity *resumes*
  (`INTERRUPT_DURATION`: a nap is 90 minutes, not a night).
"""

from __future__ import annotations

from mortal.core.clock import day_start, parse_clock
from mortal.core.constants import DAY_MINUTES, INTERRUPT_DURATION, KINDS, TICK_MINUTES
from mortal.core.decision import PlanEntry
from mortal.core.models import PlanItem, valid_place


def resolve_entries(entries: list[PlanEntry], now: int) -> list[PlanItem]:
    """Validate and convert entries to absolute-time items inside the current story day."""
    horizon = day_start(now) + DAY_MINUTES
    items: list[PlanItem] = []
    for entry in entries:
        when = parse_clock(entry.at, now)
        kind = entry.kind.strip().lower()
        place = entry.place.strip().lower()
        if when is None or kind not in KINDS or not valid_place(place):
            continue
        when = max(when, now)  # the past collapses to "now"
        if when >= horizon:
            continue
        items.append(PlanItem(t=when, place=place, kind=kind, activity=entry.activity.strip()))
    items.sort(key=lambda i: i.t)  # stable: later entries at the same minute win (last wins in `current_item`)
    return items


def current_item(plan: list[PlanItem], now: int) -> PlanItem | None:
    current: PlanItem | None = None
    for item in plan:  # plan is kept sorted
        if item.t <= now:
            current = item
        else:
            break
    return current


def merge_plan(old: list[PlanItem], new: list[PlanItem], now: int, *, replace_future: bool) -> list[PlanItem]:
    """Return the plan after applying `new`. `old` and `new` are sorted; result is sorted."""
    if not new:
        return list(old)
    resume = current_item(old, now)
    past = [i for i in old if i.t < now]
    if replace_future:
        return past + new
    first, last = new[0].t, new[-1].t
    kept_before = [i for i in old if i.t < first]
    after = [i for i in old if i.t > last]
    merged = kept_before + new + after
    # bracket the interruption: when the last new activity has run its course, go back to what
    # the person was doing, unless the old plan already schedules something in between.
    if resume is not None:
        end = last + INTERRUPT_DURATION.get(new[-1].kind, 45)
        end = (end + TICK_MINUTES - 1) // TICK_MINUTES * TICK_MINUTES
        next_old = after[0].t if after else None
        if next_old is None or end < next_old:
            merged.append(PlanItem(t=end, place=resume.place, kind=resume.kind, activity=resume.activity))
    merged.sort(key=lambda i: i.t)
    return merged
