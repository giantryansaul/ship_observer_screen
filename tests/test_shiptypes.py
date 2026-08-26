import pytest
from ship_observer.models import ShipCategory
from ship_observer.shiptypes import (
    CATEGORY_PRIORITY,
    classify,
    parse_category_names,
    priority_for,
)


@pytest.mark.parametrize("code,expected", [
    (30, ShipCategory.FISHING),
    (31, ShipCategory.TUG), (32, ShipCategory.TUG), (52, ShipCategory.TUG),
    (35, ShipCategory.MILITARY),
    (36, ShipCategory.SAILING),
    (37, ShipCategory.PLEASURE),
    (51, ShipCategory.PATROL), (55, ShipCategory.PATROL),
    (60, ShipCategory.PASSENGER), (64, ShipCategory.PASSENGER), (69, ShipCategory.PASSENGER),
    (70, ShipCategory.CARGO), (79, ShipCategory.CARGO),
    (80, ShipCategory.TANKER), (89, ShipCategory.TANKER),
    (50, ShipCategory.OTHER), (33, ShipCategory.OTHER), (90, ShipCategory.OTHER),
    (0, ShipCategory.OTHER),
])
def test_classify_boundaries(code, expected):
    assert classify(code) is expected


def test_classify_none_is_unknown():
    """No ShipStaticData yet - distinct from OTHER, which is a resolved type."""
    assert classify(None) is ShipCategory.UNKNOWN


@pytest.mark.parametrize("code", [-1, 100, 999])
def test_classify_out_of_range_is_other(code):
    assert classify(code) is ShipCategory.OTHER


def test_every_code_0_to_99_classifies():
    for code in range(100):
        assert isinstance(classify(code), ShipCategory)


def test_tier_ordering_matches_spec():
    p = priority_for
    tier1 = {ShipCategory.MILITARY, ShipCategory.PASSENGER, ShipCategory.PATROL}
    tier2 = {ShipCategory.CARGO, ShipCategory.TANKER}
    tier4 = {ShipCategory.FISHING, ShipCategory.SAILING, ShipCategory.PLEASURE}
    assert all(p(c) == 40 for c in tier1)
    assert all(p(c) == 30 for c in tier2)
    assert p(ShipCategory.TUG) == 20
    assert all(p(c) == 10 for c in tier4)


def test_unknown_gets_provisional_tier_3():
    """Unresolved vessels must outrank recreational traffic, not sit below it."""
    assert priority_for(ShipCategory.UNKNOWN) == 20
    assert priority_for(ShipCategory.UNKNOWN) > priority_for(ShipCategory.SAILING)


def test_every_category_has_a_priority():
    for category in ShipCategory:
        assert category in CATEGORY_PRIORITY


def test_parse_category_names():
    assert parse_category_names("fishing,sailing") == frozenset(
        {ShipCategory.FISHING, ShipCategory.SAILING}
    )
    assert parse_category_names(" FISHING , Sailing ") == frozenset(
        {ShipCategory.FISHING, ShipCategory.SAILING}
    )
    assert parse_category_names("") == frozenset()


def test_parse_category_names_rejects_unknown_name():
    with pytest.raises(ValueError, match="rowboat"):
        parse_category_names("fishing,rowboat")
