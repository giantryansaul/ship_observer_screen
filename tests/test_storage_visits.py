from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.models import ShipCategory, Vessel
from ship_observer.storage import Storage

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(tmp_path):
    s = Storage(tmp_path / "test.db")
    await s.open()
    yield s
    await s.close()


def make_vessel(mmsi=366123456, **overrides):
    base = dict(mmsi=mmsi, entered_at=T0, last_seen=T0, name="POLAR RESOLUTE",
                call_sign="WCX8834", destination="CHERRY PT", ship_type=80,
                category=ShipCategory.TANKER, priority=30, imo=9312345,
                length_m=240.0, beam_m=32.0, draught_m=12.5,
                first_lat=47.88, first_lon=-122.41,
                last_lat=47.89, last_lon=-122.40, max_sog=14.2,
                position_count=5, static_resolved=True)
    base.update(overrides)
    return Vessel(**base)


async def test_open_creates_schema_and_enables_wal(store):
    tables = await store._fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'")
    assert {"ship_log", "event_log"} <= {r["name"] for r in tables}
    mode = await store._fetchall("PRAGMA journal_mode")
    assert mode[0]["journal_mode"].lower() == "wal"


async def test_open_is_idempotent(tmp_path):
    for _ in range(2):
        s = Storage(tmp_path / "t.db")
        await s.open()
        await s.close()


async def test_open_creates_missing_parent_directories(tmp_path):
    s = Storage(tmp_path / "deep" / "nested" / "t.db")
    await s.open()
    await s.close()
    assert (tmp_path / "deep" / "nested" / "t.db").exists()


async def test_begin_visit_returns_a_row_id_and_persists_metadata(store):
    v = make_vessel()
    log_id = await store.begin_visit(v)
    assert isinstance(log_id, int)

    rows = await store._fetchall("SELECT * FROM ship_log")
    assert len(rows) == 1
    row = rows[0]
    assert row["mmsi"] == 366123456
    assert row["name"] == "POLAR RESOLUTE"
    assert row["call_sign"] == "WCX8834"
    assert row["destination"] == "CHERRY PT"
    assert row["category"] == "tanker"
    assert row["length_m"] == pytest.approx(240.0)
    assert row["departed_at"] is None
    assert row["entered_at"] == T0.isoformat()


async def test_update_visit_updates_in_place(store):
    v = make_vessel()
    v.log_id = await store.begin_visit(v)

    v.last_seen = T0 + timedelta(minutes=5)
    v.position_count = 42
    v.destination = "SEATTLE"
    v.displayed = True
    await store.update_visit(v)

    rows = await store._fetchall("SELECT * FROM ship_log")
    assert len(rows) == 1, "update must not insert a second row"
    assert rows[0]["position_count"] == 42
    assert rows[0]["destination"] == "SEATTLE"
    assert rows[0]["displayed"] == 1


async def test_update_visit_without_a_log_id_is_a_no_op(store):
    await store.update_visit(make_vessel())   # log_id is None
    assert await store._fetchall("SELECT * FROM ship_log") == []


async def test_end_visit_records_departure(store):
    v = make_vessel()
    v.log_id = await store.begin_visit(v)
    v.departed_at = T0 + timedelta(minutes=20)
    await store.end_visit(v, "timeout")

    row = (await store._fetchall("SELECT * FROM ship_log"))[0]
    assert row["departed_at"] == (T0 + timedelta(minutes=20)).isoformat()
    assert row["depart_reason"] == "timeout"


async def test_reentry_creates_a_second_row(store):
    first = make_vessel()
    first.log_id = await store.begin_visit(first)
    first.departed_at = T0 + timedelta(minutes=20)
    await store.end_visit(first, "timeout")

    second = make_vessel(entered_at=T0 + timedelta(minutes=30),
                         last_seen=T0 + timedelta(minutes=30))
    await store.begin_visit(second)

    rows = await store._fetchall("SELECT * FROM ship_log ORDER BY entered_at")
    assert len(rows) == 2
    assert rows[0]["departed_at"] is not None
    assert rows[1]["departed_at"] is None


async def test_raw_payloads_round_trip_as_json(store):
    v = make_vessel(raw_static={"Type": 80, "Name": "X"},
                    raw_position={"Sog": 12.4})
    await store.begin_visit(v)
    row = (await store._fetchall("SELECT * FROM ship_log"))[0]
    import json
    assert json.loads(row["raw_static"])["Type"] == 80
    assert json.loads(row["raw_position"])["Sog"] == pytest.approx(12.4)


async def test_unresolved_vessel_stores_null_type_and_unknown_category(store):
    v = make_vessel(ship_type=None, category=ShipCategory.UNKNOWN,
                    static_resolved=False, length_m=None)
    await store.begin_visit(v)
    row = (await store._fetchall("SELECT * FROM ship_log"))[0]
    assert row["ship_type"] is None
    assert row["category"] == "unknown"
    assert row["static_resolved"] == 0


async def test_log_event_persists_level_category_and_json_detail(store):
    await store.log_event("WARN", "ws", "disconnected", {"attempt": 3})
    row = (await store._fetchall("SELECT * FROM event_log"))[0]
    assert row["level"] == "WARN"
    assert row["category"] == "ws"
    assert row["message"] == "disconnected"
    import json
    assert json.loads(row["detail"])["attempt"] == 3


async def test_log_event_accepts_no_detail(store):
    await store.log_event("INFO", "display", "started")
    assert (await store._fetchall("SELECT * FROM event_log"))[0]["detail"] is None
