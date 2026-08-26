from __future__ import annotations

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


def is_eligible(vessel: Vessel, settings: Settings) -> bool:
    """Hard display gates. The log is never filtered - only the panel is.

    A vessel whose ShipStaticData has not arrived is always eligible: static
    data can take six minutes, and gating on it would hide real traffic.
    """
    if not vessel.static_resolved:
        return True
    if vessel.category in settings.exclude_categories:
        return False
    if settings.min_length_meters > 0:
        if vessel.length_m is None or vessel.length_m < settings.min_length_meters:
            return False
    return True


def _select(vessels: list[Vessel], settings: Settings, limit: int) -> list[Vessel]:
    """Pick up to `limit` vessels, then order the winners newest-entrant-first."""
    if limit <= 0:
        return []
    eligible = [v for v in vessels if is_eligible(v, settings)]
    if settings.priority_selection:
        # Highest priority wins a slot; ties broken by most recent entry.
        ranked = sorted(eligible, key=lambda v: (v.priority, v.entered_at),
                        reverse=True)
    else:
        ranked = sorted(eligible, key=lambda v: v.entered_at, reverse=True)
    chosen = ranked[:limit]
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
    if not settings.display_history or remaining <= 0:
        return Slots(live=chosen_live, history=[], show_divider=False)

    # `departed` is already most-recently-departed first; preserve that order.
    history = [v for v in departed if is_eligible(v, settings)][:remaining]
    return Slots(live=chosen_live, history=history, show_divider=bool(history))
