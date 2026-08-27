import json
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.config import Settings
from ship_observer.replay import ReplayClient, read_session, replay

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)


def write_session(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def record(mmsi, offset_s, message_type="PositionReport", **payload):
    return {
        "received_at": (T0 + timedelta(seconds=offset_s)).isoformat(),
        "message_type": message_type,
        "mmsi": mmsi,
        "meta_name": "TEST SHIP",
        "lat": 47.88, "lon": -122.41,
        "payload": payload or {"Sog": 10.0},
    }


def test_read_session_parses_jsonl(tmp_path):
    path = write_session(tmp_path / "s.jsonl", [record(1, 0), record(2, 5)])
    messages = list(read_session(path))
    assert [m.mmsi for m in messages] == [1, 2]
    assert messages[0].received_at == T0
    assert messages[0].message_type == "PositionReport"


def test_read_session_skips_corrupt_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(record(1, 0)) + "\nnot json\n{}\n"
                    + json.dumps(record(2, 1)) + "\n")
    assert [m.mmsi for m in read_session(path)] == [1, 2]


def test_read_session_on_an_empty_file(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text("")
    assert list(read_session(path)) == []


async def test_replay_client_yields_then_stops(tmp_path):
    path = write_session(tmp_path / "s.jsonl", [record(1, 0), record(1, 5)])
    client = ReplayClient(list(read_session(path)), speed=0.0)
    messages = [m async for m in client.stream()]
    assert len(messages) == 2
    assert client.last_message_at == T0 + timedelta(seconds=5)


async def test_replay_populates_the_ship_log(tmp_path):
    path = write_session(tmp_path / "s.jsonl", [
        record(1, 0),
        record(1, 30, "ShipStaticData", Name="EVER GIVEN", CallSign="H3RC",
               Destination="SEATTLE", Type=70,
               Dimension={"A": 300, "B": 100, "C": 30, "D": 30}),
        record(2, 60),
    ])
    settings = Settings.from_env({
        "AIS_STREAM_API_KEY": "k",
        "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
        "DB_PATH": str(tmp_path / "replay.db"),
        "DISPLAY_DRIVER": "null",
    })

    result = await replay(path, settings, speed=0.0)

    assert result["messages"] == 3
    assert result["visits"] == 2
    assert result["by_category"]["cargo"] == 1


async def test_replay_never_touches_the_network(tmp_path, monkeypatch):
    """A replay that dialled AISStream would be worse than useless."""
    import websockets

    def explode(*args, **kwargs):
        raise AssertionError("replay must not open a websocket")

    monkeypatch.setattr(websockets, "connect", explode)
    path = write_session(tmp_path / "s.jsonl", [record(1, 0)])
    settings = Settings.from_env({
        "AIS_STREAM_API_KEY": "k",
        "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
        "DB_PATH": str(tmp_path / "r.db"),
        "DISPLAY_DRIVER": "null",
    })
    await replay(path, settings, speed=0.0)
