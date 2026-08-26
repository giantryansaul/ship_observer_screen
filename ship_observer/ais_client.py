from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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
