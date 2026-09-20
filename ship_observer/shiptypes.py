from __future__ import annotations

from .models import ShipCategory

# Exact codes take precedence over ranges.
_EXACT: dict[int, ShipCategory] = {
    30: ShipCategory.FISHING,
    31: ShipCategory.TUG,
    32: ShipCategory.TUG,
    35: ShipCategory.MILITARY,
    36: ShipCategory.SAILING,
    37: ShipCategory.PLEASURE,
    51: ShipCategory.PATROL,
    52: ShipCategory.TUG,
    55: ShipCategory.PATROL,
}

# (low, high) inclusive.
_RANGES: list[tuple[int, int, ShipCategory]] = [
    (60, 69, ShipCategory.PASSENGER),
    (70, 79, ShipCategory.CARGO),
    (80, 89, ShipCategory.TANKER),
]

CATEGORY_PRIORITY: dict[ShipCategory, int] = {
    # Tier 1
    ShipCategory.MILITARY: 40,
    ShipCategory.PASSENGER: 40,
    ShipCategory.PATROL: 40,
    # Tier 2
    ShipCategory.CARGO: 30,
    ShipCategory.TANKER: 30,
    # Tier 3
    ShipCategory.TUG: 20,
    ShipCategory.OTHER: 20,
    # Unresolved static data gets a provisional Tier 3 slot so a cargo ship
    # is not invisible for its first six minutes in the box.
    ShipCategory.UNKNOWN: 20,
    # Tier 4
    ShipCategory.FISHING: 10,
    ShipCategory.SAILING: 10,
    ShipCategory.PLEASURE: 10,
}


def classify(ship_type: int | None) -> ShipCategory:
    """Map an AIS ship-and-cargo type code to a display category.

    None means ShipStaticData has not arrived yet and 0 is the vessel
    broadcasting "not available": either way nobody has said what it is,
    which is materially different from a resolved-but-uninteresting type.
    """
    if ship_type is None or ship_type == 0:
        return ShipCategory.UNKNOWN
    if ship_type in _EXACT:
        return _EXACT[ship_type]
    for low, high, category in _RANGES:
        if low <= ship_type <= high:
            return category
    return ShipCategory.OTHER


def priority_for(category: ShipCategory) -> int:
    return CATEGORY_PRIORITY[category]


def parse_category_names(raw: str) -> frozenset[ShipCategory]:
    """Parse a comma-separated EXCLUDE_CATEGORIES value."""
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    valid = {c.value for c in ShipCategory}
    unknown = [n for n in names if n not in valid]
    if unknown:
        raise ValueError(
            f"Unknown ship categories: {', '.join(unknown)}. "
            f"Valid values: {', '.join(sorted(valid))}"
        )
    return frozenset(ShipCategory(n) for n in names)
