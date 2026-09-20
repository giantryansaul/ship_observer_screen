from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.ais_client import AisMessage
from ship_observer.config import Settings
from ship_observer.models import BroadcastFacts, CategorySource, ShipCategory
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


def test_partial_static_retransmission_preserves_existing_resolved_fields():
    r = VesselRegistry(settings())
    r.apply(position(1, T0, name="EVER GIVEN"))

    original = static(1, T0 + timedelta(seconds=10))
    vessel = r.apply(original).vessel
    assert vessel.category is ShipCategory.TANKER
    assert vessel.priority == 30
    assert vessel.imo == 9312345
    assert vessel.length_m == pytest.approx(240.0)
    assert vessel.beam_m == pytest.approx(32.0)
    assert vessel.draught_m == pytest.approx(12.5)
    assert vessel.eta == "08-27 06:30"

    partial = AisMessage(
        message_type="ShipStaticData",
        mmsi=1,
        received_at=T0 + timedelta(seconds=20),
        meta_name="SHOULD NOT OVERRIDE",
        lat=IN_BOX[0],
        lon=IN_BOX[1],
        payload={"Name": "UPDATED NAME ONLY"},
    )
    updated = r.apply(partial).vessel

    # Name can update, but absent static fields must never regress.
    assert updated.name == "UPDATED NAME ONLY"
    assert updated.category is ShipCategory.TANKER
    assert updated.priority == 30
    assert updated.imo == 9312345
    assert updated.length_m == pytest.approx(240.0)
    assert updated.beam_m == pytest.approx(32.0)
    assert updated.draught_m == pytest.approx(12.5)
    assert updated.eta == "08-27 06:30"


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


def test_prune_orders_multiple_expirations_by_last_seen_not_insertion_order():
    """Insert 1, 2, 3 but make vessel 1 the LAST to have been heard from, so a
    naive dict-iteration return would disagree with the required last_seen order.
    """
    r = VesselRegistry(settings(SHIP_TIMEOUT_SECONDS="900"))
    r.apply(position(1, T0))                              # inserted first
    r.apply(position(2, T0 + timedelta(minutes=1)))        # inserted second
    r.apply(position(3, T0 + timedelta(minutes=2)))        # inserted third
    r.apply(position(1, T0 + timedelta(minutes=5)))        # update: 1's last_seen now latest

    expired = r.prune(now=T0 + timedelta(minutes=21))      # cutoff = T0+6min, clears all three

    assert [v.mmsi for v in expired] == [2, 3, 1], "prune() must return ascending last_seen order"
    assert [v.mmsi for v in r.departed()] == [1, 3, 2], "departed() is most-recently-processed first"


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


def class_b_static(mmsi, at, part_a=None, part_b=None, name="TEST SHIP"):
    """AISStream's StaticDataReport (AIS message 24). Each frame carries one
    part; the other report rides along zeroed and marked not valid."""
    report_a = {"Valid": part_a is not None, "Name": part_a or ""}
    report_b = {"Valid": part_b is not None, "ShipType": 0, "CallSign": "",
                "Dimension": {"A": 0, "B": 0, "C": 0, "D": 0},
                **(part_b or {})}
    return AisMessage(
        message_type="StaticDataReport", mmsi=mmsi, received_at=at,
        meta_name=name, lat=IN_BOX[0], lon=IN_BOX[1],
        payload={"MessageID": 24, "PartNumber": part_a is None,
                 "ReportA": report_a, "ReportB": report_b},
    )


def test_remembered_facts_resolve_a_vessel_that_has_sent_no_static_data():
    r = VesselRegistry(settings())
    v = r.apply(position(1, T0)).vessel

    seeded = r.remember(1, BroadcastFacts(
        name="SWIFTSURE", call_sign="WDF123", imo=9312345, ship_type=60,
        length_m=30.0, beam_m=8.0))

    assert seeded is True
    assert v.static_resolved is True
    assert v.category is ShipCategory.PASSENGER
    assert v.category_source is CategorySource.REMEMBERED
    assert v.ship_type == 60
    assert v.priority == 40
    assert (v.name, v.call_sign, v.imo) == ("SWIFTSURE", "WDF123", 9312345)
    assert (v.length_m, v.beam_m) == (30.0, 8.0)


def test_remembering_does_not_forge_a_live_raw_static_payload():
    """raw_static means "what actually arrived this visit". A seeded visit
    must stay distinguishable from one that resolved over the air."""
    r = VesselRegistry(settings())
    v = r.apply(position(1, T0)).vessel
    r.remember(1, BroadcastFacts(ship_type=60))
    assert v.raw_static is None


def test_remembering_never_overwrites_live_static_data():
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    r.apply(static(1, T0, ship_type=80))

    assert r.remember(1, BroadcastFacts(ship_type=60, name="OLD NAME")) is False
    v = r.live()[0]
    assert v.category is ShipCategory.TANKER
    assert v.category_source is CategorySource.BROADCAST
    assert v.name == "POLAR RESOLUTE"


def test_remembering_is_a_noop_for_a_vessel_not_in_the_box():
    r = VesselRegistry(settings())
    assert r.remember(42, BroadcastFacts(ship_type=60)) is False


def test_live_static_data_takes_over_from_a_remembered_category():
    r = VesselRegistry(settings())
    v = r.apply(position(1, T0)).vessel
    r.remember(1, BroadcastFacts(ship_type=60))

    r.apply(static(1, T0 + timedelta(minutes=1), ship_type=80))

    assert v.category is ShipCategory.TANKER
    assert v.category_source is CategorySource.BROADCAST
    assert v.raw_static is not None


def test_the_first_live_static_message_of_a_seeded_visit_is_reported():
    """The service writes the visit row at once on this flag; a seeded visit
    must not leave its over-the-air payload waiting on the write throttle."""
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    r.remember(1, BroadcastFacts(ship_type=60))

    first = r.apply(static(1, T0 + timedelta(minutes=1)))
    second = r.apply(static(1, T0 + timedelta(minutes=7)))

    assert first.static_resolved_now is True
    assert second.static_resolved_now is False


def test_a_remembered_name_alone_does_not_stop_the_wait_for_static_data():
    r = VesselRegistry(settings())
    v = r.apply(position(1, T0, name=None)).vessel

    assert r.remember(1, BroadcastFacts(name="WINDSONG")) is True

    assert v.name == "WINDSONG"
    assert v.static_resolved is False
    assert v.category is ShipCategory.UNKNOWN


def test_type_0_is_unknown_and_keeps_its_provisional_priority():
    r = VesselRegistry(settings())
    v = r.apply(static(1, T0, ship_type=0)).vessel
    assert v.static_resolved is True
    assert v.category is ShipCategory.UNKNOWN
    assert v.category_source is None
    assert v.priority == 20


def test_a_remembered_type_beats_a_live_type_0():
    r = VesselRegistry(settings())
    v = r.apply(static(1, T0, ship_type=0)).vessel

    assert r.remember(1, BroadcastFacts(ship_type=70)) is True

    assert v.category is ShipCategory.CARGO
    assert v.category_source is CategorySource.REMEMBERED
    assert v.ship_type == 70
    assert v.priority == 30


def test_a_later_type_0_never_regresses_a_type_stated_this_visit():
    r = VesselRegistry(settings())
    v = r.apply(static(1, T0, ship_type=70)).vessel
    r.apply(static(1, T0 + timedelta(minutes=6), ship_type=0))
    assert v.category is ShipCategory.CARGO
    assert v.category_source is CategorySource.BROADCAST


def test_a_later_vague_type_never_regresses_a_specific_one_this_visit():
    r = VesselRegistry(settings())
    v = r.apply(static(1, T0, ship_type=70)).vessel
    r.apply(static(1, T0 + timedelta(minutes=6), ship_type=90))
    assert v.category is ShipCategory.CARGO


def test_a_message_that_changes_the_category_is_reported_like_the_first():
    """Class B states its name and its type in separate frames; the frame
    that finally says what the vessel is has to reach the visit log at once,
    not wait out the write throttle."""
    r = VesselRegistry(settings())
    r.apply(class_b_static(1, T0, part_a="WINDSONG"))
    change = r.apply(class_b_static(1, T0 + timedelta(seconds=1),
                                    part_b={"ShipType": 36}))
    assert change.vessel.category is ShipCategory.SAILING
    assert change.static_resolved_now is True


def test_a_static_message_reports_the_facts_it_carried():
    r = VesselRegistry(settings())
    assert r.apply(position(1, T0)).facts is None

    change = r.apply(static(1, T0 + timedelta(minutes=1)))

    assert change.facts == BroadcastFacts(
        name="POLAR RESOLUTE", call_sign="WCX8834", imo=9312345, ship_type=80,
        length_m=240.0, beam_m=32.0)


def test_unset_dimensions_are_not_facts():
    """AIS sends all-zero dimensions for "not available"; remembering them
    would erase a length an earlier message had stated."""
    r = VesselRegistry(settings())
    change = r.apply(static(1, T0, a=0, b=0))
    assert change.facts.length_m is None


def test_class_b_part_a_names_the_vessel_and_nothing_else():
    r = VesselRegistry(settings())
    change = r.apply(class_b_static(1, T0, part_a="WINDSONG", name=None))

    assert change.facts == BroadcastFacts(name="WINDSONG")
    v = change.vessel
    assert v.name == "WINDSONG"
    # Part A's zeroed, not-valid ReportB must not read as "type 0, 0 m long".
    assert v.broadcast_type is None
    assert v.length_m is None
    assert v.category is ShipCategory.UNKNOWN


def test_class_b_part_b_feeds_call_sign_type_and_dimensions():
    r = VesselRegistry(settings())
    r.apply(class_b_static(1, T0, part_a="WINDSONG"))
    change = r.apply(class_b_static(1, T0 + timedelta(seconds=1), part_b={
        "ShipType": 36, "CallSign": "WDL4455",
        "Dimension": {"A": 8, "B": 4, "C": 2, "D": 2}}))

    assert change.facts == BroadcastFacts(call_sign="WDL4455", ship_type=36,
                                          length_m=12.0, beam_m=4.0)
    v = change.vessel
    assert v.name == "WINDSONG"
    assert v.call_sign == "WDL4455"
    assert v.category is ShipCategory.SAILING
    assert v.category_source is CategorySource.BROADCAST
    assert v.static_resolved is True
    assert v.raw_static["MessageID"] == 24
