"""An in-process stand-in for AISStream's websocket endpoint.

Rather than binding a real port, this fakes the `connect()` callable that
AisClient accepts, which keeps the tests fast and hermetic.
"""
from __future__ import annotations

import asyncio
import json


class _Connection:
    def __init__(self, server):
        self._server = server
        self._frames = list(server.frames)
        self._drop = server.drop_next
        server.drop_next = False

    async def send(self, payload):
        self._server.subscriptions.append(json.loads(payload))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        if self._drop:
            raise ConnectionError("connection reset by peer")
        # Idle forever so the client does not busy-reconnect.
        await asyncio.sleep(3600)
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeAisStream:
    def __init__(self, frames: list[str] | None = None) -> None:
        self.frames = frames or []
        self.subscriptions: list[dict] = []
        self.connections = 0
        self.drop_next = False

    def connect(self, url, **kwargs):
        self.connections += 1
        return _Connection(self)

    def drop_next_connection(self) -> None:
        self.drop_next = True


def envelope(mmsi, message_type="PositionReport", time_utc=None, **payload):
    """Build an AISStream envelope in the exact wire format."""
    time_utc = time_utc or "2026-08-26 17:04:11.123456789 +0000 UTC"
    meta = {"MMSI": mmsi, "ShipName": payload.pop("_name", "TEST SHIP"),
            "latitude": payload.pop("_lat", 47.88),
            "longitude": payload.pop("_lon", -122.41),
            "time_utc": time_utc}
    return json.dumps({"MessageType": message_type, "MetaData": meta,
                       "Message": {message_type: payload}})
