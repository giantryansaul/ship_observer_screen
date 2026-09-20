import pytest
from ship_observer.models import ShipCategory
from ship_observer.shiptypes import (
    CATEGORY_PRIORITY,
    classify,
    parse_category_names,
    priority_for,
)

# Written independently of the implementation so it catches a shifted range or a
# dropped exact-code entry, not just a type error.
EXPECTED_BY_CODE = {
    0: ShipCategory.UNKNOWN,                            # "not available"
    **{c: ShipCategory.OTHER for c in range(1, 30)},
    30: ShipCategory.FISHING,
    31: ShipCategory.TUG,
    32: ShipCategory.TUG,
    33: ShipCategory.OTHER,
    34: ShipCategory.OTHER,
    35: ShipCategory.MILITARY,
    36: ShipCategory.SAILING,
    37: ShipCategory.PLEASURE,
    **{c: ShipCategory.OTHER for c in range(38, 51)},   # includes 50, pilot
    51: ShipCategory.PATROL,
    52: ShipCategory.TUG,
    53: ShipCategory.OTHER,
    54: ShipCategory.OTHER,
    55: ShipCategory.PATROL,
    **{c: ShipCategory.OTHER for c in range(56, 60)},
    **{c: ShipCategory.PASSENGER for c in range(60, 70)},
    **{c: ShipCategory.CARGO for c in range(70, 80)},
    **{c: ShipCategory.TANKER for c in range(80, 90)},
    **{c: ShipCategory.OTHER for c in range(90, 100)},
}


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
    (0, ShipCategory.UNKNOWN), (1, ShipCategory.OTHER),
])
def test_classify_boundaries(code, expected):
    assert classify(code) is expected


def test_classify_none_is_unknown():
    """No ShipStaticData yet - distinct from OTHER, which is a resolved type."""
    assert classify(None) is ShipCategory.UNKNOWN


def test_classify_not_available_is_unknown():
    """Type 0 is a vessel declining to say what it is. OTHER is an answer;
    UNKNOWN is the absence of one, and keeps the vessel a lookup candidate."""
    assert classify(0) is ShipCategory.UNKNOWN


@pytest.mark.parametrize("code", [-1, 100, 999])
def test_classify_out_of_range_is_other(code):
    assert classify(code) is ShipCategory.OTHER


def test_every_code_0_to_99_maps_to_its_expected_category():
    assert len(EXPECTED_BY_CODE) == 100, "the expectation table must cover 0-99"
    assert {code: classify(code) for code in range(100)} == EXPECTED_BY_CODE


def test_every_code_0_to_99_lands_on_a_valid_tier():
    """Spec 13: every code maps to a category *and a tier*."""
    for code in range(100):
        assert priority_for(classify(code)) in {10, 20, 30, 40}


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
