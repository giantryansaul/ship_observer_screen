import asyncio
import json
import logging
import random
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import websockets

from .config import Settings

POSITION_REPORT = "PositionReport"
SHIP_STATIC_DATA = "ShipStaticData"
SUBSCRIBED_TYPES = (POSITION_REPORT, SHIP_STATIC_DATA)

# AISStream emits Go's default time format: nanosecond precision plus a
# trailing zone label. datetime.strptime handles at most 6 fractional digits,
# so the fraction is truncated before parsing.
_TIME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?"
)


def parse_time_utc(raw: str | None) -> datetime | None:
    """Parse AISStream's `2026-08-26 17:04:11.123456789 +0000 UTC` format.

    Returns None rather than raising: a bad timestamp must never kill the
    consumer, and callers fall back to arrival time.
    """
    if not raw or not isinstance(raw, str):
        return None
    match = _TIME_RE.match(raw.strip())
    if match is None:
        return None
    micros = (match.group("frac") or "")[:6].ljust(6, "0")
    try:
        return datetime.strptime(
            f"{match.group('date')} {match.group('time')}.{micros}",
            "%Y-%m-%d %H:%M:%S.%f",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _clean(value: Any) -> str | None:
    """AIS pads text fields to fixed width with spaces and sometimes '@'."""
    if not isinstance(value, str):
        return None
    stripped = value.replace("@", " ").strip()
    return stripped or None


@dataclass(frozen=True)
class AisMessage:
    message_type: str
    mmsi: int
    received_at: datetime
    meta_name: str | None
    lat: float | None
    lon: float | None
    payload: dict[str, Any]


def parse_envelope(envelope: Any, received_at: datetime) -> AisMessage | None:
    """Convert one AISStream envelope into an AisMessage.

    Returns None for anything malformed or unsubscribed. Never raises - one bad
    message must not take down the stream.
    """
    if not isinstance(envelope, dict):
        return None
    message_type = envelope.get("MessageType")
    if message_type not in SUBSCRIBED_TYPES:
        return None

    meta = envelope.get("MetaData")
    if not isinstance(meta, dict):
        return None
    try:
        mmsi = int(meta["MMSI"])
    except (KeyError, TypeError, ValueError, OverflowError):
        # OverflowError matters: json.loads accepts bare Infinity/-Infinity
        # tokens by default, and int(float('inf')) raises. Without it a single
        # corrupt frame escapes this function, and the transport half would
        # misread that as a disconnect and reconnect on every such frame.
        return None

    message = envelope.get("Message")
    if not isinstance(message, dict):
        return None
    payload = message.get(message_type)
    if not isinstance(payload, dict) or not payload:
        return None

    def _coord(key: str) -> float | None:
        value = meta.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    return AisMessage(
        message_type=message_type,
        mmsi=mmsi,
        received_at=parse_time_utc(meta.get("time_utc")) or received_at,
        meta_name=_clean(meta.get("ShipName")),
        lat=_coord("latitude"),
        lon=_coord("longitude"),
        payload=payload,
    )


log = logging.getLogger(__name__)

AIS_STREAM_URL = "wss://stream.aisstream.io/v0/stream"

EventCallback = Callable[[str, str, str, "dict | None"], None]


def build_subscription(settings: Settings) -> dict[str, Any]:
    """AISStream accepts only these four keys. There is no ship-type filter,
    which is why all type filtering happens locally.
    """
    return {
        "APIKey": settings.ais_stream_api_key,
        "BoundingBoxes": settings.bbox.to_aisstream(),
        "FilterMessageTypes": list(SUBSCRIBED_TYPES),
    }


class AisClient:
    def __init__(
        self,
        settings: Settings,
        on_event: EventCallback | None = None,
        connect: Callable[..., Any] | None = None,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
    ) -> None:
        self._settings = settings
        self._on_event = on_event or (lambda *args: None)
        self._connect = connect or websockets.connect
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._attempt = 0
        self._connected = False
        self._last_message_at: datetime | None = None
        self.dropped_frames = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_message_at(self) -> datetime | None:
        return self._last_message_at

    def _next_backoff(self) -> float:
        """Exponential with full jitter, capped. Jitter avoids a thundering
        herd if the service and the upstream restart together.
        """
        delay = min(self._backoff_base * (2 ** self._attempt), self._backoff_max)
        self._attempt += 1
        return delay * (0.5 + random.random() * 0.5) if delay else 0.0

    def _reset_backoff(self) -> None:
        self._attempt = 0

    def _event(self, level: str, message: str, detail: dict | None = None) -> None:
        self._on_event(level, "ws", message, detail)

    async def stream(self) -> AsyncIterator[AisMessage]:
        """Yield AisMessages forever, reconnecting as needed.

        Never raises for network problems; the caller can treat this as an
        infinite source. Cancellation propagates normally.
        """
        while True:
            try:
                async with self._connect(
                    AIS_STREAM_URL, ping_interval=20, ping_timeout=20
                ) as socket:
                    # AISStream drops the connection if the subscription does
                    # not arrive within 3 seconds.
                    await socket.send(json.dumps(build_subscription(self._settings)))
                    self._connected = True
                    self._reset_backoff()
                    self._event("INFO", "connected and subscribed", {
                        "bbox": self._settings.bbox.to_aisstream(),
                    })

                    async for frame in socket:
                        message = self._decode(frame)
                        if message is None:
                            continue
                        self._last_message_at = message.received_at
                        yield message
            except asyncio.CancelledError:
                self._connected = False
                raise
            except Exception as exc:
                self._connected = False
                self._event("WARN", f"disconnected: {type(exc).__name__}: {exc}")
            else:
                self._connected = False
                self._event("WARN", "disconnected: stream ended")

            delay = self._next_backoff()
            self._event("INFO", f"reconnecting in {delay:.1f}s",
                        {"attempt": self._attempt})
            if delay:
                await asyncio.sleep(delay)

    def _decode(self, frame: Any) -> AisMessage | None:
        try:
            envelope = json.loads(frame)
        except (TypeError, ValueError):
            self.dropped_frames += 1
            log.debug("undecodable frame: %r", frame)
            return None
        message = parse_envelope(envelope, datetime.now(timezone.utc))
        if message is None:
            self.dropped_frames += 1
            log.debug("unparseable envelope: %r", envelope)
        return message
