from datetime import datetime, timedelta, timezone

from ship_observer.config import Settings
from ship_observer.models import ShipCategory, Vessel
from ship_observer.selection import filtered_reason, is_eligible, select_slots

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)
MINIMAL = {"AIS_STREAM_API_KEY": "k",
           "BBOX": "-122.527428,47.859476,-122.323322,47.910359"}


def settings(**overrides):
    return Settings.from_env({**MINIMAL, **overrides})


def vessel(mmsi, minutes, category=ShipCategory.CARGO, priority=30,
           length=200.0, resolved=True):
    at = T0 + timedelta(minutes=minutes)
    return Vessel(mmsi=mmsi, entered_at=at, last_seen=at, name=f"SHIP {mmsi}",
                  category=category, priority=priority, length_m=length,
                  static_resolved=resolved)


def test_fewer_than_capacity_is_pure_recency():
    """With <= MAX_SHIPS vessels, behaviour matches the original spec exactly."""
    vessels = [vessel(1, 0, ShipCategory.SAILING, 10, 12.0),
               vessel(2, 1, ShipCategory.CARGO, 30)]
    slots = select_slots(vessels, [], settings(), capacity=3)
    assert [v.mmsi for v in slots.live] == [2, 1]  # newest first
    assert slots.history == []
    assert slots.show_divider is False


def test_priority_decides_which_vessels_get_slots():
    vessels = [
        vessel(1, 4, ShipCategory.SAILING, 10, 12.0),
        vessel(2, 3, ShipCategory.FISHING, 10, 18.0),
        vessel(3, 2, ShipCategory.CARGO, 30),
        vessel(4, 1, ShipCategory.PASSENGER, 40, 120.0),
        vessel(5, 0, ShipCategory.TANKER, 30),
    ]
    slots = select_slots(vessels, [], settings(), capacity=3)
    # Selected: passenger (40), then the two 30s. Ordered newest-first.
    assert [v.mmsi for v in slots.live] == [3, 4, 5]


def test_within_a_tier_the_newest_entrant_wins_the_slot():
    vessels = [vessel(1, 0, ShipCategory.CARGO, 30),
               vessel(2, 1, ShipCategory.CARGO, 30),
               vessel(3, 2, ShipCategory.CARGO, 30),
               vessel(4, 3, ShipCategory.CARGO, 30)]
    slots = select_slots(vessels, [], settings(), capacity=3)
    assert [v.mmsi for v in slots.live] == [4, 3, 2]


def test_priority_selection_disabled_reverts_to_pure_recency():
    vessels = [vessel(1, 3, ShipCategory.SAILING, 10, 12.0),
               vessel(2, 2, ShipCategory.SAILING, 10, 12.0),
               vessel(3, 1, ShipCategory.SAILING, 10, 12.0),
               vessel(4, 0, ShipCategory.PASSENGER, 40, 120.0)]
    slots = select_slots(vessels, [], settings(PRIORITY_SELECTION="false"), capacity=3)
    assert [v.mmsi for v in slots.live] == [1, 2, 3]


def test_min_length_gate_excludes_small_resolved_vessels():
    s = settings(MIN_LENGTH_METERS="50")
    assert is_eligible(vessel(1, 0, ShipCategory.SAILING, 10, 12.0), s) is False
    assert is_eligible(vessel(2, 0, ShipCategory.CARGO, 30, 200.0), s) is True


def test_unresolved_vessels_are_never_hard_gated():
    """ShipStaticData takes ~6 minutes; gating on it would blank real ships."""
    s = settings(MIN_LENGTH_METERS="50", EXCLUDE_CATEGORIES="unknown")
    unresolved = vessel(1, 0, ShipCategory.UNKNOWN, 20, length=None, resolved=False)
    assert is_eligible(unresolved, s) is True


def test_exclude_categories_gate():
    s = settings(EXCLUDE_CATEGORIES="fishing,sailing")
    assert is_eligible(vessel(1, 0, ShipCategory.FISHING, 10, 18.0), s) is False
    assert is_eligible(vessel(2, 0, ShipCategory.CARGO, 30), s) is True


def test_filtered_reason_matches_hard_gates():
    s = settings(MIN_LENGTH_METERS="50", EXCLUDE_CATEGORIES="fishing")

    unresolved = vessel(1, 0, ShipCategory.UNKNOWN, 20, length=None, resolved=False)
    too_short = vessel(2, 0, ShipCategory.CARGO, 30, length=11.0, resolved=True)
    excluded = vessel(3, 0, ShipCategory.FISHING, 10, length=99.0, resolved=True)
    allowed = vessel(4, 0, ShipCategory.CARGO, 30, length=120.0, resolved=True)

    assert filtered_reason(unresolved, s) is None
    assert filtered_reason(too_short, s) == "min_length:50.0m"
    assert filtered_reason(excluded, s) == "excluded_category:fishing"
    assert filtered_reason(allowed, s) is None
    assert is_eligible(unresolved, s) is True
    assert is_eligible(too_short, s) is False
    assert is_eligible(excluded, s) is False
    assert is_eligible(allowed, s) is True


def test_filtered_vessels_do_not_occupy_slots():
    vessels = [vessel(1, 2, ShipCategory.SAILING, 10, 12.0),
               vessel(2, 1, ShipCategory.FISHING, 10, 18.0),
               vessel(3, 0, ShipCategory.CARGO, 30)]
    slots = select_slots(vessels, [], settings(MIN_LENGTH_METERS="50"), capacity=3)
    assert [v.mmsi for v in slots.live] == [3]


def test_history_backfills_remaining_slots_when_enabled():
    live = [vessel(1, 5), vessel(2, 4)]
    departed = [vessel(3, 3), vessel(4, 2)]
    slots = select_slots(live, departed, settings(DISPLAY_HISTORY="true"), capacity=3)
    assert [v.mmsi for v in slots.live] == [1, 2]
    assert [v.mmsi for v in slots.history] == [3]
    assert slots.show_divider is True


def test_no_divider_when_live_ships_fill_every_slot():
    live = [vessel(1, 3), vessel(2, 2), vessel(3, 1)]
    slots = select_slots(live, [vessel(9, 0)], settings(DISPLAY_HISTORY="true"),
                         capacity=3)
    assert len(slots.live) == 3
    assert slots.history == []
    assert slots.show_divider is False


def test_all_history_when_the_box_is_empty():
    departed = [vessel(1, 3), vessel(2, 2), vessel(3, 1), vessel(4, 0)]
    slots = select_slots([], departed, settings(DISPLAY_HISTORY="true"), capacity=3)
    assert slots.live == []
    assert [v.mmsi for v in slots.history] == [1, 2, 3]
    assert slots.show_divider is True


def test_history_disabled_shows_nothing_extra():
    slots = select_slots([], [vessel(1, 0)], settings(DISPLAY_HISTORY="false"),
                         capacity=3)
    assert slots.live == [] and slots.history == []
    assert slots.show_divider is False


def test_capacity_clamps_below_max_ships():
    live = [vessel(i, i) for i in range(5)]
    slots = select_slots(live, [], settings(MAX_SHIPS="3"), capacity=2)
    assert len(slots.live) == 2


def test_history_is_filtered_too():
    departed = [vessel(1, 1, ShipCategory.SAILING, 10, 12.0), vessel(2, 0)]
    slots = select_slots([], departed,
                         settings(DISPLAY_HISTORY="true", MIN_LENGTH_METERS="50"),
                         capacity=3)
    assert [v.mmsi for v in slots.history] == [2]
