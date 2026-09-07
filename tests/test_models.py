import pytest
from datetime import datetime, timezone
from ship_observer.models import BoundingBox, DisplayMode, ShipCategory, Vessel

BBOX_RAW = "-122.527428,47.859476,-122.323322,47.910359"


def test_parse_reads_longitude_first():
    b = BoundingBox.parse(BBOX_RAW)
    assert b.lon_min == pytest.approx(-122.527428)
    assert b.lat_min == pytest.approx(47.859476)
    assert b.lon_max == pytest.approx(-122.323322)
    assert b.lat_max == pytest.approx(47.910359)


def test_contains_uses_lat_lon_argument_order():
    b = BoundingBox.parse(BBOX_RAW)
    assert b.contains(47.88, -122.40) is True
    assert b.contains(47.80, -122.40) is False   # south of the box
    assert b.contains(47.88, -122.60) is False   # west of the box


def test_to_aisstream_emits_latitude_first_corner_pairs():
    b = BoundingBox.parse(BBOX_RAW)
    assert b.to_aisstream() == [[
        [pytest.approx(47.859476), pytest.approx(-122.527428)],
        [pytest.approx(47.910359), pytest.approx(-122.323322)],
    ]]


@pytest.mark.parametrize("raw", [
    "1,2,3",                       # too few
    "1,2,3,4,5",                   # too many
    "a,b,c,d",                     # not numbers
    "-122.5,47.9,-122.3,47.8",     # lat_min > lat_max
    "-122.3,47.8,-122.5,47.9",     # lon_min > lon_max
    "-122.5,91.0,-122.3,92.0",     # latitude out of range
    "-181.0,47.8,-122.3,47.9",     # longitude out of range
])
def test_parse_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        BoundingBox.parse(raw)


def test_vessel_defaults_to_unknown_category():
    now = datetime.now(timezone.utc)
    v = Vessel(mmsi=366123456, entered_at=now, last_seen=now)
    assert v.category is ShipCategory.UNKNOWN
    assert v.static_resolved is False
    assert v.position_count == 0


def test_display_mode_values_are_the_api_identifiers():
    assert [m.value for m in DisplayMode] == ["three_ship", "two_ship", "one_ship"]


@pytest.mark.parametrize("raw", ["three_ship", "two_ship", "one_ship"])
def test_display_mode_coerce_accepts_every_known_mode(raw):
    assert DisplayMode.coerce(raw) is DisplayMode(raw)


@pytest.mark.parametrize("raw", [None, "", "four_ship", "THREE_SHIP"])
def test_display_mode_coerce_falls_back_to_the_default(raw):
    """A missing or hand-edited setting must never stop the panel drawing."""
    assert DisplayMode.coerce(raw) is DisplayMode.THREE_SHIP
