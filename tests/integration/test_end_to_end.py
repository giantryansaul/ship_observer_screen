import asyncio

import pytest

from ship_observer.ais_client import AisClient
from ship_observer.config import Settings
from ship_observer.drivers.null import NullDriver
from ship_observer.service import Service

from .fake_aisstream import FakeAisStream, envelope

CARGO_STATIC = dict(Name="EVER GIVEN", CallSign="H3RC", Destination="SEATTLE",
                    Type=70, Dimension={"A": 300, "B": 100, "C": 30, "D": 30})
SAIL_STATIC = dict(Name="WINDSONG", CallSign="WDX1", Destination="PORT LUDLOW",
                   Type=36, Dimension={"A": 8, "B": 4, "C": 2, "D": 2})


def env(tmp_path, **overrides):
    return {"AIS_STREAM_API_KEY": "test-key",
            "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
            "DB_PATH": str(tmp_path / "e2e.db"),
            "DISPLAY_DRIVER": "null",
            **overrides}


async def run_service(settings, server, seconds=0.3):
    service = Service(settings, driver=NullDriver(settings.panel_width,
                                                  settings.panel_height))
    # Assigned before start() so Service.start()'s own
    # `if self.client is None: self.client = AisClient(...)` never runs -
    # otherwise a real AisClient bound to the live websockets.connect would
    # be built and immediately discarded in this hermetic test.
    service.client = AisClient(settings, on_event=service.record_event,
                               connect=server.connect,
                               backoff_base=0.01, backoff_max=0.01)
    await service.start()
    ingest = asyncio.create_task(service.ingest_loop())
    render = asyncio.create_task(service.render_loop())
    await asyncio.sleep(seconds)
    ingest.cancel()
    render.cancel()
    # Awaited (not just cancelled) so a task that raised something other than
    # CancelledError surfaces as a test failure instead of an unretrieved-
    # task warning.
    await asyncio.gather(ingest, render, return_exceptions=True)
    return service


async def test_full_pipeline_from_websocket_to_frame(tmp_path):
    server = FakeAisStream([
        envelope(1, Sog=12.0, _name="EVER GIVEN"),
        envelope(1, "ShipStaticData", **CARGO_STATIC),
    ])
    settings = Settings.from_env(env(tmp_path))

    service = await run_service(settings, server)
    try:
        # The subscription went out in AISStream's exact shape.
        assert server.subscriptions[0]["APIKey"] == "test-key"
        assert server.subscriptions[0]["BoundingBoxes"] == [
            [[47.859476, -122.527428], [47.910359, -122.323322]]]

        rows = await service.storage._fetchall("SELECT * FROM ship_log")
        assert len(rows) == 1
        assert rows[0]["name"] == "EVER GIVEN"
        assert rows[0]["category"] == "cargo"
        assert rows[0]["length_m"] == pytest.approx(400.0)
        assert rows[0]["max_sog"] == pytest.approx(12.0), "position data must merge too"
        assert rows[0]["displayed"] == 1

        assert service.driver.frames
        assert len(service.driver.frames[-1]) == 64 * 64 * 3
        assert any(service.driver.frames[-1]), "the panel must not be black"
    finally:
        await service.stop()


async def test_reconnect_resubscribes_and_logs(tmp_path):
    server = FakeAisStream([envelope(1, Sog=10.0)])
    server.drop_next_connection()
    settings = Settings.from_env(env(tmp_path))

    service = await run_service(settings, server, seconds=0.4)
    try:
        assert server.connections >= 2, "must reconnect after the drop"
        assert len(server.subscriptions) >= 2, "must resubscribe"
        events = await service.storage.query_events(category="ws")
        assert any("disconnected" in e["message"] for e in events)
        assert any("reconnecting" in e["message"] for e in events)
    finally:
        await service.stop()


async def test_small_craft_is_logged_but_kept_off_the_panel(tmp_path):
    """The core noise-control guarantee: filters affect the panel, not the log."""
    # ShipStaticData is sent first so the vessel's category and length are
    # known from the moment it enters the registry. `is_eligible` treats a
    # vessel with no static data yet as always eligible (real static data can
    # take minutes, and gating on it would hide real traffic) - sending the
    # PositionReport first would leave a brief, real window where the render
    # loop could legitimately display this vessel before its length is known,
    # which is not what this test is checking.
    server = FakeAisStream([
        envelope(2, "ShipStaticData", **SAIL_STATIC),
        envelope(2, Sog=5.0, _name="WINDSONG"),
    ])
    settings = Settings.from_env(env(tmp_path, MIN_LENGTH_METERS="50"))

    service = await run_service(settings, server)
    try:
        rows = await service.storage._fetchall("SELECT * FROM ship_log")
        assert len(rows) == 1, "the sailboat must still be logged"
        assert rows[0]["category"] == "sailing"
        assert rows[0]["displayed"] == 0, "but never shown"
        assert service.driver.frames[-1] == bytes(64 * 64 * 3), "panel is black"
    finally:
        await service.stop()


async def test_priority_selection_gives_slots_to_the_big_ships(tmp_path):
    """The cargo ship enters FIRST (oldest of all four) and the three
    sailboats enter progressively LATER (all newer than it) - a real,
    non-tied chronological order, not the coincidental full-timestamp-tie
    the original version of this test had (every envelope shared one
    default time_utc, so the "proof" only held by accident of Python's
    stable sort over equal keys).

    Under pure recency the 3 most-recent entrants would be the 3 sailboats,
    evicting the cargo ship entirely - confirmed separately below. Under
    priority selection (the default, and what this test exercises), the
    cargo ship survives DESPITE being the single oldest entrant, correctly
    evicting the oldest sailboat instead. That contrast is the actual proof
    that priority - not recency - decided the outcome.
    """
    T0 = "2026-08-26 17:00:00.000000000 +0000 UTC"
    T1 = "2026-08-26 17:01:00.000000000 +0000 UTC"
    T2 = "2026-08-26 17:02:00.000000000 +0000 UTC"
    T3 = "2026-08-26 17:03:00.000000000 +0000 UTC"
    frames = [
        envelope(20, Sog=14.0, time_utc=T0),
        envelope(20, "ShipStaticData", time_utc=T0, **CARGO_STATIC),
    ]
    for mmsi, t in ((10, T1), (11, T2), (12, T3)):
        frames.append(envelope(mmsi, Sog=5.0, time_utc=t))
        frames.append(envelope(mmsi, "ShipStaticData", time_utc=t, **SAIL_STATIC))

    settings = Settings.from_env(env(tmp_path))
    service = await run_service(settings, FakeAisStream(frames))
    try:
        assert len(service.registry.live()) == 4
        live_mmsis = {v.mmsi for v in service.state.slots.live}
        assert 20 in live_mmsis, (
            "the cargo ship must hold a slot despite being the oldest entrant")
        assert len(live_mmsis) == 3, "only MAX_SHIPS=3 slots exist"

        # Confirm the contrast is real: pure recency would have excluded the
        # cargo ship entirely, since it is chronologically the oldest of all
        # four vessels.
        from ship_observer.selection import select_slots
        from ship_observer.render.layout import capacity
        recency_settings = Settings.from_env(
            env(tmp_path, PRIORITY_SELECTION="false"))
        recency_slots = select_slots(service.registry.live(), [],
                                     recency_settings, capacity(64))
        assert 20 not in {v.mmsi for v in recency_slots.live}, (
            "pure recency must exclude the oldest entrant, proving priority "
            "- not recency - is what saved the cargo ship above")

        rows = await service.storage._fetchall(
            "SELECT mmsi FROM ship_log ORDER BY mmsi")
        assert [r["mmsi"] for r in rows] == [10, 11, 12, 20], "all four logged"
    finally:
        await service.stop()


async def test_out_of_box_position_departs_and_closes_the_visit(tmp_path):
    server = FakeAisStream([
        envelope(1, Sog=12.0),
        envelope(1, Sog=12.0, _lat=47.70),   # south of the box
    ])
    settings = Settings.from_env(env(tmp_path))

    service = await run_service(server=server, settings=settings)
    try:
        row = (await service.storage._fetchall("SELECT * FROM ship_log"))[0]
        assert row["depart_reason"] == "left_bbox"
        assert row["departed_at"] is not None
        assert service.registry.live() == []
    finally:
        await service.stop()


async def test_recorded_session_replays_to_the_same_result(tmp_path):
    """Recording and replay must round-trip - this is the backtest guarantee."""
    from ship_observer.replay import replay

    recording = tmp_path / "session.jsonl"
    server = FakeAisStream([
        envelope(1, Sog=12.0, _name="EVER GIVEN"),
        envelope(1, "ShipStaticData", **CARGO_STATIC),
    ])
    live_settings = Settings.from_env(
        env(tmp_path, RECORD_RAW_PATH=str(recording)))
    service = await run_service(live_settings, server)
    await service.stop()

    assert recording.exists()
    assert len(recording.read_text().strip().splitlines()) == 2

    replay_settings = Settings.from_env(
        env(tmp_path, DB_PATH=str(tmp_path / "replay.db")))
    result = await replay(recording, replay_settings, speed=0.0)

    assert result["visits"] == 1
    assert result["by_category"] == {"cargo": 1}
