from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import BoundingBox, ShipCategory
from .shiptypes import parse_category_names

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

VALID_DRIVERS = ("auto", "rgbmatrix", "null")


class ConfigError(Exception):
    """Raised for any invalid or missing configuration. Fatal at startup."""


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required but was not set")
    return value


def _int(env: Mapping[str, str], name: str, default: int,
         minimum: int | None = None, maximum: int | None = None) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}, got {value}")
    return value


def _float(env: Mapping[str, str], name: str, default: float,
           minimum: float | None = None) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ConfigError(
        f"{name} must be one of {sorted(_TRUE | _FALSE - {''})}, got {raw!r}"
    )


@dataclass(frozen=True)
class Settings:
    ais_stream_api_key: str = field(repr=False)
    bbox: BoundingBox

    ship_log_days: int = 7
    event_log_hours: int = 48
    display_history: bool = False
    ship_timeout_seconds: int = 900
    max_ships: int = 3
    min_length_meters: float = 0.0
    exclude_categories: frozenset[ShipCategory] = frozenset()
    priority_selection: bool = True
    stale_seconds: int = 120

    render_fps: int = 15
    web_fps: int = 10

    display_driver: str = "auto"
    matrix_rows: int = 64
    matrix_cols: int = 64
    matrix_chain: int = 1
    matrix_parallel: int = 1
    matrix_brightness: int = 60
    matrix_gpio_slowdown: int = 4
    matrix_hardware_mapping: str = "adafruit-hat-pwm"

    http_host: str = "0.0.0.0"
    http_port: int = 8080
    db_path: Path = Path("/var/lib/ship-observer/ships.db")
    record_raw_path: Path | None = None
    log_level: str = "INFO"

    @property
    def panel_width(self) -> int:
        return self.matrix_cols * self.matrix_chain

    @property
    def panel_height(self) -> int:
        return self.matrix_rows * self.matrix_parallel

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env

        api_key = _require(env, "AIS_STREAM_API_KEY")
        bbox_raw = _require(env, "BBOX")
        try:
            bbox = BoundingBox.parse(bbox_raw)
        except ValueError as exc:
            raise ConfigError(f"BBOX is invalid: {exc}") from exc

        try:
            exclude = parse_category_names(env.get("EXCLUDE_CATEGORIES", ""))
        except ValueError as exc:
            raise ConfigError(f"EXCLUDE_CATEGORIES is invalid: {exc}") from exc

        driver = env.get("DISPLAY_DRIVER", "auto").strip().lower() or "auto"
        if driver not in VALID_DRIVERS:
            raise ConfigError(
                f"DISPLAY_DRIVER must be one of {VALID_DRIVERS}, got {driver!r}"
            )

        record_raw = env.get("RECORD_RAW_PATH", "").strip()

        return cls(
            ais_stream_api_key=api_key,
            bbox=bbox,
            ship_log_days=_int(env, "SHIP_LOG_DAYS", 7, minimum=1),
            event_log_hours=_int(env, "EVENT_LOG_HOURS", 48, minimum=1),
            display_history=_bool(env, "DISPLAY_HISTORY", False),
            ship_timeout_seconds=_int(env, "SHIP_TIMEOUT_SECONDS", 900, minimum=30),
            max_ships=_int(env, "MAX_SHIPS", 3, minimum=1),
            min_length_meters=_float(env, "MIN_LENGTH_METERS", 0.0, minimum=0.0),
            exclude_categories=exclude,
            priority_selection=_bool(env, "PRIORITY_SELECTION", True),
            stale_seconds=_int(env, "STALE_SECONDS", 120, minimum=10),
            render_fps=_int(env, "RENDER_FPS", 15, minimum=1, maximum=60),
            web_fps=_int(env, "WEB_FPS", 10, minimum=1, maximum=60),
            display_driver=driver,
            matrix_rows=_int(env, "MATRIX_ROWS", 64, minimum=8),
            matrix_cols=_int(env, "MATRIX_COLS", 64, minimum=8),
            matrix_chain=_int(env, "MATRIX_CHAIN", 1, minimum=1),
            matrix_parallel=_int(env, "MATRIX_PARALLEL", 1, minimum=1),
            matrix_brightness=_int(env, "MATRIX_BRIGHTNESS", 60, minimum=1, maximum=100),
            matrix_gpio_slowdown=_int(env, "MATRIX_GPIO_SLOWDOWN", 4, minimum=0, maximum=5),
            matrix_hardware_mapping=env.get(
                "MATRIX_HARDWARE_MAPPING", "adafruit-hat-pwm"
            ).strip() or "adafruit-hat-pwm",
            http_host=env.get("HTTP_HOST", "0.0.0.0").strip() or "0.0.0.0",
            http_port=_int(env, "HTTP_PORT", 8080, minimum=1, maximum=65535),
            db_path=Path(
                env.get("DB_PATH", "/var/lib/ship-observer/ships.db").strip()
                or "/var/lib/ship-observer/ships.db"
            ),
            record_raw_path=Path(record_raw) if record_raw else None,
            log_level=env.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )

    def redacted(self) -> dict[str, Any]:
        """Safe for the web boundary. The API key never crosses it."""
        return {
            "ais_stream_api_key": "***redacted***",
            "bbox": {
                "lon_min": self.bbox.lon_min,
                "lat_min": self.bbox.lat_min,
                "lon_max": self.bbox.lon_max,
                "lat_max": self.bbox.lat_max,
            },
            "ship_log_days": self.ship_log_days,
            "event_log_hours": self.event_log_hours,
            "display_history": self.display_history,
            "ship_timeout_seconds": self.ship_timeout_seconds,
            "max_ships": self.max_ships,
            "min_length_meters": self.min_length_meters,
            "exclude_categories": sorted(c.value for c in self.exclude_categories),
            "priority_selection": self.priority_selection,
            "stale_seconds": self.stale_seconds,
            "render_fps": self.render_fps,
            "web_fps": self.web_fps,
            "display_driver": self.display_driver,
            "panel": {"width": self.panel_width, "height": self.panel_height},
            "matrix_brightness": self.matrix_brightness,
            "matrix_hardware_mapping": self.matrix_hardware_mapping,
            "db_path": str(self.db_path),
            "record_raw_path": str(self.record_raw_path) if self.record_raw_path else None,
            "log_level": self.log_level,
        }
