from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Vessel

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ship_log (
  id              INTEGER PRIMARY KEY,
  mmsi            INTEGER NOT NULL,
  entered_at      TEXT    NOT NULL,
  last_seen       TEXT    NOT NULL,
  departed_at     TEXT,
  depart_reason   TEXT,
  name            TEXT,
  call_sign       TEXT,
  destination     TEXT,
  ship_type       INTEGER,
  category        TEXT,
  priority        INTEGER,
  imo             INTEGER,
  length_m        REAL,
  beam_m          REAL,
  draught_m       REAL,
  eta             TEXT,
  first_lat       REAL,
  first_lon       REAL,
  last_lat        REAL,
  last_lon        REAL,
  max_sog         REAL,
  last_cog        REAL,
  last_heading    INTEGER,
  nav_status      INTEGER,
  position_count  INTEGER NOT NULL DEFAULT 0,
  static_resolved INTEGER NOT NULL DEFAULT 0,
  displayed       INTEGER NOT NULL DEFAULT 0,
  raw_static      TEXT,
  raw_position    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ship_log_entered ON ship_log(entered_at);
CREATE INDEX IF NOT EXISTS idx_ship_log_mmsi    ON ship_log(mmsi, entered_at);
CREATE INDEX IF NOT EXISTS idx_ship_log_cat     ON ship_log(category, entered_at);

CREATE TABLE IF NOT EXISTS event_log (
  id       INTEGER PRIMARY KEY,
  ts       TEXT NOT NULL,
  level    TEXT NOT NULL,
  category TEXT NOT NULL,
  message  TEXT NOT NULL,
  detail   TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_log_ts ON event_log(ts);
"""

_VISIT_COLUMNS = (
    "mmsi", "entered_at", "last_seen", "departed_at", "depart_reason", "name",
    "call_sign", "destination", "ship_type", "category", "priority", "imo",
    "length_m", "beam_m", "draught_m", "eta", "first_lat", "first_lon",
    "last_lat", "last_lon", "max_sog", "last_cog", "last_heading", "nav_status",
    "position_count", "static_resolved", "displayed", "raw_static", "raw_position",
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json(value: Any) -> str | None:
    return json.dumps(value, separators=(",", ":")) if value is not None else None


def _visit_values(vessel: Vessel) -> dict[str, Any]:
    return {
        "mmsi": vessel.mmsi,
        "entered_at": _iso(vessel.entered_at),
        "last_seen": _iso(vessel.last_seen),
        "departed_at": _iso(vessel.departed_at),
        "depart_reason": vessel.depart_reason,
        "name": vessel.name,
        "call_sign": vessel.call_sign,
        "destination": vessel.destination,
        "ship_type": vessel.ship_type,
        "category": vessel.category.value,
        "priority": vessel.priority,
        "imo": vessel.imo,
        "length_m": vessel.length_m,
        "beam_m": vessel.beam_m,
        "draught_m": vessel.draught_m,
        "eta": vessel.eta,
        "first_lat": vessel.first_lat,
        "first_lon": vessel.first_lon,
        "last_lat": vessel.last_lat,
        "last_lon": vessel.last_lon,
        "max_sog": vessel.max_sog,
        "last_cog": vessel.last_cog,
        "last_heading": vessel.last_heading,
        "nav_status": vessel.nav_status,
        "position_count": vessel.position_count,
        "static_resolved": int(vessel.static_resolved),
        "displayed": int(vessel.displayed),
        "raw_static": _json(vessel.raw_static),
        "raw_position": _json(vessel.raw_position),
    }


class Storage:
    """SQLite log. Never on the hot path - all calls hop to a worker thread.

    Write failures are logged and swallowed by the callers in __main__: losing
    a log row must never take down the display.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    async def _execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        async with self._lock:
            return await asyncio.to_thread(self._execute_sync, sql, params)

    def _execute_sync(self, sql: str, params: Any) -> sqlite3.Cursor:
        assert self._conn is not None, "Storage.open() was not awaited"
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor

    async def _fetchall(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._fetchall_sync, sql, params)

    def _fetchall_sync(self, sql: str, params: Any) -> list[dict[str, Any]]:
        assert self._conn is not None, "Storage.open() was not awaited"
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    async def begin_visit(self, vessel: Vessel) -> int:
        """Insert a new ship_log row. One row per visit, not per vessel."""
        values = _visit_values(vessel)
        columns = ", ".join(_VISIT_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in _VISIT_COLUMNS)
        cursor = await self._execute(
            f"INSERT INTO ship_log ({columns}) VALUES ({placeholders})", values
        )
        return int(cursor.lastrowid)

    async def update_visit(self, vessel: Vessel) -> None:
        if vessel.log_id is None:
            return
        values = _visit_values(vessel)
        values["id"] = vessel.log_id
        assignments = ", ".join(f"{c} = :{c}" for c in _VISIT_COLUMNS)
        await self._execute(f"UPDATE ship_log SET {assignments} WHERE id = :id", values)

    async def end_visit(self, vessel: Vessel, reason: str) -> None:
        vessel.depart_reason = reason
        if vessel.departed_at is None:
            vessel.departed_at = datetime.now(timezone.utc)
        await self.update_visit(vessel)

    async def log_event(self, level: str, category: str, message: str,
                        detail: dict[str, Any] | None = None) -> None:
        await self._execute(
            "INSERT INTO event_log (ts, level, category, message, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), level, category, message,
             _json(detail)),
        )
