import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ship_observer.config import Settings
from ship_observer.models import DisplayMode, ShipCategory, Vessel
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


async def test_index_serves_only_the_panel_kiosk_page(client):
    """/ is the panel-only display; the debug tables live at /debug."""
    body = (await (await client.get("/")).text()).lower()
    assert "traffic-summary" not in body


async def test_debug_serves_the_full_debug_page(client):
    response = await client.get("/debug")
    assert response.status == 200
    assert "text/html" in response.headers["Content-Type"]
    body = (await response.text()).lower()
    assert "live-table" in body
    assert "traffic-summary" in body


async def test_panel_serves_the_audit_page(client):
    response = await client.get("/panel")
    assert response.status == 200
    assert "text/html" in response.headers["Content-Type"]
    body = (await response.text()).lower()
    assert "canvas" in body
    assert "chars" in body and "icons" in body and "ships" in body


@pytest.mark.parametrize("view", ["chars", "icons", "ships"])
async def test_panel_audit_api_renders_each_view(client, view):
    response = await client.get(f"/api/panel-audit?view={view}")
    assert response.status == 200
    body = await response.json()
    assert body["width"] == client.app_state.settings.panel_width
    assert body["height"] == client.app_state.settings.panel_height
    assert body["rgb"], "expected a non-empty base64 frame"


async def test_panel_audit_api_includes_the_category_legend_for_icons(client):
    """The HTML legend must come from the same source as the icon colors,
    not a hand-copied duplicate that can drift."""
    from ship_observer.models import ShipCategory
    from ship_observer.render.icons import CATEGORY_COLOR

    response = await client.get("/api/panel-audit?view=icons")
    body = await response.json()
    assert [c["name"] for c in body["categories"]] == [c.value for c in ShipCategory]
    fishing = next(c for c in body["categories"] if c["name"] == "fishing")
    assert tuple(fishing["color"]) == CATEGORY_COLOR[ShipCategory.FISHING]


async def test_panel_audit_api_omits_the_legend_for_other_views(client):
    response = await client.get("/api/panel-audit?view=chars")
    body = await response.json()
    assert "categories" not in body


async def test_panel_audit_api_renders_the_chars_view_in_the_large_font(client):
    small = await (await client.get("/api/panel-audit?view=chars")).json()
    large = await (await client.get("/api/panel-audit?view=chars&font=large")).json()
    assert large["rgb"], "expected a non-empty base64 frame"
    assert large["rgb"] != small["rgb"], "the 6x10 frame must differ from the 4x6 one"


async def test_panel_audit_api_defaults_to_the_small_font(client):
    default = await (await client.get("/api/panel-audit?view=chars")).json()
    explicit = await (await client.get("/api/panel-audit?view=chars&font=small")).json()
    assert default["rgb"] == explicit["rgb"]


async def test_panel_audit_api_rejects_an_unknown_font(client):
    response = await client.get("/api/panel-audit?view=chars&font=bogus")
    assert response.status == 400


async def test_panel_audit_api_rejects_an_unknown_view(client):
    response = await client.get("/api/panel-audit?view=bogus")
    assert response.status == 400


async def test_panel_audit_api_requires_a_view(client):
    response = await client.get("/api/panel-audit")
    assert response.status == 400


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


async def test_state_resolves_a_us_guid_destination_for_the_debug_page(client, monkeypatch):
    from ship_observer.uscg_locations import GuidPlace
    monkeypatch.setattr(
        "ship_observer.uscg_locations._table",
        lambda: {"0TEM": GuidPlace("Guemes Channel WA", "Dakota Creek Industries East Pier.")},
    )
    registry = client.app_state.registry
    registry._live[1] = vessel(1, destination="US^0TEM")
    body = await (await client.get("/api/state")).json()
    v = next(v for v in body["live"] if v["mmsi"] == 1)
    assert v["destination"] == "US^0TEM", "raw destination must stay unchanged"
    assert v["destination_resolved"] == "Guemes Channel WA (Dakota Creek Industries East Pier.)"


async def test_state_omits_the_resolved_destination_when_unresolvable(client):
    registry = client.app_state.registry
    registry._live[1] = vessel(1, destination="SEATTLE")
    body = await (await client.get("/api/state")).json()
    v = next(v for v in body["live"] if v["mmsi"] == 1)
    assert v["destination_resolved"] is None


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


async def test_ships_endpoint_rejects_a_non_numeric_limit(client):
    response = await client.get("/api/ships?limit=abc")
    assert response.status == 400
    assert "limit" in (await response.json())["error"]


async def test_ships_endpoint_rejects_a_malformed_since_timestamp(client):
    response = await client.get("/api/ships?since=not-a-datetime")
    assert response.status == 400
    assert "since" in (await response.json())["error"]


async def test_ships_endpoint_honors_until(client):
    await client.app_state.storage.begin_visit(
        vessel(1, entered_at=T0 - timedelta(hours=5), last_seen=T0 - timedelta(hours=5)))
    await client.app_state.storage.begin_visit(
        vessel(2, entered_at=T0 - timedelta(hours=3), last_seen=T0 - timedelta(hours=3)))
    await client.app_state.storage.begin_visit(
        vessel(3, entered_at=T0 - timedelta(hours=1), last_seen=T0 - timedelta(hours=1)))

    until = (T0 - timedelta(hours=2)).isoformat()
    # `+` is unreserved in a query string but decodes to a literal space by
    # convention (RFC 3986 sec 3.4 vs. form-urlencoding) - the isoformat()
    # UTC offset must be percent-encoded or the server sees a malformed
    # timestamp instead of the intended one.
    body = await (await client.get(f"/api/ships?until={quote(until, safe='')}")).json()
    assert [s["mmsi"] for s in body["ships"]] == [2, 1]


async def test_ships_endpoint_rejects_a_malformed_until_timestamp(client):
    response = await client.get("/api/ships?until=not-a-datetime")
    assert response.status == 400
    assert "until" in (await response.json())["error"]


async def test_events_endpoint(client):
    await client.app_state.storage.log_event("WARN", "ws", "dropped", {"n": 1})
    body = await (await client.get("/api/events?level=WARN")).json()
    assert body["events"][0]["message"] == "dropped"
    assert body["events"][0]["detail"] == {"n": 1}


async def test_events_endpoint_rejects_a_non_numeric_limit(client):
    response = await client.get("/api/events?limit=xyz")
    assert response.status == 400
    assert "limit" in (await response.json())["error"]


async def test_events_endpoint_rejects_a_malformed_since_timestamp(client):
    response = await client.get("/api/events?since=still-not-a-datetime")
    assert response.status == 400
    assert "since" in (await response.json())["error"]


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


async def test_broadcast_frame_and_broadcast_state_reach_a_connected_client(client):
    """The periodic-broadcast path a later task's render loop actually drives,
    distinct from the initial-connect push tested above.
    """
    from ship_observer.web.server import broadcast_frame, broadcast_state

    async with client.ws_connect("/ws/frames") as ws:
        await ws.receive_str()  # initial state push on connect - drain it

        client.app_state.latest_frame = bytes([9, 9, 9] * (64 * 64))
        await broadcast_frame(client.app, client.app_state)
        frame_msg = json.loads(await ws.receive_str())
        assert frame_msg["type"] == "frame"
        assert base64.b64decode(frame_msg["rgb"]) == client.app_state.latest_frame

        await broadcast_state(client.app, client.app_state)
        state_msg = json.loads(await ws.receive_str())
        assert state_msg["type"] == "state"


async def test_display_mode_endpoint_reports_the_current_and_available_modes(client):
    body = await (await client.get("/api/display-mode")).json()
    assert body["mode"] == "three_ship"
    assert body["modes"] == ["three_ship", "two_ship", "one_ship"]


async def test_display_mode_post_updates_state_and_persists_it(client):
    response = await client.post("/api/display-mode", json={"mode": "one_ship"})
    assert response.status == 200
    assert (await response.json())["mode"] == "one_ship"
    assert client.app_state.display_mode is DisplayMode.ONE_SHIP
    # Persisted, so a restart comes back up in the mode the user chose.
    assert await client.app_state.storage.get_setting("display_mode") == "one_ship"


async def test_display_mode_survives_an_app_state_reload(client):
    await client.post("/api/display-mode", json={"mode": "two_ship"})
    reloaded = AppState(
        settings=client.app_state.settings,
        registry=client.app_state.registry,
        storage=client.app_state.storage,
        display_mode=DisplayMode.coerce(
            await client.app_state.storage.get_setting("display_mode")))
    assert reloaded.display_mode is DisplayMode.TWO_SHIP


@pytest.mark.parametrize("body", [{"mode": "four_ship"}, {"mode": None}, {}])
async def test_display_mode_post_rejects_an_unknown_mode(client, body):
    response = await client.post("/api/display-mode", json=body)
    assert response.status == 400
    assert "mode" in (await response.json())["error"]
    assert client.app_state.display_mode is DisplayMode.THREE_SHIP


async def test_display_mode_post_rejects_a_non_json_body(client):
    response = await client.post("/api/display-mode", data="not json")
    assert response.status == 400


async def test_state_reports_the_display_mode(client):
    client.app_state.display_mode = DisplayMode.TWO_SHIP
    body = await (await client.get("/api/state")).json()
    assert body["display_mode"] == "two_ship"


async def test_display_mode_post_broadcasts_state_to_open_pages(client):
    """Every open dropdown follows the change without a reload."""
    async with client.ws_connect("/ws/frames") as ws:
        await ws.receive_str()   # initial state push on connect - drain it
        response = await client.post("/api/display-mode", json={"mode": "one_ship"})
        assert response.status == 200
        message = json.loads(await ws.receive_str())
        assert message["type"] == "state"
        assert message["display_mode"] == "one_ship"
