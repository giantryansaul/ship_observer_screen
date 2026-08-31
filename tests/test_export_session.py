"""Reconstructing a replayable session from the ship_log.

The first weekend ran with RECORD_RAW_PATH unset, so there is no raw JSONL to
replay. Each ship_log row still carries the last raw position and static
payload plus the visit's time span - enough to rebuild a session that drives
the pipeline the way the weekend did.
"""
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.export_session import export_session
from ship_observer.models import ShipCategory, Vessel
from ship_observer.replay import read_session
from ship_observer.storage import Storage

T0 = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(tmp_path):
    s = Storage(tmp_path / "ships.db")
    await s.open()
    yield s
    await s.close()


async def add_visit(store, mmsi, entered, dwell_minutes=25, resolved=True):
    v = Vessel(
        mmsi=mmsi, entered_at=entered,
        last_seen=entered + timedelta(minutes=dwell_minutes),
        departed_at=entered + timedelta(minutes=dwell_minutes),
        depart_reason="timeout", name=f"SHIP {mmsi}",
        category=ShipCategory.CARGO if resolved else ShipCategory.UNKNOWN,
        priority=30 if resolved else 20,
        first_lat=47.87, first_lon=-122.50, last_lat=47.90, last_lon=-122.35,
        static_resolved=resolved,
        raw_static={"Type": 70, "Name": f"SHIP {mmsi}"} if resolved else None,
        raw_position={"Sog": 12.0, "Cog": 90.0},
    )
    v.log_id = await store.begin_visit(v)
    return v


async def test_export_writes_a_chronological_replayable_session(store, tmp_path):
    await add_visit(store, 2, T0 + timedelta(hours=1))
    await add_visit(store, 1, T0)
    out = tmp_path / "session.jsonl"

    count = export_session(store._path, out)

    messages = list(read_session(out))
    assert count == len(messages) > 0
    times = [m.received_at for m in messages]
    assert times == sorted(times)
    assert {m.mmsi for m in messages} == {1, 2}


async def test_export_keeps_position_gaps_under_the_ship_timeout(store, tmp_path):
    """A 25-minute visit rebuilt as just two pings would replay as two visits:
    the pipeline times a vessel out after 900 silent seconds."""
    await add_visit(store, 1, T0, dwell_minutes=25)
    out = tmp_path / "session.jsonl"

    export_session(store._path, out)

    messages = [m for m in read_session(out) if m.message_type == "PositionReport"]
    assert messages[0].received_at == T0
    assert messages[-1].received_at == T0 + timedelta(minutes=25)
    gaps = [(b.received_at - a.received_at).total_seconds()
            for a, b in zip(messages, messages[1:])]
    assert max(gaps) < 900
    # Positions sweep from the recorded entry point to the recorded exit.
    assert (messages[0].lat, messages[0].lon) == (47.87, -122.50)
    assert (messages[-1].lat, messages[-1].lon) == (47.90, -122.35)


async def test_export_emits_static_only_for_visits_that_resolved_it(store, tmp_path):
    await add_visit(store, 1, T0, resolved=True)
    await add_visit(store, 2, T0, resolved=False)
    out = tmp_path / "session.jsonl"

    export_session(store._path, out)

    static = [m for m in read_session(out) if m.message_type == "ShipStaticData"]
    assert [m.mmsi for m in static] == [1]
    assert static[0].payload["Type"] == 70
