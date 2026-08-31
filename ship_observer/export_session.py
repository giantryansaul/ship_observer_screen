"""Rebuild a replayable JSONL session from a ship_log database.

The pipeline can only record a true session when RECORD_RAW_PATH is set. When
it was not - like the first weekend run - the ship_log still holds each
visit's time span, entry/exit coordinates and last raw payloads. This module
turns those rows back into a message stream that drives the pipeline through
the same visits the live run saw.

Reconstruction, not a recording: positions are synthesized on a fixed
interval between the recorded entry and exit points, and static data is
replayed shortly after entry rather than whenever it actually arrived.

    python -m ship_observer.export_session --db ships.db --out session.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Comfortably inside SHIP_TIMEOUT_SECONDS (900), so a rebuilt visit never
# splits into several on replay.
POSITION_INTERVAL_S = 600.0
STATIC_DELAY_S = 60.0


def _visit_messages(row: sqlite3.Row) -> list[dict]:
    entered = datetime.fromisoformat(row["entered_at"])
    last_seen = datetime.fromisoformat(row["last_seen"])
    span = max(0.0, (last_seen - entered).total_seconds())

    payload = json.loads(row["raw_position"]) if row["raw_position"] else {"Sog": 0.0}
    steps = max(1, int(span // POSITION_INTERVAL_S) + 1)

    def _at(fraction: float) -> tuple[float | None, float | None]:
        """Linear sweep from the recorded entry point to the recorded exit."""
        lat0, lon0 = row["first_lat"], row["first_lon"]
        lat1, lon1 = row["last_lat"], row["last_lon"]
        if None in (lat0, lon0, lat1, lon1):
            return lat0 if lat0 is not None else lat1, \
                   lon0 if lon0 is not None else lon1
        return lat0 + (lat1 - lat0) * fraction, lon0 + (lon1 - lon0) * fraction

    messages = []
    for step in range(steps + 1):
        fraction = step / steps if span else 1.0
        lat, lon = _at(fraction)
        messages.append({
            "received_at": (entered + timedelta(seconds=span * fraction)).isoformat(),
            "message_type": "PositionReport",
            "mmsi": row["mmsi"],
            "meta_name": row["name"],
            "lat": lat, "lon": lon,
            "payload": payload,
        })
        if span == 0:
            break

    if row["raw_static"]:
        lat, lon = _at(min(1.0, STATIC_DELAY_S / span) if span else 1.0)
        messages.append({
            "received_at": (entered + timedelta(
                seconds=min(STATIC_DELAY_S, span))).isoformat(),
            "message_type": "ShipStaticData",
            "mmsi": row["mmsi"],
            "meta_name": row["name"],
            "lat": lat, "lon": lon,
            "payload": json.loads(row["raw_static"]),
        })
    return messages


def export_session(db_path: Path, out_path: Path) -> int:
    """Write every ship_log visit as a chronological JSONL session.

    Returns the number of messages written.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM ship_log ORDER BY entered_at").fetchall()
    finally:
        conn.close()

    messages: list[dict] = []
    for row in rows:
        messages.extend(_visit_messages(row))
    # Stable tie-break keeps the export deterministic for identical timestamps.
    messages.sort(key=lambda m: (m["received_at"], m["mmsi"]))

    with Path(out_path).open("w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(json.dumps(message, separators=(",", ":")) + "\n")
    return len(messages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ship_observer.export_session",
        description="Rebuild a replayable JSONL session from a ship_log DB.")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    count = export_session(args.db, args.out)
    print(f"wrote {count} messages to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
