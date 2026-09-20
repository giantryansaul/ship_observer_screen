from __future__ import annotations

from dataclasses import dataclass

from .models import CategorySource, ShipCategory
from .shiptypes import classify


@dataclass(frozen=True)
class VesselIdentity:
    category: ShipCategory = ShipCategory.UNKNOWN
    category_source: CategorySource | None = None
    # The AIS type code behind the category: None when no source has said
    # anything, 0 when the only thing said was "not available".
    ship_type: int | None = None


def type_strength(ship_type: int | None) -> int:
    """How much an AIS type code really says, for ranking one against
    another: nothing heard < 0 ("not available") < the vague 90s "other"
    codes < a specific type. A stronger code is never displaced by a weaker
    one, here or in the vessel store."""
    if ship_type is None:
        return 0
    if ship_type == 0:
        return 1
    if 90 <= ship_type <= 99:
        return 2
    return 3


_NOT_AVAILABLE = type_strength(0)


def resolve_identity(*, mmsi: int, name: str | None,
                     broadcast_type: int | None,
                     remembered_type: int | None) -> VesselIdentity:
    """Merge every source's answer into one vessel identity (ADR-0001).

    Sources are stored untouched and the precedence is applied here, at read
    time: specific type before vague type, and within each, this visit's
    broadcast before the remembered one. Type 0 is never an answer. `mmsi`
    and `name` are not consulted yet; the flag, operator rules and overrides
    that key on them slot in behind this same interface.
    """
    answers = [(broadcast_type, CategorySource.BROADCAST),
               (remembered_type, CategorySource.REMEMBERED)]
    # max() keeps the first of equals, so the order of `answers` breaks ties.
    code, source = max(answers, key=lambda answer: type_strength(answer[0]))
    if type_strength(code) <= _NOT_AVAILABLE:
        return VesselIdentity(ship_type=code)
    return VesselIdentity(classify(code), source, code)
