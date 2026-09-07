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
from .models import DisplayMode, Vessel
from .registry import VesselRegistry
from .render.canvas import Canvas
from .render.display import PAGE_SIZE, render_display
from .render.layout import capacity
from .render.scroll import Scroller
from .selection import RotationView, select_rotation, select_slots
from .storage import Storage
from .web.server import (DISPLAY_MODE_SETTING, AppState, broadcast_frame,
                         broadcast_state, create_app)

log = logging.getLogger(__name__)

REGISTRY_PRUNE_INTERVAL = 30.0     # seconds
RETENTION_INTERVAL = 3600.0        # hourly
VISIT_UPDATE_INTERVAL = 30.0       # how often an open visit row is rewritten
# How long one page of the 2- and 1-ship rotation stays on screen. Long
# enough to read a name and destination off the panel in passing; short
# enough that a busy box still comes round inside a couple of minutes.
DWELL_SECONDS = 10.0


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
        # Rotation clock for the 2- and 1-ship modes. The page itself lives
        # on AppState, so the web layer numbers slots by what is actually
        # on screen; only the timer that moves it is private here.
        self._dwell = 0.0
        self._rotation_pages = 0
        # create_task()'s return value must be kept somewhere, or the task
        # can be garbage-collected mid-execution - a documented asyncio
        # footgun. Discarded via add_done_callback once it finishes.
        self._event_tasks: set[asyncio.Task] = set()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        await self.storage.open()
        if self.driver is None:
            self.driver = create_driver(self.settings, self.record_event)
        if self.client is None:
            self.client = AisClient(self.settings, on_event=self.record_event)
        self.state = AppState(settings=self.settings, registry=self.registry,
                              storage=self.storage,
                              display_mode=await self._stored_display_mode())
        self.app = create_app(self.state)
        if self.settings.record_raw_path:
            path = Path(self.settings.record_raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._raw_file = path.open("a", encoding="utf-8")

    async def _stored_display_mode(self) -> DisplayMode:
        """The mode the web UI last selected, so a restart comes back up
        showing what the user chose. Anything unreadable or unrecognized
        falls back to the default rather than blocking startup.
        """
        try:
            stored = await self.storage.get_setting(DISPLAY_MODE_SETTING)
        except Exception:
            log.exception("could not read the stored display mode")
            return DisplayMode.THREE_SHIP
        return DisplayMode.coerce(stored)

    async def stop(self) -> None:
        # Close every open visit so no ship_log row is left dangling.
        for vessel in self.registry.close_all():
            await self._safe(self.storage.end_visit(vessel, "shutdown"))
            self._last_visit_write.pop(vessel.mmsi, None)

        # Let in-flight fire-and-forget event writes finish before storage
        # closes underneath them, rather than losing them or racing close().
        if self._event_tasks:
            await asyncio.gather(*self._event_tasks, return_exceptions=True)

        # Each step is independently guarded: one failure (a hung websocket
        # during runner cleanup, say) must not skip the rest - a skipped
        # driver.close() leaves the panel lit with a stale frame, and a
        # skipped storage.close() leaves SQLite open with an uncheckpointed
        # WAL. Every guarantee below stop() depends on all of this running.
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                log.exception("error cleaning up the web runner")
        if self.driver is not None:
            try:
                self.driver.close()
            except Exception:
                log.exception("error closing the display driver")
        if self._raw_file is not None:
            try:
                self._raw_file.close()
            except Exception:
                log.exception("error closing the raw recording file")
        try:
            await self.storage.close()
        except Exception:
            log.exception("error closing storage")

    def record_event(self, level: str, category: str, message: str,
                     detail: dict | None = None) -> None:
        """Synchronous callback for AisClient and create_driver.

        Schedules the write rather than awaiting it, so a slow SD card can
        never stall the websocket consumer.
        """
        log.log(logging.getLevelName(level if level != "WARN" else "WARNING"),
                "[%s] %s", category, message)
        try:
            task = asyncio.get_running_loop().create_task(
                self._safe(self.storage.log_event(level, category, message, detail)))
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)
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

            # Prune by stream time, not wall time, and before applying the
            # message: a vessel returning after SHIP_TIMEOUT_SECONDS must
            # close its old visit first or the two merge into one. This is
            # also what lets a recorded session replay with the same visit
            # boundaries the live run produced.
            await self.prune_once(now=message.received_at)

            change = self.registry.apply(message)
            vessel = change.vessel

            if change.entered:
                vessel.log_id = await self._begin_visit(vessel)
                self.record_event("INFO", "registry",
                                  f"entered: {vessel.display_name}",
                                  {"mmsi": vessel.mmsi})
                if not vessel.static_resolved:
                    await self._seed_static(vessel)
            if change.departed:
                await self._safe(self.storage.end_visit(vessel,
                                                        vessel.depart_reason or "left_bbox"))
                self._last_visit_write.pop(vessel.mmsi, None)
                self.record_event("INFO", "registry",
                                  f"departed: {vessel.display_name}",
                                  {"mmsi": vessel.mmsi,
                                   "reason": vessel.depart_reason})
            elif change.static_resolved_now or (
                    not change.entered and self._should_write(vessel)):
                # `not change.entered` avoids re-serializing the exact row
                # begin_visit() just inserted - a wasted write on the SD
                # card the throttle exists to protect.
                await self._safe(self.storage.update_visit(vessel))

    async def _seed_static(self, vessel: Vessel) -> None:
        """Resolve a fresh visit from the vessel's last resolved visit.

        AISStream's static delivery is patchy - 64 of the first weekend's 165
        visits never received ShipStaticData, including regulars that had
        resolved it on an earlier pass - so a new visit starts from what a
        previous one already learned rather than showing UNKNOWN again.
        """
        try:
            payload = await self.storage.latest_static(vessel.mmsi)
        except Exception:
            log.exception("static-cache lookup failed for %s", vessel.mmsi)
            return
        if payload and self.registry.seed_static(vessel.mmsi, payload):
            await self._safe(self.storage.update_visit(vessel))
            self.record_event("INFO", "registry",
                              f"static seeded from a previous visit: "
                              f"{vessel.display_name}",
                              {"mmsi": vessel.mmsi})

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

    def _advance_rotation(self, mode: DisplayMode, dt: float,
                          live: list[Vessel],
                          departed: list[Vessel]) -> RotationView:
        """The rotation page the 2- and 1-ship modes should draw now.

        The clock accumulates the render loop's own dt rather than reading
        the wall clock, so a slow frame delays the flip instead of the
        panel skipping a page nobody saw.

        Any change in the page count restarts the dwell, because whatever
        is on the timer was measured against a rotation that no longer
        exists. Shrinking, that means the page select_rotation just
        clamped to gets a full turn. Growing, it means the new page 0 is
        actually drawn: a quiet box sits on one page (or none) for
        minutes, and nothing consumes the timer while there is nothing to
        page through, so without the reset the second ship to arrive would
        flip the panel past the first one on the very next frame. A mode
        change is the same story - it resizes the pages under the timer.
        """
        page_size = PAGE_SIZE[mode]
        view = select_rotation(live, departed, self.settings, page_size,
                               self.state.rotation_page)
        if view.pages != self._rotation_pages:
            self._dwell = 0.0
        self._rotation_pages = view.pages

        self._dwell += dt
        if self._dwell >= DWELL_SECONDS and view.pages > 1:
            self._dwell = 0.0
            view = select_rotation(live, departed, self.settings, page_size,
                                   view.page + 1)
        self.state.rotation_page = view.page
        return view

    async def render_loop(self) -> None:
        assert self.state is not None and self.driver is not None
        interval = 1.0 / self.settings.render_fps
        web_interval = 1.0 / self.settings.web_fps
        last = time.monotonic()
        last_web = 0.0

        while True:
            now = time.monotonic()
            dt, last = now - last, now

            # Refreshed here, not just on message arrival in ingest_loop:
            # a legitimately-connected client on a quiet box (no messages
            # for a while) must not be reported as disconnected, and a real
            # disconnect must be visible even if no message ever arrives to
            # notice it via ingest_loop.
            self.state.connected = bool(getattr(self.client, "connected", False))
            self.state.dropped_frames = getattr(self.client, "dropped_frames", 0)

            live, departed = self.registry.live(), self.registry.departed()
            slots = select_slots(live, departed, self.settings,
                                 capacity(self.settings.panel_height))
            self.state.slots = slots

            mode = self.state.display_mode
            view = (None if mode is DisplayMode.THREE_SHIP
                    else self._advance_rotation(mode, dt, live, departed))

            age = None
            if self.state.last_message_at is not None:
                age = (datetime.now(timezone.utc)
                       - self.state.last_message_at).total_seconds()
            stale = age is None or age > self.settings.stale_seconds

            try:
                render_display(mode, self.canvas, slots, self.scroller, dt,
                               stale=stale, view=view)
                frame = self.canvas.to_bytes()
                self.state.latest_frame = frame
                self.driver.show(frame)
            except Exception as exc:
                # Dedup by (exception type, the line in THIS function that
                # raised it) rather than the full message: a message that
                # embeds a varying value (an mmsi, a list index) would give
                # every occurrence a distinct key, defeating the dedup and
                # flooding the journal at RENDER_FPS - exactly what this
                # mechanism exists to prevent. The traceback's outermost
                # frame is always this try block's call site, which is
                # exactly the granularity that's actually useful here.
                tb = exc.__traceback__
                key = f"{type(exc).__name__}@{tb.tb_lineno if tb else 0}"
                if key not in self._render_errors:
                    self._render_errors.add(key)
                    log.exception("render failed")
                    self.record_event("ERROR", "display",
                                      f"render failed: {type(exc).__name__}: {exc}")
                await asyncio.sleep(interval)
                continue

            # `displayed` means "was actually on the panel": in a rotation
            # mode that is this page's vessels, not every one waiting its
            # turn - the rest are marked as their pages come up.
            on_screen = slots.live if view is None else (
                [] if view.from_history else view.vessels)
            for vessel in on_screen:
                if not vessel.displayed:
                    vessel.displayed = True
                    await self._safe(self.storage.update_visit(vessel))

            if self.app is not None and now - last_web >= web_interval:
                last_web = now
                await broadcast_frame(self.app, self.state)
                await broadcast_state(self.app, self.state)

            await asyncio.sleep(max(0.0, interval - (time.monotonic() - now)))

    async def prune_once(self, now: datetime | None = None) -> None:
        for vessel in self.registry.prune(now):
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
        try:
            await self.start()
            await self.serve_web()
        except BaseException:
            # BaseException, not Exception: asyncio.CancelledError is a
            # BaseException, and a SIGINT/SIGTERM landing mid-startup (e.g.
            # Ctrl-C right after launching on a Pi) must still release
            # whatever was already acquired - the storage connection opened
            # in start(), an opened raw-recording file, a partially-set-up
            # aiohttp runner - rather than leaking it. Always re-raised
            # unconditionally below, so nothing is silently swallowed.
            log.exception("service failed to start")
            await self.stop()
            raise

        self.record_event("INFO", "config", "service started",
                          {"bbox": self.settings.bbox.to_aisstream()})
        tasks = [asyncio.create_task(coro) for coro in (
            self.ingest_loop(), self.render_loop(),
            self.registry_prune_loop(), self.retention_loop(),
        )]
        failed = False
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.exception() is not None:
                    log.error("task failed", exc_info=task.exception())
                    failed = True
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self.stop()

        if failed:
            raise RuntimeError(
                "a supervised task failed; see the error above for details")
