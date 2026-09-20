from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .ais_client import (POSITION_REPORT, SHIP_STATIC_DATA,
                         STATIC_DATA_REPORT, AisMessage)
from .config import Settings
from .identity import resolve_identity, type_strength
from .models import BroadcastFacts, Vessel
from .shiptypes import priority_for


@dataclass(frozen=True)
class RegistryChange:
    vessel: Vessel
    entered: bool = False
    departed: bool = False
    static_resolved_now: bool = False
    # What a static data message said about the vessel itself, for the
    # vessel store. None for every other message.
    facts: BroadcastFacts | None = None


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.replace("@", " ").strip()
    return stripped or None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _dimensions(dim: Any) -> tuple[float | None, float | None]:
    """(length, beam) from an AIS Dimension block. All-zero offsets are the
    AIS encoding of "not available", not a vessel 0 m long."""
    if not isinstance(dim, dict):
        return None, None
    a, b = _number(dim.get("A")), _number(dim.get("B"))
    c, d = _number(dim.get("C")), _number(dim.get("D"))
    length = a + b if a is not None and b is not None else None
    beam = c + d if c is not None and d is not None else None
    return length or None, beam or None


def _ship_static_facts(p: dict[str, Any]) -> BroadcastFacts:
    ship_type, imo = p.get("Type"), p.get("ImoNumber")
    length, beam = _dimensions(p.get("Dimension"))
    return BroadcastFacts(
        name=_clean(p.get("Name")),
        call_sign=_clean(p.get("CallSign")),
        imo=imo if isinstance(imo, int) and imo > 0 else None,
        ship_type=ship_type if isinstance(ship_type, int) else None,
        length_m=length, beam_m=beam,
    )


def _class_b_facts(p: dict[str, Any]) -> BroadcastFacts:
    """A StaticDataReport frame carries part A (the name) or part B (the
    rest). The part it does not carry rides along zeroed and marked not
    valid, and must not be read as "type 0"."""
    name = call_sign = ship_type = length = beam = None
    part_a, part_b = p.get("ReportA"), p.get("ReportB")
    if isinstance(part_a, dict) and part_a.get("Valid"):
        name = _clean(part_a.get("Name"))
    if isinstance(part_b, dict) and part_b.get("Valid"):
        call_sign = _clean(part_b.get("CallSign"))
        raw_type = part_b.get("ShipType")
        ship_type = raw_type if isinstance(raw_type, int) else None
        length, beam = _dimensions(part_b.get("Dimension"))
    return BroadcastFacts(name=name, call_sign=call_sign, ship_type=ship_type,
                          length_m=length, beam_m=beam)


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
        facts = None
        if msg.message_type == POSITION_REPORT:
            self._apply_position(vessel, msg)
        elif msg.message_type in (SHIP_STATIC_DATA, STATIC_DATA_REPORT):
            # raw_static, not static_resolved: a visit seeded from the vessel
            # store is already resolved, and its first over-the-air message
            # still has to reach the visit log straight away.
            first, category = vessel.raw_static is None, vessel.category
            if msg.message_type == SHIP_STATIC_DATA:
                facts = _ship_static_facts(msg.payload)
                self._apply_voyage(vessel, msg.payload)
            else:
                facts = _class_b_facts(msg.payload)
            self._apply_static(vessel, facts, msg.payload)
            # Class B states its type in a later frame than its name, so a
            # changed category counts the same as the first message.
            static_resolved_now = first or vessel.category is not category

        # A position outside the box means the vessel has left; do not wait
        # out SHIP_TIMEOUT_SECONDS.
        if (msg.lat is not None and msg.lon is not None
                and not self._settings.bbox.contains(msg.lat, msg.lon)):
            self._depart(vessel, msg.received_at, "left_bbox")
            return RegistryChange(vessel, entered=entered, departed=True,
                                  static_resolved_now=static_resolved_now,
                                  facts=facts)

        return RegistryChange(vessel, entered=entered,
                              static_resolved_now=static_resolved_now,
                              facts=facts)

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

    def remember(self, mmsi: int, facts: BroadcastFacts) -> bool:
        """Seed a live vessel from the vessel store. True if it changed.

        Ship type, call sign and dimensions do not change between visits, so
        remembered facts are as good as a live message - except raw_static,
        which stays empty so a seeded visit is distinguishable in the log
        from one that resolved over the air. Live static data always wins:
        only fields this visit has not heard are filled, and the remembered
        type goes through the identity precedence like any other source.
        """
        vessel = self._live.get(mmsi)
        if vessel is None:
            return False

        def seedable() -> tuple:
            return (vessel.name, vessel.call_sign, vessel.imo, vessel.length_m,
                    vessel.beam_m, vessel.category, vessel.static_resolved)

        before = seedable()
        if vessel.static_resolved:
            vessel.name = vessel.name or facts.name
        else:
            # Until static data arrives the name is only the feed's metadata
            # label, which the vessel's own stated name outranks.
            vessel.name = facts.name or vessel.name
        vessel.call_sign = vessel.call_sign or facts.call_sign
        vessel.imo = vessel.imo or facts.imo
        vessel.length_m = vessel.length_m or facts.length_m
        vessel.beam_m = vessel.beam_m or facts.beam_m
        vessel.remembered_type = facts.ship_type
        # A store row can hold a name and nothing else (Class B part A). That
        # is not enough to stop waiting for static data.
        if facts.ship_type is not None:
            vessel.static_resolved = True
        self._resolve_identity(vessel)
        return before != seedable()

    def _apply_static(self, vessel: Vessel, facts: BroadcastFacts,
                      payload: dict[str, Any]) -> None:
        # Real AIS static data can arrive as partial retransmissions (Class B
        # splits it across separate frames; any decoder can also just drop a
        # field). A later message missing a field must never regress state a
        # prior message already resolved - keep vessel.<field> whenever the
        # new value is absent or malformed. This matters most for
        # category/priority: a TANKER silently reverting to UNKNOWN would let
        # it lose its display slot. A vaguer type is the same absence, spelled out.
        vessel.name = facts.name or vessel.name
        vessel.call_sign = facts.call_sign or vessel.call_sign
        vessel.imo = facts.imo or vessel.imo
        vessel.length_m = facts.length_m or vessel.length_m
        vessel.beam_m = facts.beam_m or vessel.beam_m
        if (facts.ship_type is not None and type_strength(facts.ship_type)
                >= type_strength(vessel.broadcast_type)):
            vessel.broadcast_type = facts.ship_type
        self._resolve_identity(vessel)

        vessel.static_resolved = True
        # raw_static intentionally always overwrites: it exists to show what
        # the most recent message actually contained, partial or not.
        vessel.raw_static = dict(payload)

    @staticmethod
    def _apply_voyage(vessel: Vessel, p: dict[str, Any]) -> None:
        """The parts of ShipStaticData that describe the voyage, not the
        vessel, and so are never remembered between visits."""
        vessel.destination = _clean(p.get("Destination")) or vessel.destination
        draught = _number(p.get("MaximumStaticDraught"))
        if draught is not None:
            vessel.draught_m = draught
        eta = _format_eta(p.get("Eta"))
        if eta is not None:
            vessel.eta = eta

    @staticmethod
    def _resolve_identity(vessel: Vessel) -> None:
        identity = resolve_identity(
            mmsi=vessel.mmsi, name=vessel.name,
            broadcast_type=vessel.broadcast_type,
            remembered_type=vessel.remembered_type)
        vessel.ship_type = identity.ship_type
        vessel.category = identity.category
        vessel.category_source = identity.category_source
        vessel.priority = priority_for(identity.category)

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
