import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.models import BroadcastFacts, Vessel
from ship_observer.storage import SCHEMA, Storage

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)

FULL = BroadcastFacts(name="POLAR RESOLUTE", call_sign="WCX8834", imo=9312345,
                      ship_type=80, length_m=240.0, beam_m=32.0)


@pytest.fixture
async def store(tmp_path):
    s = Storage(tmp_path / "test.db")
    await s.open()
    yield s
    await s.close()


async def test_a_vessel_nobody_has_described_is_not_remembered(store):
    assert await store.remembered_broadcast(366123456) is None


async def test_broadcast_facts_round_trip(store):
    await store.remember_broadcast(366123456, FULL, T0)
    assert await store.remembered_broadcast(366123456) == FULL


async def test_vessels_are_remembered_separately(store):
    await store.remember_broadcast(1, FULL, T0)
    await store.remember_broadcast(2, BroadcastFacts(ship_type=52), T0)
    assert await store.remembered_broadcast(1) == FULL
    assert await store.remembered_broadcast(2) == BroadcastFacts(ship_type=52)


async def test_a_later_partial_message_never_erases_a_known_field(store):
    await store.remember_broadcast(1, FULL, T0)
    await store.remember_broadcast(1, BroadcastFacts(name="POLAR RESOLUTE"),
                                   T0 + timedelta(hours=1))
    assert await store.remembered_broadcast(1) == FULL


async def test_a_later_message_updates_the_fields_it_carries(store):
    await store.remember_broadcast(1, FULL, T0)
    await store.remember_broadcast(
        1, BroadcastFacts(name="POLAR ENDEAVOUR", ship_type=81),
        T0 + timedelta(hours=1))
    remembered = await store.remembered_broadcast(1)
    assert remembered.name == "POLAR ENDEAVOUR"
    assert remembered.ship_type == 81
    assert remembered.call_sign == "WCX8834"


async def test_type_0_never_overwrites_a_known_type(store):
    await store.remember_broadcast(1, FULL, T0)
    await store.remember_broadcast(1, BroadcastFacts(ship_type=0),
                                   T0 + timedelta(hours=1))
    assert (await store.remembered_broadcast(1)).ship_type == 80


async def test_a_vague_type_never_overwrites_a_specific_one(store):
    """ADR-0001: specific beats vague. A cargo ship that one day calls itself
    "other" (the 90s codes) must not be remembered as other for good."""
    await store.remember_broadcast(1, FULL, T0)
    await store.remember_broadcast(1, BroadcastFacts(ship_type=90),
                                   T0 + timedelta(hours=1))
    assert (await store.remembered_broadcast(1)).ship_type == 80


async def test_a_stated_other_replaces_not_available(store):
    await store.remember_broadcast(1, BroadcastFacts(ship_type=0), T0)
    await store.remember_broadcast(1, BroadcastFacts(ship_type=90),
                                   T0 + timedelta(hours=1))
    assert (await store.remembered_broadcast(1)).ship_type == 90


async def test_type_0_is_remembered_when_nothing_better_is_known(store):
    await store.remember_broadcast(1, BroadcastFacts(ship_type=0), T0)
    assert (await store.remembered_broadcast(1)).ship_type == 0
    await store.remember_broadcast(1, BroadcastFacts(ship_type=70),
                                   T0 + timedelta(hours=1))
    assert (await store.remembered_broadcast(1)).ship_type == 70


async def test_an_empty_message_is_not_remembered(store):
    await store.remember_broadcast(1, BroadcastFacts(), T0)
    assert await store.remembered_broadcast(1) is None


async def test_the_store_outlives_the_visit_logs_retention(store):
    long_ago = datetime.now(timezone.utc) - timedelta(days=400)
    await store.begin_visit(Vessel(mmsi=1, entered_at=long_ago,
                                   last_seen=long_ago))
    await store.remember_broadcast(1, FULL, long_ago)

    ships, _events = await store.prune(ship_log_days=7, event_log_hours=48)

    assert ships == 1   # the visit log's retention is unchanged
    assert await store.remembered_broadcast(1) == FULL


async def test_the_store_is_created_on_an_existing_database(tmp_path):
    """Upgrading a running panel needs no manual step: a database written
    before the vessel store existed gains it on the next open()."""
    path = tmp_path / "old.db"
    before = SCHEMA.split("CREATE TABLE IF NOT EXISTS vessel ")[0]
    assert "ship_log" in before and before != SCHEMA
    conn = sqlite3.connect(path)
    conn.executescript(before)
    conn.execute("INSERT INTO ship_log (mmsi, entered_at, last_seen) "
                 "VALUES (1, ?, ?)", (T0.isoformat(), T0.isoformat()))
    conn.commit()
    conn.close()

    s = Storage(path)
    await s.open()
    try:
        await s.remember_broadcast(1, FULL, T0)
        assert await s.remembered_broadcast(1) == FULL
        assert len(await s.query_ships()) == 1
    finally:
        await s.close()


async def test_an_upgraded_database_remembers_what_its_visit_log_already_knew(tmp_path):
    """Before the vessel store, a regular was seeded from its last visit that
    heard static data. Upgrading must not turn those regulars back into
    question marks until they happen to rebroadcast."""
    path = tmp_path / "old.db"
    s = Storage(path)
    await s.open()
    day = timedelta(days=1)
    heard = dict(static_resolved=True, raw_static={"Type": 60})
    await s.begin_visit(Vessel(mmsi=1, entered_at=T0, last_seen=T0,
                               name="OLD NAME", ship_type=60, **heard))
    await s.begin_visit(Vessel(mmsi=1, entered_at=T0 + day, last_seen=T0 + day,
                               name="SWIFTSURE", call_sign="WDF123",
                               ship_type=60, length_m=0.0, **heard))
    # A seeded visit (no payload) and an unresolved one teach the store nothing.
    await s.begin_visit(Vessel(mmsi=2, entered_at=T0, last_seen=T0,
                               ship_type=70, static_resolved=True))
    await s.begin_visit(Vessel(mmsi=3, entered_at=T0, last_seen=T0))
    await s._execute("DROP TABLE vessel")
    await s.close()

    s = Storage(path)
    await s.open()
    try:
        assert await s.remembered_broadcast(1) == BroadcastFacts(
            name="SWIFTSURE", call_sign="WDF123", ship_type=60)
        assert await s.remembered_broadcast(2) is None
        assert await s.remembered_broadcast(3) is None
    finally:
        await s.close()


async def test_the_backfill_never_overwrites_what_the_store_has_learned(tmp_path):
    path = tmp_path / "t.db"
    s = Storage(path)
    await s.open()
    await s.begin_visit(Vessel(mmsi=1, entered_at=T0, last_seen=T0,
                               ship_type=60, static_resolved=True,
                               raw_static={"Type": 60}))
    await s.remember_broadcast(1, BroadcastFacts(ship_type=80), T0)
    await s.close()

    s = Storage(path)
    await s.open()
    try:
        assert (await s.remembered_broadcast(1)).ship_type == 80
    finally:
        await s.close()
