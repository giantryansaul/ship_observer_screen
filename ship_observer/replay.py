from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .ais_client import AisMessage
from .config import ConfigError, Settings
from .drivers.null import NullDriver
from .service import Service

log = logging.getLogger(__name__)


def read_session(path: Path) -> Iterator[AisMessage]:
    """Read a RECORD_RAW_PATH JSONL session. Corrupt lines are skipped."""
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                yield AisMessage(
                    message_type=row["message_type"],
                    mmsi=int(row["mmsi"]),
                    received_at=datetime.fromisoformat(row["received_at"]),
                    meta_name=row.get("meta_name"),
                    lat=row.get("lat"),
                    lon=row.get("lon"),
                    payload=row["payload"],
                )
            except (ValueError, KeyError, TypeError):
                log.debug("skipping malformed line %d", number)


class ReplayClient:
    """Drop-in for AisClient that reads from a recorded session.

    Ends the stream when the session is exhausted, which is what lets
    `replay()` terminate instead of running forever.
    """

    def __init__(self, messages: list[AisMessage], speed: float = 0.0) -> None:
        self._messages = messages
        self._speed = speed
        self.connected = True
        self.last_message_at: datetime | None = None
        self.dropped_frames = 0

    async def stream(self) -> AsyncIterator[AisMessage]:
        previous: datetime | None = None
        for message in self._messages:
            if self._speed > 0 and previous is not None:
                gap = (message.received_at - previous).total_seconds()
                if gap > 0:
                    await asyncio.sleep(gap / self._speed)
            previous = message.received_at
            self.last_message_at = message.received_at
            yield message
        self.connected = False


async def replay(path: Path, settings: Settings, speed: float = 0.0) -> dict:
    """Feed a recorded session through the real pipeline with a null driver."""
    messages = list(read_session(path))
    service = Service(settings,
                      driver=NullDriver(settings.panel_width,
                                        settings.panel_height),
                      client=ReplayClient(messages, speed=speed))
    await service.start()
    try:
        await service.ingest_loop()
        # Close every visit so dwell times and departure reasons are complete.
        for vessel in service.registry.close_all():
            await service.storage.end_visit(vessel, "shutdown")

        rows = await service.storage._fetchall(
            "SELECT category, COUNT(*) AS n FROM ship_log GROUP BY category")
        total = await service.storage._fetchall(
            "SELECT COUNT(*) AS n FROM ship_log")
        return {
            "messages": len(messages),
            "visits": total[0]["n"],
            "by_category": {row["category"]: row["n"] for row in rows},
        }
    finally:
        await service.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ship_observer.replay",
        description="Replay a recorded AIS session through the pipeline.")
    parser.add_argument("--file", required=True, type=Path,
                        help="JSONL session written by RECORD_RAW_PATH")
    parser.add_argument("--speed", type=float, default=0.0,
                        help="0 replays as fast as possible; 10 is 10x real time")
    parser.add_argument("--db", type=Path,
                        help="override DB_PATH so the live log is untouched")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2
    if args.db:
        settings = replace_db(settings, args.db)

    result = asyncio.run(replay(args.file, settings, speed=args.speed))
    print(json.dumps(result, indent=2))
    return 0


def replace_db(settings: Settings, db_path: Path) -> Settings:
    import dataclasses
    return dataclasses.replace(settings, db_path=db_path,
                               display_driver="null")


if __name__ == "__main__":
    raise SystemExit(main())
