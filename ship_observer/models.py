from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ShipCategory(str, Enum):
    """Display categories derived from the AIS ship-and-cargo type code."""

    PASSENGER = "passenger"
    CARGO = "cargo"
    TANKER = "tanker"
    TUG = "tug"
    FISHING = "fishing"
    SAILING = "sailing"
    PLEASURE = "pleasure"
    PATROL = "patrol"
    MILITARY = "military"
    OTHER = "other"
    UNKNOWN = "unknown"


class CategorySource(str, Enum):
    """Where a vessel's category came from. The values are also the API
    identifiers the debug page shows."""

    BROADCAST = "broadcast"      # static data received during this visit
    REMEMBERED = "remembered"    # the vessel store, from an earlier visit


class DisplayMode(str, Enum):
    """How many vessels the panel shows at once.

    Chosen from the web UI and persisted in app_settings; the values are
    also the API identifiers, so they must not be renamed casually.
    """

    THREE_SHIP = "three_ship"
    TWO_SHIP = "two_ship"
    ONE_SHIP = "one_ship"

    @classmethod
    def coerce(cls, raw: str | None) -> "DisplayMode":
        """A stored or user-supplied value, falling back to the default.

        A setting that is missing, empty or hand-edited to nonsense must
        never stop the panel drawing.
        """
        try:
            return cls(raw)
        except ValueError:
            return cls.THREE_SHIP


@dataclass(frozen=True)
class BoundingBox:
    """A geographic box. Field order matches the BBOX env var: longitude first."""

    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float

    @classmethod
    def parse(cls, raw: str) -> "BoundingBox":
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError(
                f"BBOX needs exactly 4 comma-separated values "
                f"(lon_min,lat_min,lon_max,lat_max), got {len(parts)}: {raw!r}"
            )
        try:
            lon_min, lat_min, lon_max, lat_max = (float(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"BBOX values must all be numbers: {raw!r}") from exc

        for name, lat in (("lat_min", lat_min), ("lat_max", lat_max)):
            if not -90.0 <= lat <= 90.0:
                raise ValueError(f"BBOX {name}={lat} is outside [-90, 90]")
        for name, lon in (("lon_min", lon_min), ("lon_max", lon_max)):
            if not -180.0 <= lon <= 180.0:
                raise ValueError(f"BBOX {name}={lon} is outside [-180, 180]")
        if lat_min >= lat_max:
            raise ValueError(
                f"BBOX lat_min ({lat_min}) must be less than lat_max ({lat_max}). "
                "Expected order is lon_min,lat_min,lon_max,lat_max."
            )
        if lon_min >= lon_max:
            raise ValueError(
                f"BBOX lon_min ({lon_min}) must be less than lon_max ({lon_max}). "
                "Expected order is lon_min,lat_min,lon_max,lat_max."
            )
        return cls(lon_min, lat_min, lon_max, lat_max)

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max

    def to_aisstream(self) -> list[list[list[float]]]:
        """AISStream wants [[[lat, lon], [lat, lon]]] — latitude first."""
        return [[[self.lat_min, self.lon_min], [self.lat_max, self.lon_max]]]


@dataclass(frozen=True)
class BroadcastFacts:
    """What one static data message said about the vessel itself.

    Only the facts that outlive a visit: destination, draught and ETA belong
    to the voyage. None means the message did not carry the field.
    """

    name: str | None = None
    call_sign: str | None = None
    imo: int | None = None
    ship_type: int | None = None
    length_m: float | None = None
    beam_m: float | None = None


@dataclass
class Vessel:
    """One visit by one vessel. A re-entry after departure is a new Vessel."""

    mmsi: int
    entered_at: datetime
    last_seen: datetime

    name: str | None = None
    call_sign: str | None = None
    destination: str | None = None

    # The merged vessel identity (see identity.py): the type code that won,
    # its category, and which source said so. The two inputs sit below.
    ship_type: int | None = None
    category: ShipCategory = ShipCategory.UNKNOWN
    category_source: CategorySource | None = None
    priority: int = 20
    broadcast_type: int | None = None     # stated during this visit
    remembered_type: int | None = None    # from the vessel store

    imo: int | None = None
    length_m: float | None = None
    beam_m: float | None = None
    draught_m: float | None = None
    eta: str | None = None

    first_lat: float | None = None
    first_lon: float | None = None
    last_lat: float | None = None
    last_lon: float | None = None
    max_sog: float | None = None
    last_cog: float | None = None
    last_heading: int | None = None
    nav_status: int | None = None

    position_count: int = 0
    static_resolved: bool = False
    displayed: bool = False

    departed_at: datetime | None = None
    depart_reason: str | None = None

    raw_static: dict[str, Any] | None = None
    raw_position: dict[str, Any] | None = None

    log_id: int | None = field(default=None, repr=False)

    @property
    def display_name(self) -> str:
        return self.name or f"MMSI {self.mmsi}"
