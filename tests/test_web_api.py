import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ship_observer.config import Settings
from ship_observer.models import ShipCategory, Vessel
from ship_observer.registry import VesselRegistry
from ship_observer.storage import Storage
from ship_observer.web.server import AppState, create_app

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)
MINIMAL = {"AIS_STREAM_API_KEY": "super-secret",
           "BBOX": "-122.527428,47.859476,-122.323322,47.910359"}


@pytest.fixture
async def client(tmp_path):
    settings = Settings.from_env({**MINIMAL, "DB_PATH": str(tmp_path / "t.db")})
    storage = Storage(settings.db_path)
    await storage.open()
    state = AppState(settings=settings,
                     registry=VesselRegistry(settings),
                     storage=storage)
    test_client = TestClient(TestServer(create_app(state)))
    await test_client.start_server()
    test_client.app_state = state
    yield test_client
    await test_client.close()
    await storage.close()


def vessel(mmsi=1, **overrides):
    base = dict(mmsi=mmsi, entered_at=T0, last_seen=T0, name="EVER GIVEN",
                call_sign="H3RC", destination="SEATTLE",
                category=ShipCategory.CARGO, priority=30, length_m=400.0,
                static_resolved=True)
    base.update(overrides)
    return Vessel(**base)


async def test_index_serves_html(client):
    response = await client.get("/")
    assert response.status == 200
    assert "text/html" in response.headers["Content-Type"]
    assert "canvas" in (await response.text()).lower()


async def test_healthz_reports_liveness_and_message_age(client):
    client.app_state.last_message_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    client.app_state.connected = True
    body = await (await client.get("/healthz")).json()
    assert body["ok"] is True
    assert body["connected"] is True
    assert 4 <= body["last_message_age_seconds"] <= 10


async def test_healthz_when_no_message_has_arrived(client):
    body = await (await client.get("/healthz")).json()
    assert body["last_message_age_seconds"] is None


async def test_state_never_exposes_the_api_key(client):
    text = await (await client.get("/api/state")).text()
    assert "super-secret" not in text
    assert json.loads(text)["config"]["ais_stream_api_key"] == "***redacted***"


async def test_state_surfaces_the_parsed_bounding_box(client):
    """A transposed BBOX paste has to be visible somewhere."""
    body = await (await client.get("/api/state")).json()
    assert body["config"]["bbox"]["lat_min"] == pytest.approx(47.859476)


async def test_state_lists_live_vessels_with_slot_assignment(client):
    registry = client.app_state.registry
    registry._live[1] = vessel(1)
    registry._live[2] = vessel(2, name="SAILBOAT", category=ShipCategory.SAILING,
                               priority=10, length_m=11.0, entered_at=T0)
    body = await (await client.get("/api/state")).json()
    assert {v["mmsi"] for v in body["live"]} == {1, 2}
    assert all("slot" in v and "eligible" in v for v in body["live"])


async def test_state_marks_filtered_vessels_so_exclusions_are_explicable(client, tmp_path):
    settings = Settings.from_env({**MINIMAL, "MIN_LENGTH_METERS": "50",
                                  "DB_PATH": str(tmp_path / "u.db")})
    client.app_state.settings = settings
    client.app_state.registry = VesselRegistry(settings)
    client.app_state.registry._live[2] = vessel(
        2, category=ShipCategory.SAILING, priority=10, length_m=11.0)
    body = await (await client.get("/api/state")).json()
    row = body["live"][0]
    assert row["eligible"] is False
    assert row["slot"] is None
    assert "min_length" in row["filtered_reason"]


async def test_ships_endpoint_returns_logged_visits(client):
    v = vessel(7)
    await client.app_state.storage.begin_visit(v)
    body = await (await client.get("/api/ships")).json()
    assert [row["mmsi"] for row in body["ships"]] == [7]


async def test_ships_endpoint_accepts_filters(client):
    await client.app_state.storage.begin_visit(vessel(1))
    await client.app_state.storage.begin_visit(
        vessel(2, category=ShipCategory.TANKER))
    body = await (await client.get("/api/ships?category=tanker&limit=10")).json()
    assert [row["mmsi"] for row in body["ships"]] == [2]


async def test_events_endpoint(client):
    await client.app_state.storage.log_event("WARN", "ws", "dropped", {"n": 1})
    body = await (await client.get("/api/events?level=WARN")).json()
    assert body["events"][0]["message"] == "dropped"
    assert body["events"][0]["detail"] == {"n": 1}


async def test_traffic_summary_endpoint(client):
    await client.app_state.storage.begin_visit(vessel(1))
    body = await (await client.get("/api/traffic-summary?window=7d")).json()
    assert body["total_visits"] == 1
    assert body["window_seconds"] == 604800


async def test_traffic_summary_rejects_a_bad_window(client):
    response = await client.get("/api/traffic-summary?window=banana")
    assert response.status == 400
    assert "window" in (await response.json())["error"]


async def test_websocket_pushes_state_then_frames(client):
    client.app_state.latest_frame = bytes([1, 2, 3] * (64 * 64))
    async with client.ws_connect("/ws/frames") as ws:
        first = json.loads(await ws.receive_str())
        assert first["type"] == "state"
        second = json.loads(await ws.receive_str())
        assert second["type"] == "frame"
        assert base64.b64decode(second["rgb"]) == client.app_state.latest_frame
        assert second["width"] == 64 and second["height"] == 64
