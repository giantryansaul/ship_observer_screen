from __future__ import annotations

import logging
from typing import Callable

from ..config import Settings
from .base import DisplayDriver
from .null import NullDriver

log = logging.getLogger(__name__)

EventCallback = Callable[[str, str, str, "dict | None"], None]


def create_driver(settings: Settings,
                  on_event: EventCallback | None = None) -> DisplayDriver:
    """Build the configured driver, falling back to NullDriver on failure.

    A panel that will not initialise must not stop the service: the web UI is
    how that failure gets diagnosed remotely.
    """
    emit = on_event or (lambda *args: None)
    width, height = settings.panel_width, settings.panel_height

    if settings.display_driver == "null":
        return NullDriver(width, height)

    try:
        from .rgbmatrix import RgbMatrixDriver

        driver = RgbMatrixDriver(settings)
        emit("INFO", "display", "rgbmatrix driver initialised",
             {"width": width, "height": height})
        return driver
    except Exception as exc:
        message = f"rgbmatrix unavailable ({type(exc).__name__}: {exc})"
        if settings.display_driver == "rgbmatrix":
            log.error("%s - falling back to the null driver", message)
            emit("ERROR", "display", f"{message}; falling back to null driver", None)
        else:
            log.info("%s - using the null driver", message)
            emit("INFO", "display", f"{message}; using the null driver", None)
        return NullDriver(width, height)
