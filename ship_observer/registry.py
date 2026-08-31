from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .ais_client import POSITION_REPORT, SHIP_STATIC_DATA, AisMessage
from .config import Settings
from .models import Vessel
from .shiptypes import classify, priority_for


@dataclass(frozen=True)
class RegistryChange:
    vessel: Vessel
    entered: bool = False
    departed: bool = False
    static_resolved_now: bool = False


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.replace("@", " ").strip()
    return stripped or None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _format_eta(eta: Any) -> str | None:
    """AIS ETA has no year. Render as MM-DD HH:MM, or None if unset."""
    if not isinstance(eta, dict):
        return None
    month, day = eta.get("Month"), eta.get("Day")
    hour, minute = eta.get("Hour"), eta.get("Minute")
    if not all(isinstance(v, int) for v in (month, day, hour, minute)):
        return None
    if month == 0 or day == 0:
        return None
    return f"{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


class VesselRegistry:
    """In-memory state for vessels currently inside the bounding box.

    One Vessel per *visit*: a re-entry after departure is a new Vessel with a
    fresh entered_at and no log_id, so storage opens a new ship_log row.
    """

    def __init__(self, settings: Settings,
                 clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._live: dict[int, Vessel] = {}
        self._departed: deque[Vessel] = deque(maxlen=max(settings.max_ships, 1))

    def apply(self, msg: AisMessage) -> RegistryChange:
        vessel = self._live.get(msg.mmsi)
        entered = vessel is None
        if vessel is None:
            vessel = Vessel(mmsi=msg.mmsi, entered_at=msg.received_at,
                            last_seen=msg.received_at)
            self._live[msg.mmsi] = vessel

        vessel.last_seen = max(vessel.last_seen, msg.received_at)

        if msg.meta_name and not vessel.static_resolved:
            vessel.name = msg.meta_name

        static_resolved_now = False
        if msg.message_type == POSITION_REPORT:
            self._apply_position(vessel, msg)
        elif msg.message_type == SHIP_STATIC_DATA:
            static_resolved_now = not vessel.static_resolved
            self._apply_static(vessel, msg.payload)

        # A position outside the box means the vessel has left; do not wait
        # out SHIP_TIMEOUT_SECONDS.
        if (msg.lat is not None and msg.lon is not None
                and not self._settings.bbox.contains(msg.lat, msg.lon)):
            self._depart(vessel, msg.received_at, "left_bbox")
            return RegistryChange(vessel, entered=entered, departed=True,
                                  static_resolved_now=static_resolved_now)

        return RegistryChange(vessel, entered=entered,
                              static_resolved_now=static_resolved_now)

    def _apply_position(self, vessel: Vessel, msg: AisMessage) -> None:
        p = msg.payload
        vessel.position_count += 1
        lat = msg.lat if msg.lat is not None else _number(p.get("Latitude"))
        lon = msg.lon if msg.lon is not None else _number(p.get("Longitude"))
        if lat is not None and lon is not None:
            if vessel.first_lat is None:
                vessel.first_lat, vessel.first_lon = lat, lon
            vessel.last_lat, vessel.last_lon = lat, lon
        sog = _number(p.get("Sog"))
        if sog is not None and (vessel.max_sog is None or sog > vessel.max_sog):
            vessel.max_sog = sog
        vessel.last_cog = _number(p.get("Cog"))
        heading = p.get("TrueHeading")
        vessel.last_heading = heading if isinstance(heading, int) else None
        status = p.get("NavigationalStatus")
        vessel.nav_status = status if isinstance(status, int) else None
        vessel.raw_position = dict(p)

    def seed_static(self, mmsi: int, payload: dict[str, Any]) -> bool:
        """Resolve a live vessel from a previous visit's static payload.

        Ship type, call sign and dimensions do not change between visits, so
        cached data is as good as a live message - except raw_static, which is
        cleared so a seeded visit stays distinguishable in the log from one
        that resolved over the air. Live static data always wins: a vessel
        that has already resolved is never touched.
        """
        vessel = self._live.get(mmsi)
        if vessel is None or vessel.static_resolved:
            return False
        self._apply_static(vessel, payload)
        vessel.raw_static = None
        return True

    def _apply_static(self, vessel: Vessel, p: dict[str, Any]) -> None:
        vessel.name = _clean(p.get("Name")) or vessel.name
        vessel.call_sign = _clean(p.get("CallSign")) or vessel.call_sign
        vessel.destination = _clean(p.get("Destination")) or vessel.destination

        # Real AIS static data can arrive as partial retransmissions (Class B
        # splits it across separate frames; any decoder can also just drop a
        # field). A later message missing a field must never regress state a
        # prior message already resolved - keep vessel.<field> whenever the
        # new value is absent or malformed, exactly like the string fields
        # above. This matters most for category/priority: a TANKER silently
        # reverting to UNKNOWN would let it lose its display slot.
        ship_type = p.get("Type")
        if isinstance(ship_type, int):
            vessel.ship_type = ship_type
            vessel.category = classify(vessel.ship_type)
            vessel.priority = priority_for(vessel.category)

        imo = p.get("ImoNumber")
        if isinstance(imo, int) and imo > 0:
            vessel.imo = imo

        dim = p.get("Dimension")
        if isinstance(dim, dict):
            a, b = _number(dim.get("A")), _number(dim.get("B"))
            c, d = _number(dim.get("C")), _number(dim.get("D"))
            if a is not None and b is not None:
                vessel.length_m = a + b
            if c is not None and d is not None:
                vessel.beam_m = c + d

        draught = _number(p.get("MaximumStaticDraught"))
        if draught is not None:
            vessel.draught_m = draught

        eta = _format_eta(p.get("Eta"))
        if eta is not None:
            vessel.eta = eta

        vessel.static_resolved = True
        # raw_static intentionally always overwrites: it exists to show what
        # the most recent message actually contained, partial or not.
        vessel.raw_static = dict(p)

    def _depart(self, vessel: Vessel, when: datetime, reason: str) -> None:
        self._live.pop(vessel.mmsi, None)
        vessel.departed_at = when
        vessel.depart_reason = reason
        self._departed.appendleft(vessel)

    def prune(self, now: datetime | None = None) -> list[Vessel]:
        """Expire vessels silent for longer than SHIP_TIMEOUT_SECONDS."""
        now = now or self._clock()
        cutoff = now - timedelta(seconds=self._settings.ship_timeout_seconds)
        expired = sorted(
            (v for v in self._live.values() if v.last_seen < cutoff),
            key=lambda v: v.last_seen,
        )
        for vessel in expired:
            self._depart(vessel, now, "timeout")
        return expired

    def close_all(self, now: datetime | None = None) -> list[Vessel]:
        """Close every open visit. Called on shutdown so no visit is left open."""
        now = now or self._clock()
        vessels = list(self._live.values())
        for vessel in vessels:
            self._depart(vessel, now, "shutdown")
        return vessels

    def live(self) -> list[Vessel]:
        """Newest entrant first - the spec's display ordering."""
        return sorted(self._live.values(), key=lambda v: v.entered_at, reverse=True)

    def departed(self) -> list[Vessel]:
        """Most recently departed first, capped at MAX_SHIPS."""
        return list(self._departed)
