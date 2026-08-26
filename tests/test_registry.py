from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.ais_client import AisMessage
from ship_observer.config import Settings
from ship_observer.models import ShipCategory
from ship_observer.registry import VesselRegistry

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)
MINIMAL = {
    "AIS_STREAM_API_KEY": "k",
    "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
}
IN_BOX = (47.88, -122.41)
OUT_OF_BOX = (47.70, -122.41)


def settings(**overrides):
    return Settings.from_env({**MINIMAL, **overrides})


def position(mmsi, at, lat=IN_BOX[0], lon=IN_BOX[1], sog=10.0, name="TEST SHIP"):
    return AisMessage(
        message_type="PositionReport", mmsi=mmsi, received_at=at, meta_name=name,
        lat=lat, lon=lon,
        payload={"Sog": sog, "Cog": 180.0, "TrueHeading": 179, "NavigationalStatus": 0},
    )


def static(mmsi, at, ship_type=80, name="POLAR RESOLUTE", call_sign="WCX8834",
           destination="CHERRY PT", a=180, b=60):
    return AisMessage(
        message_type="ShipStaticData", mmsi=mmsi, received_at=at, meta_name=name,
        lat=IN_BOX[0], lon=IN_BOX[1],
        payload={"Name": name, "CallSign": call_sign, "Destination": destination,
                 "Type": ship_type, "ImoNumber": 9312345,
                 "Dimension": {"A": a, "B": b, "C": 16, "D": 16},
                 "MaximumStaticDraught": 12.5,
                 "Eta": {"Month": 8, "Day": 27, "Hour": 6, "Minute": 30}},
    )


def test_first_message_enters_the_vessel():
    r = VesselRegistry(settings())
    change = r.apply(position(1, T0))
    assert change.entered is True
    assert change.vessel.mmsi == 1
    assert change.vessel.entered_at == T0
    assert len(r.live()) == 1


def test_meta_name_gives_a_provisional_name_before_static_data_arrives():
    """Otherwise a ship is anonymous for its first six minutes."""
    r = VesselRegistry(settings())
    v = r.apply(position(1, T0, name="EVER GIVEN")).vessel
    assert v.name == "EVER GIVEN"
    assert v.static_resolved is False
    assert v.category is ShipCategory.UNKNOWN
    assert v.priority == 20  # provisional tier 3


def test_static_data_merges_over_position_data():
    r = VesselRegistry(settings())
    r.apply(position(1, T0, name="EVER GIVEN"))
    change = r.apply(static(1, T0 + timedelta(seconds=30)))
    v = change.vessel
    assert change.static_resolved_now is True
    assert v.name == "POLAR RESOLUTE"
    assert v.call_sign == "WCX8834"
    assert v.destination == "CHERRY PT"
    assert v.category is ShipCategory.TANKER
    assert v.priority == 30
    assert v.length_m == pytest.approx(240.0)   # A + B
    assert v.beam_m == pytest.approx(32.0)      # C + D
    assert v.static_resolved is True
    # Position data survives the merge.
    assert v.last_lat == pytest.approx(47.88)


def test_second_static_message_does_not_report_resolved_again():
    r = VesselRegistry(settings())
    r.apply(static(1, T0))
    assert r.apply(static(1, T0 + timedelta(minutes=6))).static_resolved_now is False


def test_position_tracking_accumulates():
    r = VesselRegistry(settings())
    r.apply(position(1, T0, sog=8.0))
    r.apply(position(1, T0 + timedelta(seconds=10), sog=14.0))
    v = r.apply(position(1, T0 + timedelta(seconds=20), sog=11.0)).vessel
    assert v.position_count == 3
    assert v.max_sog == pytest.approx(14.0)
    assert v.last_seen == T0 + timedelta(seconds=20)
    assert v.first_lat == pytest.approx(47.88)


def test_position_outside_the_box_departs_immediately():
    """AISStream can emit edge positions; waiting 15 minutes would be wrong."""
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    change = r.apply(position(1, T0 + timedelta(seconds=10), lat=OUT_OF_BOX[0]))
    assert change.departed is True
    assert change.vessel.depart_reason == "left_bbox"
    assert r.live() == []
    assert [v.mmsi for v in r.departed()] == [1]


def test_prune_expires_silent_vessels():
    r = VesselRegistry(settings(SHIP_TIMEOUT_SECONDS="900"))
    r.apply(position(1, T0))
    r.apply(position(2, T0 + timedelta(minutes=10)))

    departed = r.prune(now=T0 + timedelta(minutes=16))

    assert [v.mmsi for v in departed] == [1]
    assert [v.mmsi for v in r.live()] == [2]
    assert departed[0].depart_reason == "timeout"
    assert departed[0].departed_at == T0 + timedelta(minutes=16)


def test_prune_is_exclusive_at_the_boundary():
    r = VesselRegistry(settings(SHIP_TIMEOUT_SECONDS="900"))
    r.apply(position(1, T0))
    assert r.prune(now=T0 + timedelta(seconds=900)) == []
    assert r.prune(now=T0 + timedelta(seconds=901)) != []


def test_live_is_ordered_newest_entrant_first():
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    r.apply(position(2, T0 + timedelta(minutes=1)))
    r.apply(position(3, T0 + timedelta(minutes=2)))
    r.apply(position(1, T0 + timedelta(minutes=3)))  # update, not a re-entry
    assert [v.mmsi for v in r.live()] == [3, 2, 1]


def test_reentry_after_departure_starts_a_new_visit():
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    r.prune(now=T0 + timedelta(minutes=20))

    change = r.apply(position(1, T0 + timedelta(minutes=30)))

    assert change.entered is True
    assert change.vessel.entered_at == T0 + timedelta(minutes=30)
    assert change.vessel.position_count == 1
    assert change.vessel.log_id is None, "new visit needs its own ship_log row"


def test_departed_deque_is_capped_and_newest_first():
    r = VesselRegistry(settings(MAX_SHIPS="3"))
    for mmsi in range(1, 6):
        r.apply(position(mmsi, T0 + timedelta(minutes=mmsi)))
        r.prune(now=T0 + timedelta(minutes=mmsi + 16))
    assert [v.mmsi for v in r.departed()] == [5, 4, 3]


def test_close_all_departs_everything_for_shutdown():
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    r.apply(position(2, T0))
    closed = r.close_all(now=T0 + timedelta(minutes=1))
    assert {v.mmsi for v in closed} == {1, 2}
    assert all(v.depart_reason == "shutdown" for v in closed)
    assert r.live() == []


def test_message_with_no_coordinates_still_updates_last_seen():
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    change = r.apply(static(1, T0 + timedelta(minutes=5)))
    assert change.departed is False
    assert change.vessel.last_seen == T0 + timedelta(minutes=5)
