from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.models import ShipCategory, Vessel
from ship_observer.storage import Storage, parse_window

NOW = datetime.now(timezone.utc)


@pytest.fixture
async def store(tmp_path):
    s = Storage(tmp_path / "t.db")
    await s.open()
    yield s
    await s.close()


async def add_visit(store, mmsi, entered, dwell_minutes=30,
                    category=ShipCategory.CARGO, length=200.0, resolved=True,
                    displayed=False):
    v = Vessel(mmsi=mmsi, entered_at=entered,
               last_seen=entered + timedelta(minutes=dwell_minutes),
               departed_at=entered + timedelta(minutes=dwell_minutes),
               depart_reason="timeout", name=f"SHIP {mmsi}",
               category=category, priority=30, length_m=length,
               static_resolved=resolved, displayed=displayed)
    v.log_id = await store.begin_visit(v)
    return v


@pytest.mark.parametrize("raw,seconds", [
    ("7d", 604800), ("48h", 172800), ("30m", 1800), ("90s", 90), ("1d", 86400),
])
def test_parse_window(raw, seconds):
    assert parse_window(raw) == seconds


@pytest.mark.parametrize("raw", ["", "7", "d7", "-1d", "7y", "abc"])
def test_parse_window_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_window(raw)


async def test_prune_respects_both_retention_windows(store):
    await add_visit(store, 1, NOW - timedelta(days=9))
    await add_visit(store, 2, NOW - timedelta(days=1))
    await store._execute(
        "INSERT INTO event_log (ts, level, category, message) VALUES (?,?,?,?)",
        ((NOW - timedelta(hours=72)).isoformat(), "INFO", "ws", "old"))
    await store.log_event("INFO", "ws", "recent")

    ships_deleted, events_deleted = await store.prune(ship_log_days=7,
                                                      event_log_hours=48)

    assert (ships_deleted, events_deleted) == (1, 1)
    assert [r["mmsi"] for r in await store._fetchall("SELECT mmsi FROM ship_log")] == [2]
    messages = [r["message"] for r in await store._fetchall("SELECT message FROM event_log")]
    assert messages == ["recent"]


async def test_prune_on_an_empty_database_is_safe(store):
    assert await store.prune(7, 48) == (0, 0)


async def test_query_ships_filters_and_orders_newest_first(store):
    await add_visit(store, 1, NOW - timedelta(hours=5), category=ShipCategory.CARGO)
    await add_visit(store, 2, NOW - timedelta(hours=1), category=ShipCategory.TANKER)
    await add_visit(store, 3, NOW - timedelta(hours=3), category=ShipCategory.CARGO)

    assert [r["mmsi"] for r in await store.query_ships()] == [2, 3, 1]
    assert [r["mmsi"] for r in await store.query_ships(category="cargo")] == [3, 1]
    assert [r["mmsi"] for r in await store.query_ships(
        since=NOW - timedelta(hours=4))] == [2, 3]
    assert len(await store.query_ships(limit=1)) == 1


async def test_query_ships_honors_until_and_unknown_category(store):
    await add_visit(store, 1, NOW - timedelta(hours=5), category=ShipCategory.CARGO)
    await add_visit(store, 2, NOW - timedelta(hours=3), category=ShipCategory.TANKER)
    await add_visit(store, 3, NOW - timedelta(hours=1), category=ShipCategory.CARGO)

    # "until" bounds by entered_at and still returns newest-first.
    rows = await store.query_ships(until=NOW - timedelta(hours=2))
    assert [r["mmsi"] for r in rows] == [2, 1]

    # Unknown category is an exact-match filter and returns no rows.
    assert await store.query_ships(category="not-a-real-category") == []


async def test_query_events_filters_by_level_and_category(store):
    await store.log_event("INFO", "ws", "connected")
    await store.log_event("WARN", "ws", "dropped")
    await store.log_event("ERROR", "display", "render failed")

    assert len(await store.query_events()) == 3
    # level is a minimum severity: WARN also returns ERROR (newest first).
    assert [r["message"] for r in await store.query_events(level="WARN")] == [
        "render failed", "dropped"]
    assert [r["message"] for r in await store.query_events(category="display")] == [
        "render failed"]


async def test_query_events_level_filter_is_a_minimum_severity(store):
    await store.log_event("DEBUG", "ws", "d")
    await store.log_event("INFO", "ws", "i")
    await store.log_event("ERROR", "ws", "e")
    messages = {r["message"] for r in await store.query_events(level="INFO")}
    assert messages == {"i", "e"}


async def test_query_events_unknown_level_is_ignored_but_category_still_filters(store):
    await store.log_event("INFO", "ws", "connected")
    await store.log_event("WARN", "ws", "dropped")
    await store.log_event("ERROR", "display", "render failed")

    # Unknown levels are ignored (same as no level filter).
    rows = await store.query_events(level="NOPE")
    assert [r["message"] for r in rows] == ["render failed", "dropped", "connected"]

    # Unknown categories remain exact-match filters and return no rows.
    assert await store.query_events(category="not-a-real-category") == []


async def test_traffic_summary_counts_by_category(store):
    await add_visit(store, 1, NOW - timedelta(hours=2), category=ShipCategory.CARGO)
    await add_visit(store, 2, NOW - timedelta(hours=3), category=ShipCategory.CARGO)
    await add_visit(store, 3, NOW - timedelta(hours=4), category=ShipCategory.SAILING,
                    length=12.0)
    await add_visit(store, 4, NOW - timedelta(days=30), category=ShipCategory.TANKER)

    summary = await store.traffic_summary(window_seconds=86400)

    assert summary["total_visits"] == 3, "the 30-day-old visit is outside the window"
    by_cat = {row["category"]: row["visits"] for row in summary["by_category"]}
    assert by_cat == {"cargo": 2, "sailing": 1}


async def test_traffic_summary_length_histogram_buckets_by_ten_metres(store):
    await add_visit(store, 1, NOW - timedelta(hours=1), length=12.0)
    await add_visit(store, 2, NOW - timedelta(hours=1), length=18.0)
    await add_visit(store, 3, NOW - timedelta(hours=1), length=205.0)

    buckets = {row["bucket_m"]: row["visits"]
               for row in (await store.traffic_summary(86400))["length_histogram"]}

    assert buckets[10] == 2
    assert buckets[200] == 1


async def test_traffic_summary_reports_dwell_and_unresolved_counts(store):
    await add_visit(store, 1, NOW - timedelta(hours=2), dwell_minutes=10)
    await add_visit(store, 2, NOW - timedelta(hours=2), dwell_minutes=30)
    await add_visit(store, 3, NOW - timedelta(hours=2), dwell_minutes=50,
                    resolved=False, length=None)

    summary = await store.traffic_summary(86400)

    dwell = {row["category"]: row for row in summary["dwell_by_category"]}
    assert dwell["cargo"]["median_minutes"] == pytest.approx(30.0, abs=0.1)
    assert summary["unresolved_static_visits"] == 1


async def test_traffic_summary_reports_visits_per_hour(store):
    await add_visit(store, 1, NOW - timedelta(hours=1))
    await add_visit(store, 2, NOW - timedelta(hours=1))
    hours = {row["hour"]: row["visits"] for row in
             (await store.traffic_summary(86400))["visits_by_hour"]}
    assert sum(hours.values()) == 2


async def test_traffic_summary_on_empty_database_returns_zeros(store):
    summary = await store.traffic_summary(86400)
    assert summary["total_visits"] == 0
    assert summary["by_category"] == []
    assert summary["length_histogram"] == []
    assert summary["unresolved_static_visits"] == 0


async def test_settings_round_trip_and_overwrite(store):
    assert await store.get_setting("display_mode") is None
    await store.set_setting("display_mode", "two_ship")
    assert await store.get_setting("display_mode") == "two_ship"
    # A second write to the same key replaces it rather than erroring on the
    # primary key or leaving two rows to disagree.
    await store.set_setting("display_mode", "one_ship")
    assert await store.get_setting("display_mode") == "one_ship"
    rows = await store._fetchall("SELECT COUNT(*) AS n FROM app_settings")
    assert rows[0]["n"] == 1


async def test_settings_keys_are_independent(store):
    await store.set_setting("a", "1")
    await store.set_setting("b", "2")
    assert (await store.get_setting("a"), await store.get_setting("b")) == ("1", "2")
