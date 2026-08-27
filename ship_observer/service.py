from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from .ais_client import AisClient
from .config import Settings
from .drivers import create_driver
from .drivers.base import DisplayDriver
from .models import Vessel
from .registry import VesselRegistry
from .render.canvas import Canvas
from .render.layout import capacity, render_frame
from .render.scroll import Scroller
from .selection import select_slots
from .storage import Storage
from .web.server import AppState, broadcast_frame, broadcast_state, create_app

log = logging.getLogger(__name__)

REGISTRY_PRUNE_INTERVAL = 30.0     # seconds
RETENTION_INTERVAL = 3600.0        # hourly
VISIT_UPDATE_INTERVAL = 30.0       # how often an open visit row is rewritten


class Service:
    """Owns every concurrent task and the objects they share."""

    def __init__(self, settings: Settings,
                 driver: DisplayDriver | None = None,
                 client: object | None = None) -> None:
        self.settings = settings
        self.storage = Storage(settings.db_path)
        self.registry = VesselRegistry(settings)
        self.driver = driver
        self.client = client
        self.scroller = Scroller()
        self.canvas = Canvas(settings.panel_width, settings.panel_height)
        self.state: AppState | None = None
        self.app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._raw_file = None
        self._last_visit_write: dict[int, float] = {}
        self._render_errors: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        await self.storage.open()
        if self.driver is None:
            self.driver = create_driver(self.settings, self.record_event)
        if self.client is None:
            self.client = AisClient(self.settings, on_event=self.record_event)
        self.state = AppState(settings=self.settings, registry=self.registry,
                              storage=self.storage)
        self.app = create_app(self.state)
        if self.settings.record_raw_path:
            path = Path(self.settings.record_raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._raw_file = path.open("a", encoding="utf-8")

    async def stop(self) -> None:
        # Close every open visit so no ship_log row is left dangling.
        for vessel in self.registry.close_all():
            await self._safe(self.storage.end_visit(vessel, "shutdown"))
        if self._runner is not None:
            await self._runner.cleanup()
        if self.driver is not None:
            self.driver.close()
        if self._raw_file is not None:
            self._raw_file.close()
        await self.storage.close()

    def record_event(self, level: str, category: str, message: str,
                     detail: dict | None = None) -> None:
        """Synchronous callback for AisClient and create_driver.

        Schedules the write rather than awaiting it, so a slow SD card can
        never stall the websocket consumer.
        """
        log.log(logging.getLevelName(level if level != "WARN" else "WARNING"),
                "[%s] %s", category, message)
        try:
            asyncio.get_running_loop().create_task(
                self._safe(self.storage.log_event(level, category, message, detail)))
        except RuntimeError:
            pass   # no loop yet: startup logging only

    @staticmethod
    async def _safe(coro) -> None:
        """Await a storage write, swallowing failures.

        Losing a log row is acceptable; taking down the display is not.
        """
        try:
            await coro
        except Exception:
            log.exception("storage write failed")

    # -- tasks -------------------------------------------------------------

    async def ingest_loop(self) -> None:
        assert self.state is not None and self.client is not None
        async for message in self.client.stream():
            self.state.connected = getattr(self.client, "connected", True)
            self.state.last_message_at = message.received_at

            if self._raw_file is not None:
                self._raw_file.write(json.dumps({
                    "received_at": message.received_at.isoformat(),
                    "message_type": message.message_type,
                    "mmsi": message.mmsi,
                    "meta_name": message.meta_name,
                    "lat": message.lat, "lon": message.lon,
                    "payload": message.payload,
                }) + "\n")
                self._raw_file.flush()

            change = self.registry.apply(message)
            vessel = change.vessel

            if change.entered:
                vessel.log_id = await self._begin_visit(vessel)
                self.record_event("INFO", "registry",
                                  f"entered: {vessel.display_name}",
                                  {"mmsi": vessel.mmsi})
            if change.departed:
                await self._safe(self.storage.end_visit(vessel,
                                                        vessel.depart_reason or "left_bbox"))
                self.record_event("INFO", "registry",
                                  f"departed: {vessel.display_name}",
                                  {"mmsi": vessel.mmsi,
                                   "reason": vessel.depart_reason})
            elif change.static_resolved_now or self._should_write(vessel):
                await self._safe(self.storage.update_visit(vessel))

    async def _begin_visit(self, vessel: Vessel) -> int | None:
        try:
            return await self.storage.begin_visit(vessel)
        except Exception:
            log.exception("could not open a ship_log row for %s", vessel.mmsi)
            return None

    def _should_write(self, vessel: Vessel) -> bool:
        """Throttle in-place visit updates - this is an SD card."""
        now = time.monotonic()
        last = self._last_visit_write.get(vessel.mmsi, 0.0)
        if now - last < VISIT_UPDATE_INTERVAL:
            return False
        self._last_visit_write[vessel.mmsi] = now
        return True

    async def render_loop(self) -> None:
        assert self.state is not None and self.driver is not None
        interval = 1.0 / self.settings.render_fps
        web_interval = 1.0 / self.settings.web_fps
        last = time.monotonic()
        last_web = 0.0

        while True:
            now = time.monotonic()
            dt, last = now - last, now

            slots = select_slots(self.registry.live(), self.registry.departed(),
                                 self.settings, capacity(self.settings.panel_height))
            self.state.slots = slots

            age = None
            if self.state.last_message_at is not None:
                age = (datetime.now(timezone.utc)
                       - self.state.last_message_at).total_seconds()
            stale = age is None or age > self.settings.stale_seconds

            try:
                render_frame(self.canvas, slots, self.scroller, dt, stale=stale)
            except Exception as exc:
                # Log each unique failure once; a per-frame exception would
                # otherwise flood the journal at RENDER_FPS.
                key = f"{type(exc).__name__}: {exc}"
                if key not in self._render_errors:
                    self._render_errors.add(key)
                    log.exception("render failed")
                    self.record_event("ERROR", "display", f"render failed: {key}")
                await asyncio.sleep(interval)
                continue

            frame = self.canvas.to_bytes()
            self.state.latest_frame = frame
            self.driver.show(frame)

            for vessel in slots.live:
                if not vessel.displayed:
                    vessel.displayed = True
                    await self._safe(self.storage.update_visit(vessel))

            if self.app is not None and now - last_web >= web_interval:
                last_web = now
                await broadcast_frame(self.app, self.state)
                await broadcast_state(self.app, self.state)

            await asyncio.sleep(max(0.0, interval - (time.monotonic() - now)))

    async def prune_once(self) -> None:
        for vessel in self.registry.prune():
            await self._safe(self.storage.end_visit(vessel, "timeout"))
            self._last_visit_write.pop(vessel.mmsi, None)
            self.record_event("INFO", "registry",
                              f"pruned: {vessel.display_name}",
                              {"mmsi": vessel.mmsi})

    async def registry_prune_loop(self) -> None:
        while True:
            await asyncio.sleep(REGISTRY_PRUNE_INTERVAL)
            await self.prune_once()

    async def retention_once(self) -> None:
        try:
            ships, events = await self.storage.prune(
                self.settings.ship_log_days, self.settings.event_log_hours)
        except Exception:
            log.exception("retention prune failed")
            return
        if ships or events:
            self.record_event("INFO", "storage", "retention prune",
                              {"ships_deleted": ships, "events_deleted": events})

    async def retention_loop(self) -> None:
        while True:
            await self.retention_once()
            await asyncio.sleep(RETENTION_INTERVAL)

    async def serve_web(self) -> None:
        assert self.app is not None
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.settings.http_host,
                           self.settings.http_port)
        await site.start()
        self.record_event("INFO", "web", "listening",
                          {"host": self.settings.http_host,
                           "port": self.settings.http_port})

    async def run(self) -> None:
        await self.start()
        await self.serve_web()
        self.record_event("INFO", "config", "service started",
                          {"bbox": self.settings.bbox.to_aisstream()})
        tasks = [asyncio.create_task(coro) for coro in (
            self.ingest_loop(), self.render_loop(),
            self.registry_prune_loop(), self.retention_loop(),
        )]
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.exception() is not None:
                    log.error("task failed", exc_info=task.exception())
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self.stop()
