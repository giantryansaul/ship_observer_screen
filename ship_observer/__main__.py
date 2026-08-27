from __future__ import annotations

import asyncio
import logging
import signal
import sys

from dotenv import load_dotenv

from .config import ConfigError, Settings
from .service import Service


async def _run() -> int:
    load_dotenv()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        # Fail fast with a specific message rather than crash-looping.
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    service = Service(settings)
    task = asyncio.create_task(service.run())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)

    try:
        await task
    except asyncio.CancelledError:
        logging.getLogger(__name__).info("shutting down")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
