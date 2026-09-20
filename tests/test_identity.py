import pytest

from ship_observer.identity import resolve_identity, type_strength
from ship_observer.models import CategorySource, ShipCategory

BROADCAST = CategorySource.BROADCAST
REMEMBERED = CategorySource.REMEMBERED

# ADR-0001, with only the broadcast sources to merge so far: a specific type
# beats a vague one (0 and the 90s "other" codes), this visit's broadcast
# beats the remembered one at equal strength, and 0 is never an answer.
# Every row is (broadcast type, remembered type) -> (category, source, code).
PRECEDENCE = [
    # nothing known
    (None, None, ShipCategory.UNKNOWN, None, None),
    (0, None, ShipCategory.UNKNOWN, None, 0),
    (None, 0, ShipCategory.UNKNOWN, None, 0),
    (0, 0, ShipCategory.UNKNOWN, None, 0),
    # one source
    (70, None, ShipCategory.CARGO, BROADCAST, 70),
    (None, 70, ShipCategory.CARGO, REMEMBERED, 70),
    (90, None, ShipCategory.OTHER, BROADCAST, 90),
    (None, 99, ShipCategory.OTHER, REMEMBERED, 99),
    # a code with no category of its own is still a specific answer
    (50, None, ShipCategory.OTHER, BROADCAST, 50),
    # both specific: this visit's word wins
    (80, 70, ShipCategory.TANKER, BROADCAST, 80),
    # specific beats vague, whichever side it is on
    (0, 70, ShipCategory.CARGO, REMEMBERED, 70),
    (90, 70, ShipCategory.CARGO, REMEMBERED, 70),
    (52, 90, ShipCategory.TUG, BROADCAST, 52),
    (52, 0, ShipCategory.TUG, BROADCAST, 52),
    (50, 99, ShipCategory.OTHER, BROADCAST, 50),
    # both vague: a stated "other" beats "not available"
    (0, 90, ShipCategory.OTHER, REMEMBERED, 90),
    (90, 0, ShipCategory.OTHER, BROADCAST, 90),
    (91, 95, ShipCategory.OTHER, BROADCAST, 91),
]


@pytest.mark.parametrize("broadcast,remembered,category,source,code", PRECEDENCE)
def test_precedence(broadcast, remembered, category, source, code):
    identity = resolve_identity(mmsi=366123456, name="POLAR RESOLUTE",
                                broadcast_type=broadcast,
                                remembered_type=remembered)
    assert identity.category is category
    assert identity.category_source is source
    assert identity.ship_type == code


def test_source_values_are_the_api_identifiers():
    assert BROADCAST.value == "broadcast"
    assert REMEMBERED.value == "remembered"


@pytest.mark.parametrize("weaker,stronger", [
    (None, 0), (0, 90), (99, 50), (90, 70), (None, 70),
])
def test_type_strength_orders_silence_not_available_vague_specific(weaker, stronger):
    assert type_strength(weaker) < type_strength(stronger)


def test_type_strength_ties():
    assert type_strength(90) == type_strength(99)
    assert type_strength(70) == type_strength(52)
