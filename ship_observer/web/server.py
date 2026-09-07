from __future__ import annotations

import base64
import json
import logging
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from ..config import Settings
from ..models import DisplayMode, Vessel
from ..registry import VesselRegistry
from ..render.audit import (
    dummy_slots,
    icon_audit_categories,
    icon_audit_pages,
    render_font_audit,
    render_icon_audit,
)
from ..render.canvas import Canvas
from ..render.display import PAGE_SIZE, render_display
from ..render.font import Font
from ..render.icons import CATEGORY_COLOR
from ..render.layout import capacity
from ..render.scroll import Scroller
from ..selection import (Slots, filtered_reason, is_eligible, select_rotation,
                         select_slots)
from ..storage import Storage, parse_window
from ..uscg_locations import resolve_destination

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# AppKey's optional second argument is a *type*; AppState is defined below,
# so both keys are declared untyped rather than with a forward reference.
WEBSOCKETS_KEY = web.AppKey("websockets")
STATE_KEY = web.AppKey("state")
# app_settings key the selected display mode is persisted under.
DISPLAY_MODE_SETTING = "display_mode"


@dataclass
class AppState:
    """Everything the web layer reads. Mutated by __main__'s tasks."""

    settings: Settings
    registry: VesselRegistry
    storage: Storage
    display_mode: DisplayMode = DisplayMode.THREE_SHIP
    # Which page of the rotation the 2- and 1-ship modes are showing, kept
    # current by the render loop's rotation clock.
    rotation_page: int = 0
    latest_frame: bytes | None = None
    connected: bool = False
    last_message_at: datetime | None = None
    slots: Slots = field(default_factory=Slots)
    dropped_frames: int = 0
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))

    def filtered_reason(self, vessel: Vessel) -> str | None:
        return filtered_reason(vessel, self.settings)

    def _resolved_destination(self, vessel: Vessel) -> str | None:
        """Plain-English place name for a US/GUID destination code (USCG AIS
        Encoding Guide v.25), or None when the raw destination doesn't
        resolve - the raw text is shown as-is either way."""
        if not vessel.destination:
            return None
        resolved = resolve_destination(vessel.destination)
        return resolved.detail if resolved is not None else None

    def vessel_json(self, vessel: Vessel, slot: int | None) -> dict[str, Any]:
        return {
            "mmsi": vessel.mmsi,
            "name": vessel.name,
            "display_name": vessel.display_name,
            "call_sign": vessel.call_sign,
            "destination": vessel.destination,
            "destination_resolved": self._resolved_destination(vessel),
            "ship_type": vessel.ship_type,
            "category": vessel.category.value,
            "priority": vessel.priority,
            "length_m": vessel.length_m,
            "beam_m": vessel.beam_m,
            "draught_m": vessel.draught_m,
            "eta": vessel.eta,
            "imo": vessel.imo,
            "lat": vessel.last_lat,
            "lon": vessel.last_lon,
            "sog": vessel.max_sog,
            "cog": vessel.last_cog,
            "heading": vessel.last_heading,
            "nav_status": vessel.nav_status,
            "entered_at": vessel.entered_at.isoformat(),
            "last_seen": vessel.last_seen.isoformat(),
            "departed_at": (vessel.departed_at.isoformat()
                            if vessel.departed_at else None),
            "depart_reason": vessel.depart_reason,
            "position_count": vessel.position_count,
            "static_resolved": vessel.static_resolved,
            "eligible": is_eligible(vessel, self.settings),
            "filtered_reason": self.filtered_reason(vessel),
            "slot": slot,
        }

    def _on_screen(self, live: list[Vessel],
                   departed: list[Vessel]) -> tuple[list[Vessel],
                                                    list[Vessel], bool]:
        """The vessels the panel is actually drawing, for the current mode.

        The debug page's slot numbers have to describe what is on the
        panel; in a rotation mode that is one page of the box, not the
        3-ship layout's three slots. The rotation modes have no divider,
        so they never claim one.
        """
        if self.display_mode is DisplayMode.THREE_SHIP:
            slots = select_slots(live, departed, self.settings,
                                 capacity(self.settings.panel_height))
            return slots.live, slots.history, slots.show_divider
        view = select_rotation(live, departed, self.settings,
                               PAGE_SIZE[self.display_mode],
                               self.rotation_page)
        if view.from_history:
            return [], view.vessels, False
        return view.vessels, [], False

    def state_json(self) -> dict[str, Any]:
        live = self.registry.live()
        departed = self.registry.departed()
        on_screen, on_screen_history, show_divider = self._on_screen(
            live, departed)
        slot_index = {v.mmsi: i for i, v in enumerate(on_screen)}
        history_index = {v.mmsi: i for i, v in enumerate(on_screen_history)}
        now = datetime.now(timezone.utc)
        age = ((now - self.last_message_at).total_seconds()
               if self.last_message_at else None)

        return {
            "type": "state",
            "now": now.isoformat(),
            "connected": self.connected,
            "stale": age is None or age > self.settings.stale_seconds,
            "last_message_age_seconds": age,
            "uptime_seconds": (now - self.started_at).total_seconds(),
            "dropped_frames": self.dropped_frames,
            "config": self.settings.redacted(),
            "display_mode": self.display_mode.value,
            "capacity": capacity(self.settings.panel_height),
            "show_divider": show_divider,
            "live": [self.vessel_json(v, slot_index.get(v.mmsi)) for v in live],
            "history": [self.vessel_json(v, history_index.get(v.mmsi))
                        for v in departed],
        }


def _frame_json(state: AppState) -> dict[str, Any] | None:
    if state.latest_frame is None:
        return None
    return {
        "type": "frame",
        "width": state.settings.panel_width,
        "height": state.settings.panel_height,
        "rgb": base64.b64encode(state.latest_frame).decode("ascii"),
    }


def _since(request: web.Request) -> datetime | None:
    raw = request.query.get("since")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"since must be an ISO-8601 datetime, got {raw!r}"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _until(request: web.Request) -> datetime | None:
    raw = request.query.get("until")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"until must be an ISO-8601 datetime, got {raw!r}"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _index(request: web.Request) -> web.FileResponse:
    """The panel-only kiosk page: just the mirror and connection status."""
    return web.FileResponse(STATIC_DIR / "index.html")


async def _debug(request: web.Request) -> web.FileResponse:
    """The full debug page: mirror, live vessels, traffic, events, config."""
    return web.FileResponse(STATIC_DIR / "debug.html")


async def _panel(request: web.Request) -> web.FileResponse:
    """The panel audit tool: font, icon, and sample-data views."""
    return web.FileResponse(STATIC_DIR / "panel.html")


_PANEL_AUDIT_VIEWS = ("chars", "icons", "ships")
# The characters view can be rendered in either panel font; the others are
# drawn by code that owns its own sizing.
_PANEL_AUDIT_FONTS = {"small": Font.default, "large": Font.large}
# The icons view draws one icon size per frame, paging when a size no longer
# fits every category on one.
_PANEL_AUDIT_ICON_SIZES = ("8", "16", "32")


async def _panel_audit(request: web.Request) -> web.Response:
    """One static frame for the /panel audit tool, rendered through the
    real pipeline so it is pixel-identical to what the hardware would show.
    """
    state: AppState = request.app[STATE_KEY]
    view = request.query.get("view")
    if view not in _PANEL_AUDIT_VIEWS:
        return web.json_response(
            {"error": f"view must be one of chars, icons, ships; got {view!r}"},
            status=400)
    font_name = request.query.get("font", "small")
    if font_name not in _PANEL_AUDIT_FONTS:
        return web.json_response(
            {"error": f"font must be one of small, large; got {font_name!r}"},
            status=400)
    size_name = request.query.get("size", "8")
    if size_name not in _PANEL_AUDIT_ICON_SIZES:
        return web.json_response(
            {"error": "size must be one of "
                      f"{', '.join(_PANEL_AUDIT_ICON_SIZES)}; got {size_name!r}"},
            status=400)
    size = int(size_name)
    pages = icon_audit_pages(size)
    page_name = request.query.get("page", "0")
    if not page_name.isdigit() or int(page_name) >= pages:
        return web.json_response(
            {"error": f"page must be 0..{pages - 1} at size {size}; "
                      f"got {page_name!r}"},
            status=400)
    page = int(page_name)

    canvas = Canvas(state.settings.panel_width, state.settings.panel_height)
    if view == "ships":
        # In the mode the panel is actually in: an audit of a layout
        # nobody is looking at is worth nothing.
        render_display(state.display_mode, canvas, dummy_slots(), Scroller(),
                       dt=0.0)
    elif view == "icons":
        render_icon_audit(canvas, size=size, page=page)
    else:
        render_font_audit(canvas, font=_PANEL_AUDIT_FONTS[font_name]())

    payload = {
        "width": state.settings.panel_width,
        "height": state.settings.panel_height,
        "rgb": base64.b64encode(canvas.to_bytes()).decode("ascii"),
    }
    if view == "icons":
        # Same order render_icon_audit lays the grid out in, and the same
        # colors the icons themselves are drawn with - so the legend can
        # never drift from what's actually on screen. Only this page's
        # categories: the legend identifies icons by grid position.
        payload["pages"] = pages
        payload["page"] = page
        payload["categories"] = [
            {"name": category.value, "color": list(CATEGORY_COLOR[category])}
            for category in icon_audit_categories(size, page)
        ]
    return web.json_response(payload)


async def _healthz(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    age = ((datetime.now(timezone.utc) - state.last_message_at).total_seconds()
           if state.last_message_at else None)
    return web.json_response({
        "ok": True,
        "connected": state.connected,
        "last_message_age_seconds": age,
        "uptime_seconds": (datetime.now(timezone.utc)
                           - state.started_at).total_seconds(),
    })


async def _state(request: web.Request) -> web.Response:
    return web.json_response(request.app[STATE_KEY].state_json())


def _display_mode_json(state: AppState) -> dict[str, Any]:
    return {
        "mode": state.display_mode.value,
        "modes": [mode.value for mode in DisplayMode],
    }


async def _display_mode(request: web.Request) -> web.Response:
    return web.json_response(_display_mode_json(request.app[STATE_KEY]))


async def _set_display_mode(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "the body must be a JSON object"},
                                 status=400)
    raw = body.get("mode") if isinstance(body, dict) else None
    try:
        mode = DisplayMode(raw)
    except ValueError:
        allowed = ", ".join(m.value for m in DisplayMode)
        return web.json_response(
            {"error": f"mode must be one of {allowed}; got {raw!r}"}, status=400)

    state.display_mode = mode
    await state.storage.set_setting(DISPLAY_MODE_SETTING, mode.value)
    # Push the new state so every open page's dropdown - including the one
    # that didn't make the change - follows along without a reload.
    await broadcast_state(request.app, state)
    return web.json_response(_display_mode_json(state))


def _limit(request: web.Request, default: int = 200) -> int:
    raw = request.query.get("limit", str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"limit must be an integer, got {raw!r}") from exc


async def _ships(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    try:
        limit = _limit(request)
        since = _since(request)
        until = _until(request)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    rows = await state.storage.query_ships(
        since=since,
        until=until,
        category=request.query.get("category"),
        limit=limit,
    )
    return web.json_response({"ships": rows})


async def _events(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    try:
        limit = _limit(request)
        since = _since(request)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    rows = await state.storage.query_events(
        since=since,
        level=request.query.get("level"),
        category=request.query.get("category"),
        limit=limit,
    )
    for row in rows:
        if row.get("detail"):
            try:
                row["detail"] = json.loads(row["detail"])
            except ValueError:
                pass
    return web.json_response({"events": rows})


async def _traffic_summary(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    try:
        window = parse_window(request.query.get("window", "7d"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response(await state.storage.traffic_summary(window))


async def _websocket(request: web.Request) -> web.WebSocketResponse:
    state: AppState = request.app[STATE_KEY]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    request.app[WEBSOCKETS_KEY].add(ws)
    try:
        await ws.send_json(state.state_json())
        frame = _frame_json(state)
        if frame is not None:
            await ws.send_json(frame)
        async for message in ws:
            if message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        request.app[WEBSOCKETS_KEY].discard(ws)
    return ws


async def _broadcast(app: web.Application, payload: dict[str, Any]) -> None:
    for ws in list(app[WEBSOCKETS_KEY]):
        if ws.closed:
            continue
        try:
            await ws.send_json(payload)
        except Exception:
            log.debug("dropping a websocket client", exc_info=True)


async def broadcast_frame(app: web.Application, state: AppState) -> None:
    payload = _frame_json(state)
    if payload is not None:
        await _broadcast(app, payload)


async def broadcast_state(app: web.Application, state: AppState) -> None:
    await _broadcast(app, state.state_json())


def create_app(state: AppState) -> web.Application:
    app = web.Application()
    app[STATE_KEY] = state
    app[WEBSOCKETS_KEY] = weakref.WeakSet()
    app.add_routes([
        web.get("/", _index),
        web.get("/debug", _debug),
        web.get("/panel", _panel),
        web.get("/api/panel-audit", _panel_audit),
        web.get("/healthz", _healthz),
        web.get("/api/state", _state),
        web.get("/api/display-mode", _display_mode),
        web.post("/api/display-mode", _set_display_mode),
        web.get("/api/ships", _ships),
        web.get("/api/events", _events),
        web.get("/api/traffic-summary", _traffic_summary),
        web.get("/ws/frames", _websocket),
        web.static("/static", STATIC_DIR),
    ])
    return app
