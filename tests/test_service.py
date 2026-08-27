import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.ais_client import AisMessage
from ship_observer.config import Settings
from ship_observer.drivers.null import NullDriver
from ship_observer.service import Service
from ship_observer.storage import Storage

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)
IN_BOX = (47.88, -122.41)


def env(tmp_path, **overrides):
    return {"AIS_STREAM_API_KEY": "k",
            "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
            "DB_PATH": str(tmp_path / "t.db"),
            "DISPLAY_DRIVER": "null",
            **overrides}


class FakeClient:
    """Stands in for AisClient: yields queued messages, then idles."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.connected = True
        self.last_message_at = None
        self.dropped_frames = 0

    async def stream(self):
        for message in self._messages:
            self.last_message_at = message.received_at
            yield message
        while True:
            await asyncio.sleep(3600)


def position(mmsi, at, name="TEST SHIP", lat=IN_BOX[0], lon=IN_BOX[1]):
    return AisMessage(message_type="PositionReport", mmsi=mmsi, received_at=at,
                      meta_name=name, lat=lat, lon=lon,
                      payload={"Sog": 12.0, "Cog": 180.0})


def static(mmsi, at, ship_type=70):
    return AisMessage(message_type="ShipStaticData", mmsi=mmsi, received_at=at,
                      meta_name="EVER GIVEN", lat=IN_BOX[0], lon=IN_BOX[1],
                      payload={"Name": "EVER GIVEN", "CallSign": "H3RC",
                               "Destination": "SEATTLE", "Type": ship_type,
                               "Dimension": {"A": 300, "B": 100, "C": 30, "D": 30}})


@pytest.fixture
async def service(tmp_path):
    s = Service(Settings.from_env(env(tmp_path)),
                driver=NullDriver(64, 64),
                client=FakeClient([]))
    await s.start()
    yield s
    await s.stop()


async def test_ingest_opens_a_visit_row_on_entry(tmp_path):
    settings = Settings.from_env(env(tmp_path))
    svc = Service(settings, driver=NullDriver(64, 64),
                  client=FakeClient([position(1, T0)]))
    await svc.start()
    try:
        task = asyncio.create_task(svc.ingest_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        rows = await svc.storage._fetchall("SELECT * FROM ship_log")
        assert len(rows) == 1
        assert rows[0]["mmsi"] == 1
        assert rows[0]["departed_at"] is None
    finally:
        await svc.stop()


async def test_static_data_updates_the_same_visit_row(tmp_path):
    settings = Settings.from_env(env(tmp_path))
    svc = Service(settings, driver=NullDriver(64, 64),
                  client=FakeClient([position(1, T0),
                                     static(1, T0 + timedelta(seconds=30))]))
    await svc.start()
    try:
        task = asyncio.create_task(svc.ingest_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        rows = await svc.storage._fetchall("SELECT * FROM ship_log")
        assert len(rows) == 1, "static data must not open a second visit"
        assert rows[0]["category"] == "cargo"
        assert rows[0]["call_sign"] == "H3RC"
        assert rows[0]["length_m"] == pytest.approx(400.0)
    finally:
        await svc.stop()


async def test_render_loop_produces_frames_and_publishes_them(service):
    service.state.registry._live[1] = _vessel()
    task = asyncio.create_task(service.render_loop())
    await asyncio.sleep(0.25)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert service.driver.frames, "the driver must receive frames"
    assert service.state.latest_frame == service.driver.frames[-1]
    assert len(service.state.latest_frame) == 64 * 64 * 3


async def test_render_loop_marks_displayed_vessels(service):
    vessel = _vessel()
    service.state.registry._live[1] = vessel
    vessel.log_id = await service.storage.begin_visit(vessel)
    task = asyncio.create_task(service.render_loop())
    await asyncio.sleep(0.25)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert vessel.displayed is True


async def test_registry_prune_loop_closes_visits(tmp_path):
    settings = Settings.from_env(env(tmp_path, SHIP_TIMEOUT_SECONDS="30"))
    svc = Service(settings, driver=NullDriver(64, 64), client=FakeClient([]))
    await svc.start()
    try:
        vessel = _vessel(last_seen=datetime.now(timezone.utc) - timedelta(minutes=5))
        vessel.log_id = await svc.storage.begin_visit(vessel)
        svc.state.registry._live[1] = vessel

        await svc.prune_once()

        row = (await svc.storage._fetchall("SELECT * FROM ship_log"))[0]
        assert row["departed_at"] is not None
        assert row["depart_reason"] == "timeout"
    finally:
        await svc.stop()


async def test_retention_loop_deletes_expired_rows(tmp_path):
    settings = Settings.from_env(env(tmp_path, SHIP_LOG_DAYS="1"))
    svc = Service(settings, driver=NullDriver(64, 64), client=FakeClient([]))
    await svc.start()
    try:
        old = _vessel(entered_at=datetime.now(timezone.utc) - timedelta(days=3))
        await svc.storage.begin_visit(old)
        await svc.retention_once()
        assert await svc.storage._fetchall("SELECT * FROM ship_log") == []
    finally:
        await svc.stop()


async def test_storage_failures_never_stop_the_service(service, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(service.storage, "begin_visit", boom)
    service.client = FakeClient([position(1, T0)])
    task = asyncio.create_task(service.ingest_loop())
    await asyncio.sleep(0.1)
    assert not task.done(), "ingest must survive a storage failure"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_shutdown_closes_every_open_visit(tmp_path):
    settings = Settings.from_env(env(tmp_path))
    svc = Service(settings, driver=NullDriver(64, 64), client=FakeClient([]))
    await svc.start()
    vessel = _vessel()
    vessel.log_id = await svc.storage.begin_visit(vessel)
    svc.state.registry._live[1] = vessel

    await svc.stop()

    storage = svc.storage
    await storage.open()
    row = (await storage._fetchall("SELECT * FROM ship_log"))[0]
    await storage.close()
    assert row["depart_reason"] == "shutdown"
    assert row["departed_at"] is not None


async def test_run_cleans_up_and_reraises_if_startup_fails(tmp_path, monkeypatch):
    """A failure in start()/serve_web() must not leak the storage connection,
    the raw-recording file, or a partially-set-up aiohttp runner.
    """
    settings = Settings.from_env(env(tmp_path))
    svc = Service(settings, driver=NullDriver(64, 64), client=FakeClient([]))

    async def boom():
        raise OSError("port already in use")

    monkeypatch.setattr(svc, "serve_web", boom)

    with pytest.raises(OSError):
        await svc.run()

    assert svc.storage._conn is None, "storage must be closed on a failed startup"
    assert svc.driver.closed is True, "the driver must be closed on a failed startup"


async def test_stop_isolates_failures_between_cleanup_steps(tmp_path):
    """One failing cleanup step (a hung websocket during runner.cleanup(),
    say) must not skip the steps after it - driver.close() and
    storage.close() must still run. Uses a fake runner rather than a real
    aiohttp AppRunner/TCPSite so this test never binds an actual port.
    """
    settings = Settings.from_env(env(tmp_path))
    svc = Service(settings, driver=NullDriver(64, 64), client=FakeClient([]))
    await svc.start()

    class BoomingRunner:
        async def cleanup(self):
            raise RuntimeError("runner cleanup hung")

    svc._runner = BoomingRunner()

    await svc.stop()  # must not raise

    assert svc.driver.closed is True, "a failed runner cleanup must not skip driver.close()"
    assert svc.storage._conn is None, "a failed runner cleanup must not skip storage.close()"


async def test_stop_drains_in_flight_event_tasks(tmp_path):
    """record_event()'s fire-and-forget task must be tracked and awaited by
    stop(), not lost to garbage collection or left racing storage.close().
    """
    settings = Settings.from_env(env(tmp_path))
    svc = Service(settings, driver=NullDriver(64, 64), client=FakeClient([]))
    await svc.start()

    svc.record_event("INFO", "test", "in-flight event")
    assert svc._event_tasks, "record_event must track its fire-and-forget task"

    await svc.stop()

    assert all(t.done() for t in svc._event_tasks), (
        "every event task must complete before stop() returns"
    )
    reopened = Storage(settings.db_path)
    await reopened.open()
    rows = await reopened._fetchall(
        "SELECT message FROM event_log WHERE message = 'in-flight event'")
    await reopened.close()
    assert len(rows) == 1, "the event must actually be persisted, not dropped"


async def test_render_error_dedup_survives_a_varying_message(service, monkeypatch):
    """A message that embeds a varying value (e.g. a changing index) must
    still collapse to one dedup key by (exception type, call-site line) -
    not one key per distinct message, which would defeat the mechanism.
    """
    import ship_observer.service as service_module

    calls = {"n": 0}

    def flaky_render(*args, **kwargs):
        calls["n"] += 1
        raise IndexError(f"list index out of range: item {calls['n']}")

    monkeypatch.setattr(service_module, "render_frame", flaky_render)

    task = asyncio.create_task(service.render_loop())
    await asyncio.sleep(0.3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls["n"] > 1, "the flaky render must have been invoked more than once"
    assert len(service._render_errors) == 1, (
        "a varying message from the same call site must not defeat the dedup"
    )


def _vessel(**overrides):
    from ship_observer.models import ShipCategory, Vessel
    now = datetime.now(timezone.utc)
    base = dict(mmsi=1, entered_at=now, last_seen=now, name="EVER GIVEN",
                call_sign="H3RC", destination="SEATTLE",
                category=ShipCategory.CARGO, priority=30, length_m=400.0,
                static_resolved=True)
    base.update(overrides)
    return Vessel(**base)
