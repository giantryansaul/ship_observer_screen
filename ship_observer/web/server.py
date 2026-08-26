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
from ..models import Vessel
from ..registry import VesselRegistry
from ..render.layout import capacity
from ..selection import Slots, is_eligible, select_slots
from ..storage import Storage, parse_window

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# AppKey's optional second argument is a *type*; AppState is defined below,
# so both keys are declared untyped rather than with a forward reference.
WEBSOCKETS_KEY = web.AppKey("websockets")
STATE_KEY = web.AppKey("state")


@dataclass
class AppState:
    """Everything the web layer reads. Mutated by __main__'s tasks."""

    settings: Settings
    registry: VesselRegistry
    storage: Storage
    latest_frame: bytes | None = None
    connected: bool = False
    last_message_at: datetime | None = None
    slots: Slots = field(default_factory=Slots)
    dropped_frames: int = 0
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))

    def filtered_reason(self, vessel: Vessel) -> str | None:
        s = self.settings
        if not vessel.static_resolved:
            return None
        if vessel.category in s.exclude_categories:
            return f"excluded_category:{vessel.category.value}"
        if s.min_length_meters > 0 and (
                vessel.length_m is None or vessel.length_m < s.min_length_meters):
            return f"min_length:{s.min_length_meters}m"
        return None

    def vessel_json(self, vessel: Vessel, slot: int | None) -> dict[str, Any]:
        return {
            "mmsi": vessel.mmsi,
            "name": vessel.name,
            "display_name": vessel.display_name,
            "call_sign": vessel.call_sign,
            "destination": vessel.destination,
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

    def state_json(self) -> dict[str, Any]:
        live = self.registry.live()
        departed = self.registry.departed()
        slots = select_slots(live, departed, self.settings,
                             capacity(self.settings.panel_height))
        slot_index = {v.mmsi: i for i, v in enumerate(slots.live)}
        history_index = {v.mmsi: i for i, v in enumerate(slots.history)}
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
            "capacity": capacity(self.settings.panel_height),
            "show_divider": slots.show_divider,
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
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


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


async def _ships(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    rows = await state.storage.query_ships(
        since=_since(request),
        category=request.query.get("category"),
        limit=int(request.query.get("limit", 200)),
    )
    return web.json_response({"ships": rows})


async def _events(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    rows = await state.storage.query_events(
        since=_since(request),
        level=request.query.get("level"),
        category=request.query.get("category"),
        limit=int(request.query.get("limit", 200)),
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
        web.get("/healthz", _healthz),
        web.get("/api/state", _state),
        web.get("/api/ships", _ships),
        web.get("/api/events", _events),
        web.get("/api/traffic-summary", _traffic_summary),
        web.get("/ws/frames", _websocket),
        web.static("/static", STATIC_DIR),
    ])
    return app
