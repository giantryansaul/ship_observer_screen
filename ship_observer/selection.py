from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import Settings
from .models import Vessel


@dataclass(frozen=True)
class Slots:
    live: list[Vessel] = field(default_factory=list)
    history: list[Vessel] = field(default_factory=list)
    show_divider: bool = False

    @property
    def total(self) -> int:
        return len(self.live) + len(self.history)


@dataclass(frozen=True)
class RotationView:
    """One page of the rotation the 2- and 1-ship modes page through.

    Those modes show far fewer vessels than the box can hold, so nothing is
    dropped for want of a slot the way `Slots` drops it - the whole box is
    shown a page at a time instead. `from_history` is a property of the
    page, not of a vessel: the fallback only happens when the box is empty,
    so a page is either all live or all recently-departed.
    """

    vessels: list[Vessel] = field(default_factory=list)
    from_history: bool = False
    pages: int = 0
    page: int = 0

    @classmethod
    def from_slots(cls, slots: Slots, page_size: int) -> "RotationView":
        """A single static page of already-selected slots.

        Still renders - the /panel audit tool, PNG dumps - have no rotation
        clock to page anything, so they hand over the slots the 3-ship
        layout would have drawn and get the first page of them.
        """
        vessels = (slots.live or slots.history)[:max(0, page_size)]
        if not vessels:
            return cls()
        return cls(vessels=vessels, from_history=not slots.live,
                   pages=1, page=0)


def filtered_reason(vessel: Vessel, settings: Settings) -> str | None:
    """Return why a vessel is filtered, or None if eligible."""
    if not vessel.static_resolved:
        return None
    if vessel.category in settings.exclude_categories:
        return f"excluded_category:{vessel.category.value}"
    if settings.min_length_meters > 0:
        if vessel.length_m is None or vessel.length_m < settings.min_length_meters:
            return f"min_length:{settings.min_length_meters}m"
    return None


def is_eligible(vessel: Vessel, settings: Settings) -> bool:
    """Hard display gates. The log is never filtered - only the panel is.

    A vessel whose ShipStaticData has not arrived is always eligible: static
    data can take six minutes, and gating on it would hide real traffic.
    """
    return filtered_reason(vessel, settings) is None


def _ranked(vessels: list[Vessel], settings: Settings) -> list[Vessel]:
    """Every eligible vessel, best display candidate first."""
    eligible = [v for v in vessels if is_eligible(v, settings)]
    if settings.priority_selection:
        # Highest priority wins a slot; ties broken by most recent entry.
        return sorted(eligible, key=lambda v: (v.priority, v.entered_at),
                      reverse=True)
    return sorted(eligible, key=lambda v: v.entered_at, reverse=True)


def _eligible_history(departed: list[Vessel], settings: Settings) -> list[Vessel]:
    """`departed` is already most-recently-departed first; preserve that."""
    if not settings.display_history:
        return []
    return [v for v in departed if is_eligible(v, settings)]


def _select(vessels: list[Vessel], settings: Settings, limit: int) -> list[Vessel]:
    """Pick up to `limit` vessels, then order the winners newest-entrant-first."""
    if limit <= 0:
        return []
    chosen = _ranked(vessels, settings)[:limit]
    return sorted(chosen, key=lambda v: v.entered_at, reverse=True)


def select_slots(live: list[Vessel], departed: list[Vessel],
                 settings: Settings, capacity: int) -> Slots:
    """Assign display slots.

    `capacity` is what the panel can physically fit; the effective slot count
    is min(MAX_SHIPS, capacity).
    """
    limit = min(settings.max_ships, capacity)
    chosen_live = _select(live, settings, limit)

    remaining = limit - len(chosen_live)
    if remaining <= 0:
        return Slots(live=chosen_live, history=[], show_divider=False)

    history = _eligible_history(departed, settings)[:remaining]
    return Slots(live=chosen_live, history=history, show_divider=bool(history))


def select_rotation(live: list[Vessel], departed: list[Vessel],
                    settings: Settings, page_size: int,
                    page: int = 0) -> RotationView:
    """One page of the rotation, in the same priority order `select_slots`
    ranks by.

    Every eligible vessel gets a turn rather than only the top `MAX_SHIPS`:
    with one or two on screen at a time, dropping the rest would hide most
    of the box. `page` is normalized modulo the page count, so a caller's
    stale page number can never index past the end of a shrinking box.
    """
    if page_size <= 0:
        return RotationView()

    vessels = _ranked(live, settings)
    from_history = False
    if not vessels:
        # Only when the box is empty, matching the 3-ship layout's history
        # backfill - and only when the user asked to see departures at all.
        vessels = _eligible_history(departed, settings)
        from_history = bool(vessels)
    if not vessels:
        return RotationView()

    pages = math.ceil(len(vessels) / page_size)
    page = page % pages
    start = page * page_size
    return RotationView(vessels=vessels[start:start + page_size],
                        from_history=from_history, pages=pages, page=page)
