from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Vessel

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

CREATE TABLE IF NOT EXISTS app_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

_VISIT_COLUMNS = (
    "mmsi", "entered_at", "last_seen", "departed_at", "depart_reason", "name",
    "call_sign", "destination", "ship_type", "category", "priority", "imo",
    "length_m", "beam_m", "draught_m", "eta", "first_lat", "first_lon",
    "last_lat", "last_lon", "max_sog", "last_cog", "last_heading", "nav_status",
    "position_count", "static_resolved", "displayed", "raw_static", "raw_position",
)


_WINDOW_RE = re.compile(r"^(\d+)([smhd])$")
_WINDOW_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def parse_window(raw: str) -> int:
    """Parse `7d`, `48h`, `30m`, `90s` into seconds."""
    match = _WINDOW_RE.match((raw or "").strip().lower())
    if match is None:
        raise ValueError(
            f"window must look like 7d, 48h, 30m or 90s; got {raw!r}"
        )
    return int(match.group(1)) * _WINDOW_UNITS[match.group(2)]


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
        async with self._lock:
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

    async def latest_static(self, mmsi: int) -> dict[str, Any] | None:
        """The most recent visit's over-the-air static payload for a vessel.

        Powers cross-visit seeding: only rows whose payload actually arrived
        this-or-some visit count, so a seeded visit (raw_static NULL) can
        never become the source for another seed.
        """
        rows = await self._fetchall(
            "SELECT raw_static FROM ship_log "
            "WHERE mmsi = ? AND static_resolved = 1 AND raw_static IS NOT NULL "
            "ORDER BY entered_at DESC LIMIT 1", (mmsi,))
        if not rows:
            return None
        try:
            payload = json.loads(rows[0]["raw_static"])
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    async def get_setting(self, key: str) -> str | None:
        """One app-level setting, or None when it was never written."""
        rows = await self._fetchall(
            "SELECT value FROM app_settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    async def set_setting(self, key: str, value: str) -> None:
        """Write a setting, replacing any previous value for the key."""
        await self._execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def log_event(self, level: str, category: str, message: str,
                        detail: dict[str, Any] | None = None) -> None:
        await self._execute(
            "INSERT INTO event_log (ts, level, category, message, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), level, category, message,
             _json(detail)),
        )

    async def prune(self, ship_log_days: int, event_log_hours: int) -> tuple[int, int]:
        """Delete rows past their retention window. Returns (ships, events)."""
        now = datetime.now(timezone.utc)
        ship_cutoff = (now - timedelta(days=ship_log_days)).isoformat()
        event_cutoff = (now - timedelta(hours=event_log_hours)).isoformat()

        ships = await self._execute(
            "DELETE FROM ship_log WHERE entered_at < ?", (ship_cutoff,))
        ships_deleted = ships.rowcount or 0
        events = await self._execute(
            "DELETE FROM event_log WHERE ts < ?", (event_cutoff,))
        events_deleted = events.rowcount or 0

        if ships_deleted or events_deleted:
            await self._execute("PRAGMA incremental_vacuum")
        return ships_deleted, events_deleted

    async def query_ships(self, since: datetime | None = None,
                          until: datetime | None = None,
                          category: str | None = None,
                          limit: int = 200) -> list[dict[str, Any]]:
        clauses, params = [], []
        if since is not None:
            clauses.append("entered_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("entered_at <= ?")
            params.append(until.isoformat())
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 5000)))
        return await self._fetchall(
            f"SELECT * FROM ship_log {where} ORDER BY entered_at DESC LIMIT ?",
            tuple(params),
        )

    async def query_events(self, since: datetime | None = None,
                           level: str | None = None,
                           category: str | None = None,
                           limit: int = 200) -> list[dict[str, Any]]:
        clauses, params = [], []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since.isoformat())
        if level:
            # `level` is a minimum severity, not an exact match.
            minimum = LEVEL_ORDER.get(level.upper())
            if minimum is not None:
                allowed = [name for name, rank in LEVEL_ORDER.items()
                           if rank >= minimum]
                clauses.append(f"level IN ({','.join('?' * len(allowed))})")
                params.extend(allowed)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 5000)))
        return await self._fetchall(
            f"SELECT * FROM event_log {where} ORDER BY ts DESC LIMIT ?",
            tuple(params),
        )

    async def traffic_summary(self, window_seconds: int) -> dict[str, Any]:
        """Aggregates for tuning MIN_LENGTH_METERS and EXCLUDE_CATEGORIES."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=window_seconds)).isoformat()

        total = await self._fetchall(
            "SELECT COUNT(*) AS n FROM ship_log WHERE entered_at >= ?", (cutoff,))
        by_category = await self._fetchall(
            "SELECT category, COUNT(*) AS visits, "
            "       SUM(displayed) AS displayed_visits "
            "FROM ship_log WHERE entered_at >= ? "
            "GROUP BY category ORDER BY visits DESC", (cutoff,))
        histogram = await self._fetchall(
            "SELECT CAST(length_m / 10 AS INTEGER) * 10 AS bucket_m, "
            "       COUNT(*) AS visits "
            "FROM ship_log WHERE entered_at >= ? AND length_m IS NOT NULL "
            "GROUP BY bucket_m ORDER BY bucket_m", (cutoff,))
        by_hour = await self._fetchall(
            "SELECT CAST(strftime('%H', entered_at) AS INTEGER) AS hour, "
            "       COUNT(*) AS visits "
            "FROM ship_log WHERE entered_at >= ? "
            "GROUP BY hour ORDER BY hour", (cutoff,))
        unresolved = await self._fetchall(
            "SELECT COUNT(*) AS n FROM ship_log "
            "WHERE entered_at >= ? AND static_resolved = 0", (cutoff,))

        # Median and p90 dwell are computed in Python: SQLite has no percentile.
        dwell_rows = await self._fetchall(
            "SELECT category, "
            "       (julianday(COALESCE(departed_at, last_seen)) "
            "        - julianday(entered_at)) * 1440.0 AS minutes "
            "FROM ship_log WHERE entered_at >= ?", (cutoff,))
        grouped: dict[str, list[float]] = {}
        for row in dwell_rows:
            if row["minutes"] is not None:
                grouped.setdefault(row["category"], []).append(float(row["minutes"]))
        dwell_by_category = []
        for category, values in sorted(grouped.items()):
            values.sort()
            index = min(len(values) - 1, int(len(values) * 0.9))
            dwell_by_category.append({
                "category": category,
                "visits": len(values),
                "median_minutes": round(statistics.median(values), 1),
                "p90_minutes": round(values[index], 1),
            })

        return {
            "window_seconds": window_seconds,
            "total_visits": total[0]["n"],
            "by_category": by_category,
            "length_histogram": histogram,
            "visits_by_hour": by_hour,
            "dwell_by_category": dwell_by_category,
            "unresolved_static_visits": unresolved[0]["n"],
        }
