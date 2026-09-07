from datetime import datetime, timedelta, timezone

from ship_observer.config import Settings
from ship_observer.models import ShipCategory, Vessel
from ship_observer.selection import (RotationView, Slots, filtered_reason,
                                     is_eligible, select_rotation,
                                     select_slots)

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


# -- rotation (the 2- and 1-ship modes page through everything) -------------


def departed(mmsi, minutes, **kwargs):
    v = vessel(mmsi, minutes, **kwargs)
    v.departed_at = T0 + timedelta(minutes=minutes + 1)
    return v


def test_rotation_pages_through_every_eligible_live_vessel():
    """Unlike select_slots, nothing is dropped for want of a slot - the
    whole box is shown, a page at a time."""
    live = [vessel(i, i) for i in range(5)]
    view = select_rotation(live, [], settings(), page_size=2, page=0)
    assert view.pages == 3
    assert len(view.vessels) == 2
    assert view.from_history is False
    last = select_rotation(live, [], settings(), page_size=2, page=2)
    assert len(last.vessels) == 1


def test_rotation_pages_partition_the_whole_ordering():
    live = [vessel(i, i) for i in range(5)]
    seen = []
    for page in range(3):
        seen += [v.mmsi for v in
                 select_rotation(live, [], settings(), 2, page).vessels]
    assert sorted(seen) == [0, 1, 2, 3, 4]
    assert len(seen) == len(set(seen)), "no vessel may appear on two pages"


def test_rotation_uses_the_same_priority_ordering_as_select_slots():
    live = [
        vessel(1, 4, ShipCategory.SAILING, 10, 12.0),
        vessel(2, 3, ShipCategory.FISHING, 10, 18.0),
        vessel(3, 2, ShipCategory.CARGO, 30),
        vessel(4, 1, ShipCategory.PASSENGER, 40, 120.0),
        vessel(5, 0, ShipCategory.TANKER, 30),
    ]
    view = select_rotation(live, [], settings(), page_size=3, page=0)
    # Same winners select_slots(capacity=3) picks, in priority order.
    assert [v.mmsi for v in view.vessels] == [4, 3, 5]
    assert set(v.mmsi for v in view.vessels) == {
        v.mmsi for v in select_slots(live, [], settings(), capacity=3).live}


def test_rotation_without_priority_selection_is_pure_recency():
    live = [vessel(1, 0, ShipCategory.SAILING, 10, 12.0),
            vessel(2, 1, ShipCategory.PASSENGER, 40, 120.0)]
    view = select_rotation(live, [], settings(PRIORITY_SELECTION="false"), 2, 0)
    assert [v.mmsi for v in view.vessels] == [2, 1]


def test_rotation_normalizes_the_page_modulo_the_page_count():
    live = [vessel(i, i) for i in range(5)]
    assert select_rotation(live, [], settings(), 2, page=3).page == 0
    assert select_rotation(live, [], settings(), 2, page=7).page == 1
    assert select_rotation(live, [], settings(), 2, page=-1).page == 2


def test_rotation_falls_back_to_recently_departed_when_the_box_is_empty():
    history = [departed(1, 3), departed(2, 2), departed(3, 1)]
    view = select_rotation([], history, settings(DISPLAY_HISTORY="true"), 2, 0)
    assert view.from_history is True
    assert [v.mmsi for v in view.vessels] == [1, 2], "departed order is preserved"
    assert view.pages == 2


def test_rotation_prefers_live_vessels_over_history():
    view = select_rotation([vessel(1, 0)], [departed(2, 0)],
                           settings(DISPLAY_HISTORY="true"), 2, 0)
    assert view.from_history is False
    assert [v.mmsi for v in view.vessels] == [1]


def test_rotation_history_fallback_respects_the_history_setting():
    view = select_rotation([], [departed(1, 0)],
                           settings(DISPLAY_HISTORY="false"), 2, 0)
    assert view.vessels == [] and view.pages == 0
    assert view.from_history is False


def test_rotation_of_an_empty_box_is_an_empty_view():
    view = select_rotation([], [], settings(DISPLAY_HISTORY="true"), 2, 0)
    assert view.vessels == []
    assert view.pages == 0 and view.page == 0
    assert view.from_history is False


def test_rotation_applies_the_same_display_gates():
    live = [vessel(1, 1, ShipCategory.SAILING, 10, 12.0), vessel(2, 0)]
    view = select_rotation(live, [], settings(MIN_LENGTH_METERS="50"), 2, 0)
    assert [v.mmsi for v in view.vessels] == [2]


def test_rotation_with_no_room_shows_nothing():
    """A non-positive page size must not divide by zero."""
    view = select_rotation([vessel(1, 0)], [], settings(), page_size=0, page=0)
    assert view.vessels == [] and view.pages == 0


def test_rotation_view_from_slots_uses_the_live_slots():
    slots = select_slots([vessel(1, 1), vessel(2, 0)], [], settings(), capacity=3)
    view = RotationView.from_slots(slots, page_size=1)
    assert [v.mmsi for v in view.vessels] == [1]
    assert view.pages == 1 and view.page == 0 and view.from_history is False


def test_rotation_view_from_slots_falls_back_to_history():
    slots = select_slots([], [departed(1, 0)], settings(DISPLAY_HISTORY="true"),
                         capacity=3)
    view = RotationView.from_slots(slots, page_size=2)
    assert [v.mmsi for v in view.vessels] == [1]
    assert view.from_history is True


def test_rotation_view_from_empty_slots_is_empty():
    view = RotationView.from_slots(Slots(), page_size=2)
    assert view.vessels == [] and view.pages == 0
