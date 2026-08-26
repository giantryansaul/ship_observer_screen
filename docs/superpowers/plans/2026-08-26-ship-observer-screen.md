# Ship Observer Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Raspberry Pi microservice that streams live AIS vessel data from AISStream.io, filters it to a configurable bounding box, displays up to three prioritized ships on a 64x64 HUB75 LED matrix, and serves an HTML debug page backed by rolling SQLite logs.

**Architecture:** A single `asyncio` process under one systemd unit. Five concurrent tasks (AIS websocket consumer, render loop, aiohttp web server, registry pruner, retention pruner) share an in-memory `VesselRegistry`. SQLite is the durable log and never sits in the hot path. The HUB75 driver runs on its own thread behind a single-slot latest-frame buffer, because `SwapOnVSync()` blocks and would otherwise stall the event loop.

**Tech Stack:** Python 3.11.13 (pyenv), `websockets`, `aiohttp`, `python-dotenv`, `pytest` + `pytest-asyncio`, SQLite (stdlib `sqlite3`, WAL mode), `rpi-rgb-led-matrix` (built from source on the Pi, not on PyPI).

**Spec:** `docs/superpowers/specs/2026-08-26-ship-observer-design.md`

## Global Constraints

These apply to every task. Do not restate them per-task; they are always in force.

- **Python 3.11.13** via pyenv virtualenv named `ship_observer`, pinned by `.python-version`. No syntax newer than 3.11.
- **Every module except `ship_observer/drivers/rgbmatrix.py` must import and run on a non-Pi host.** Development happens on WSL2. Never import `rgbmatrix` at module scope anywhere else.
- **Package name is `ship_observer`** (underscore). systemd unit, DB directory, and install paths use `ship-observer` (hyphen).
- **`BBOX` is longitude-first:** `lon_min,lat_min,lon_max,lat_max`. AISStream wants latitude-first corner pairs. The conversion lives in exactly one place, `BoundingBox.to_aisstream()`.
- **Panel geometry is fixed by arithmetic:** `BLOCK_H = 19`, `DIVIDER_H = 7`. Three blocks plus a divider is exactly 64 px. Do not change these without redoing Section 8.1 of the spec.
- **The log records every vessel, always.** Display filters (`MIN_LENGTH_METERS`, `EXCLUDE_CATEGORIES`, priority selection) affect only what reaches the panel — never what reaches `ship_log`.
- **Vessels with unresolved static data are never hard-gated.** `ShipStaticData` takes up to six minutes; gating on it would blank a cargo ship for its first six minutes in the box.
- **The API key is never logged and never serialized.** `Settings.redacted()` is the only thing that may cross the web boundary.
- **All timestamps are timezone-aware UTC `datetime`**, stored as ISO8601 strings. Never use naive datetimes.
- **TDD is mandatory:** write the failing test, run it, watch it fail, implement minimally, watch it pass, commit. Every task follows this cycle.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, pytest config |
| `.python-version` | Pins pyenv virtualenv `ship_observer` |
| `ship_observer/models.py` | `BoundingBox`, `ShipCategory`, `Vessel` — no I/O, no deps |
| `ship_observer/shiptypes.py` | AIS type code -> category -> priority tier |
| `ship_observer/config.py` | `Settings.from_env`, validation, redaction |
| `ship_observer/ais_client.py` | Envelope parsing, websocket stream, reconnect |
| `ship_observer/registry.py` | MMSI merge, prune, departure, visit lifecycle |
| `ship_observer/selection.py` | Priority selection + slot assignment |
| `ship_observer/storage.py` | SQLite schema, visit/event writes, retention, queries |
| `ship_observer/render/canvas.py` | `Bitmap`, `Canvas` framebuffer primitives |
| `ship_observer/render/font.py` | BDF parser + vendored `4x6.bdf` |
| `ship_observer/render/icons.py` | 8x8 category icons as string art |
| `ship_observer/render/scroll.py` | Per-field horizontal scroll state machine |
| `ship_observer/render/layout.py` | Blocks + divider -> canvas |
| `ship_observer/drivers/base.py` | `DisplayDriver` protocol |
| `ship_observer/drivers/null.py` | No-op driver for dev/CI |
| `ship_observer/drivers/rgbmatrix.py` | HUB75 driver + frame thread (Pi only) |
| `ship_observer/web/server.py` | aiohttp routes, websocket push, `AppState` |
| `ship_observer/web/static/` | `index.html`, `app.js`, `style.css` |
| `ship_observer/replay.py` | Replay recorded JSONL through the pipeline |
| `ship_observer/__main__.py` | Wiring, task supervision, signal handling |
| `deploy/ship-observer.service` | systemd unit |
| `deploy/install.sh` | Pi provisioning script |
| `docs/raspberry-pi-setup.md` | Follow-along device runbook |
| `tests/` | Mirrors the package layout |

---

## Task 1: Scaffolding and Domain Models

**Files:**
- Create: `.python-version`, `pyproject.toml`, `ship_observer/__init__.py`, `ship_observer/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `ShipCategory` (str Enum), `BoundingBox` (frozen dataclass with `.parse`, `.contains`, `.to_aisstream`), `Vessel` (mutable dataclass)

- [ ] **Step 1: Create the virtualenv and pin it**

```bash
pyenv virtualenv 3.11.13 ship_observer
echo "ship_observer" > .python-version
pyenv exec python --version   # must print Python 3.11.13
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "ship-observer"
version = "0.1.0"
requires-python = ">=3.11,<3.12"
dependencies = [
    "websockets>=12.0",
    "aiohttp>=3.9",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["ship_observer*"]

[tool.setuptools.package-data]
ship_observer = ["render/fonts/*.bdf", "web/static/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Then: `pip install -e ".[dev]"`

- [ ] **Step 3: Write the failing test**

`tests/test_models.py`:

```python
import pytest
from datetime import datetime, timezone
from ship_observer.models import BoundingBox, ShipCategory, Vessel

BBOX_RAW = "-122.527428,47.859476,-122.323322,47.910359"


def test_parse_reads_longitude_first():
    b = BoundingBox.parse(BBOX_RAW)
    assert b.lon_min == pytest.approx(-122.527428)
    assert b.lat_min == pytest.approx(47.859476)
    assert b.lon_max == pytest.approx(-122.323322)
    assert b.lat_max == pytest.approx(47.910359)


def test_contains_uses_lat_lon_argument_order():
    b = BoundingBox.parse(BBOX_RAW)
    assert b.contains(47.88, -122.40) is True
    assert b.contains(47.80, -122.40) is False   # south of the box
    assert b.contains(47.88, -122.60) is False   # west of the box


def test_to_aisstream_emits_latitude_first_corner_pairs():
    b = BoundingBox.parse(BBOX_RAW)
    assert b.to_aisstream() == [[
        [pytest.approx(47.859476), pytest.approx(-122.527428)],
        [pytest.approx(47.910359), pytest.approx(-122.323322)],
    ]]


@pytest.mark.parametrize("raw", [
    "1,2,3",                       # too few
    "1,2,3,4,5",                   # too many
    "a,b,c,d",                     # not numbers
    "-122.5,47.9,-122.3,47.8",     # lat_min > lat_max
    "-122.3,47.8,-122.5,47.9",     # lon_min > lon_max
    "-122.5,91.0,-122.3,92.0",     # latitude out of range
    "-181.0,47.8,-122.3,47.9",     # longitude out of range
])
def test_parse_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        BoundingBox.parse(raw)


def test_vessel_defaults_to_unknown_category():
    now = datetime.now(timezone.utc)
    v = Vessel(mmsi=366123456, entered_at=now, last_seen=now)
    assert v.category is ShipCategory.UNKNOWN
    assert v.static_resolved is False
    assert v.position_count == 0
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.models'`

- [ ] **Step 5: Implement `ship_observer/models.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ShipCategory(str, Enum):
    """Display categories derived from the AIS ship-and-cargo type code."""

    PASSENGER = "passenger"
    CARGO = "cargo"
    TANKER = "tanker"
    TUG = "tug"
    FISHING = "fishing"
    SAILING = "sailing"
    PLEASURE = "pleasure"
    PATROL = "patrol"
    MILITARY = "military"
    OTHER = "other"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BoundingBox:
    """A geographic box. Field order matches the BBOX env var: longitude first."""

    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float

    @classmethod
    def parse(cls, raw: str) -> "BoundingBox":
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError(
                f"BBOX needs exactly 4 comma-separated values "
                f"(lon_min,lat_min,lon_max,lat_max), got {len(parts)}: {raw!r}"
            )
        try:
            lon_min, lat_min, lon_max, lat_max = (float(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"BBOX values must all be numbers: {raw!r}") from exc

        for name, lat in (("lat_min", lat_min), ("lat_max", lat_max)):
            if not -90.0 <= lat <= 90.0:
                raise ValueError(f"BBOX {name}={lat} is outside [-90, 90]")
        for name, lon in (("lon_min", lon_min), ("lon_max", lon_max)):
            if not -180.0 <= lon <= 180.0:
                raise ValueError(f"BBOX {name}={lon} is outside [-180, 180]")
        if lat_min >= lat_max:
            raise ValueError(
                f"BBOX lat_min ({lat_min}) must be less than lat_max ({lat_max}). "
                "Expected order is lon_min,lat_min,lon_max,lat_max."
            )
        if lon_min >= lon_max:
            raise ValueError(
                f"BBOX lon_min ({lon_min}) must be less than lon_max ({lon_max}). "
                "Expected order is lon_min,lat_min,lon_max,lat_max."
            )
        return cls(lon_min, lat_min, lon_max, lat_max)

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max

    def to_aisstream(self) -> list[list[list[float]]]:
        """AISStream wants [[[lat, lon], [lat, lon]]] — latitude first."""
        return [[[self.lat_min, self.lon_min], [self.lat_max, self.lon_max]]]


@dataclass
class Vessel:
    """One visit by one vessel. A re-entry after departure is a new Vessel."""

    mmsi: int
    entered_at: datetime
    last_seen: datetime

    name: str | None = None
    call_sign: str | None = None
    destination: str | None = None

    ship_type: int | None = None
    category: ShipCategory = ShipCategory.UNKNOWN
    priority: int = 20

    imo: int | None = None
    length_m: float | None = None
    beam_m: float | None = None
    draught_m: float | None = None
    eta: str | None = None

    first_lat: float | None = None
    first_lon: float | None = None
    last_lat: float | None = None
    last_lon: float | None = None
    max_sog: float | None = None
    last_cog: float | None = None
    last_heading: int | None = None
    nav_status: int | None = None

    position_count: int = 0
    static_resolved: bool = False
    displayed: bool = False

    departed_at: datetime | None = None
    depart_reason: str | None = None

    raw_static: dict[str, Any] | None = None
    raw_position: dict[str, Any] | None = None

    log_id: int | None = field(default=None, repr=False)

    @property
    def display_name(self) -> str:
        return self.name or f"MMSI {self.mmsi}"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS (11 tests)

- [ ] **Step 7: Commit**

```bash
git add .python-version pyproject.toml ship_observer/ tests/
git commit -m "feat: project scaffolding and domain models"
```

---

## Task 2: Ship Type Classification and Priority

**Files:**
- Create: `ship_observer/shiptypes.py`
- Test: `tests/test_shiptypes.py`

**Interfaces:**
- Consumes: `ShipCategory` from Task 1
- Produces: `classify(ship_type: int | None) -> ShipCategory`, `priority_for(category: ShipCategory) -> int`, `CATEGORY_PRIORITY: dict[ShipCategory, int]`, `parse_category_names(raw: str) -> frozenset[ShipCategory]`

- [ ] **Step 1: Write the failing test**

`tests/test_shiptypes.py`:

```python
import pytest
from ship_observer.models import ShipCategory
from ship_observer.shiptypes import (
    CATEGORY_PRIORITY,
    classify,
    parse_category_names,
    priority_for,
)


@pytest.mark.parametrize("code,expected", [
    (30, ShipCategory.FISHING),
    (31, ShipCategory.TUG), (32, ShipCategory.TUG), (52, ShipCategory.TUG),
    (35, ShipCategory.MILITARY),
    (36, ShipCategory.SAILING),
    (37, ShipCategory.PLEASURE),
    (51, ShipCategory.PATROL), (55, ShipCategory.PATROL),
    (60, ShipCategory.PASSENGER), (64, ShipCategory.PASSENGER), (69, ShipCategory.PASSENGER),
    (70, ShipCategory.CARGO), (79, ShipCategory.CARGO),
    (80, ShipCategory.TANKER), (89, ShipCategory.TANKER),
    (50, ShipCategory.OTHER), (33, ShipCategory.OTHER), (90, ShipCategory.OTHER),
    (0, ShipCategory.OTHER),
])
def test_classify_boundaries(code, expected):
    assert classify(code) is expected


def test_classify_none_is_unknown():
    """No ShipStaticData yet - distinct from OTHER, which is a resolved type."""
    assert classify(None) is ShipCategory.UNKNOWN


@pytest.mark.parametrize("code", [-1, 100, 999])
def test_classify_out_of_range_is_other(code):
    assert classify(code) is ShipCategory.OTHER


# Written independently of the implementation so it catches a shifted range or a
# dropped exact-code entry, not just a type error.
EXPECTED_BY_CODE = {
    **{c: ShipCategory.OTHER for c in range(0, 30)},
    30: ShipCategory.FISHING,
    31: ShipCategory.TUG,
    32: ShipCategory.TUG,
    33: ShipCategory.OTHER,
    34: ShipCategory.OTHER,
    35: ShipCategory.MILITARY,
    36: ShipCategory.SAILING,
    37: ShipCategory.PLEASURE,
    **{c: ShipCategory.OTHER for c in range(38, 51)},   # includes 50, pilot
    51: ShipCategory.PATROL,
    52: ShipCategory.TUG,
    53: ShipCategory.OTHER,
    54: ShipCategory.OTHER,
    55: ShipCategory.PATROL,
    **{c: ShipCategory.OTHER for c in range(56, 60)},
    **{c: ShipCategory.PASSENGER for c in range(60, 70)},
    **{c: ShipCategory.CARGO for c in range(70, 80)},
    **{c: ShipCategory.TANKER for c in range(80, 90)},
    **{c: ShipCategory.OTHER for c in range(90, 100)},
}


def test_every_code_0_to_99_maps_to_its_expected_category():
    assert len(EXPECTED_BY_CODE) == 100, "the expectation table must cover 0-99"
    assert {code: classify(code) for code in range(100)} == EXPECTED_BY_CODE


def test_every_code_0_to_99_lands_on_a_valid_tier():
    """Spec 13: every code maps to a category *and a tier*."""
    for code in range(100):
        assert priority_for(classify(code)) in {10, 20, 30, 40}


def test_tier_ordering_matches_spec():
    p = priority_for
    tier1 = {ShipCategory.MILITARY, ShipCategory.PASSENGER, ShipCategory.PATROL}
    tier2 = {ShipCategory.CARGO, ShipCategory.TANKER}
    tier4 = {ShipCategory.FISHING, ShipCategory.SAILING, ShipCategory.PLEASURE}
    assert all(p(c) == 40 for c in tier1)
    assert all(p(c) == 30 for c in tier2)
    assert p(ShipCategory.TUG) == 20
    assert all(p(c) == 10 for c in tier4)


def test_unknown_gets_provisional_tier_3():
    """Unresolved vessels must outrank recreational traffic, not sit below it."""
    assert priority_for(ShipCategory.UNKNOWN) == 20
    assert priority_for(ShipCategory.UNKNOWN) > priority_for(ShipCategory.SAILING)


def test_every_category_has_a_priority():
    for category in ShipCategory:
        assert category in CATEGORY_PRIORITY


def test_parse_category_names():
    assert parse_category_names("fishing,sailing") == frozenset(
        {ShipCategory.FISHING, ShipCategory.SAILING}
    )
    assert parse_category_names(" FISHING , Sailing ") == frozenset(
        {ShipCategory.FISHING, ShipCategory.SAILING}
    )
    assert parse_category_names("") == frozenset()


def test_parse_category_names_rejects_unknown_name():
    with pytest.raises(ValueError, match="rowboat"):
        parse_category_names("fishing,rowboat")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shiptypes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.shiptypes'`

- [ ] **Step 3: Implement `ship_observer/shiptypes.py`**

```python
from __future__ import annotations

from .models import ShipCategory

# Exact codes take precedence over ranges.
_EXACT: dict[int, ShipCategory] = {
    30: ShipCategory.FISHING,
    31: ShipCategory.TUG,
    32: ShipCategory.TUG,
    35: ShipCategory.MILITARY,
    36: ShipCategory.SAILING,
    37: ShipCategory.PLEASURE,
    51: ShipCategory.PATROL,
    52: ShipCategory.TUG,
    55: ShipCategory.PATROL,
}

# (low, high) inclusive.
_RANGES: list[tuple[int, int, ShipCategory]] = [
    (60, 69, ShipCategory.PASSENGER),
    (70, 79, ShipCategory.CARGO),
    (80, 89, ShipCategory.TANKER),
]

CATEGORY_PRIORITY: dict[ShipCategory, int] = {
    # Tier 1
    ShipCategory.MILITARY: 40,
    ShipCategory.PASSENGER: 40,
    ShipCategory.PATROL: 40,
    # Tier 2
    ShipCategory.CARGO: 30,
    ShipCategory.TANKER: 30,
    # Tier 3
    ShipCategory.TUG: 20,
    ShipCategory.OTHER: 20,
    # Unresolved static data gets a provisional Tier 3 slot so a cargo ship
    # is not invisible for its first six minutes in the box.
    ShipCategory.UNKNOWN: 20,
    # Tier 4
    ShipCategory.FISHING: 10,
    ShipCategory.SAILING: 10,
    ShipCategory.PLEASURE: 10,
}


def classify(ship_type: int | None) -> ShipCategory:
    """Map an AIS ship-and-cargo type code to a display category.

    None means ShipStaticData has not arrived yet, which is materially
    different from a resolved-but-uninteresting type.
    """
    if ship_type is None:
        return ShipCategory.UNKNOWN
    if ship_type in _EXACT:
        return _EXACT[ship_type]
    for low, high, category in _RANGES:
        if low <= ship_type <= high:
            return category
    return ShipCategory.OTHER


def priority_for(category: ShipCategory) -> int:
    return CATEGORY_PRIORITY[category]


def parse_category_names(raw: str) -> frozenset[ShipCategory]:
    """Parse a comma-separated EXCLUDE_CATEGORIES value."""
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    valid = {c.value for c in ShipCategory}
    unknown = [n for n in names if n not in valid]
    if unknown:
        raise ValueError(
            f"Unknown ship categories: {', '.join(unknown)}. "
            f"Valid values: {', '.join(sorted(valid))}"
        )
    return frozenset(ShipCategory(n) for n in names)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_shiptypes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/shiptypes.py tests/test_shiptypes.py
git commit -m "feat: AIS ship type classification and priority tiers"
```

---

## Task 3: Configuration

**Files:**
- Create: `ship_observer/config.py`, `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `BoundingBox`, `ShipCategory` (Task 1); `parse_category_names` (Task 2)
- Produces: `Settings` (frozen dataclass), `Settings.from_env(env: Mapping[str, str]) -> Settings`, `Settings.redacted() -> dict`, `ConfigError`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
import pytest
from ship_observer.config import ConfigError, Settings
from ship_observer.models import ShipCategory

MINIMAL = {
    "AIS_STREAM_API_KEY": "secret-key-value",
    "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
}


def test_defaults_match_spec():
    s = Settings.from_env(MINIMAL)
    assert s.ship_log_days == 7
    assert s.event_log_hours == 48
    assert s.display_history is False
    assert s.ship_timeout_seconds == 900
    assert s.max_ships == 3
    assert s.min_length_meters == 0.0
    assert s.exclude_categories == frozenset()
    assert s.priority_selection is True
    assert s.stale_seconds == 120
    assert s.render_fps == 15
    assert s.web_fps == 10
    assert s.display_driver == "auto"
    assert s.matrix_rows == 64 and s.matrix_cols == 64
    assert s.http_port == 8080
    assert s.record_raw_path is None


@pytest.mark.parametrize("missing", ["AIS_STREAM_API_KEY", "BBOX"])
def test_required_variables(missing):
    env = {k: v for k, v in MINIMAL.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        Settings.from_env(env)


def test_bad_bbox_raises_config_error_naming_the_variable():
    with pytest.raises(ConfigError, match="BBOX"):
        Settings.from_env({**MINIMAL, "BBOX": "1,2,3"})


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("no", False), ("off", False), ("", False),
])
def test_boolean_parsing(raw, expected):
    s = Settings.from_env({**MINIMAL, "DISPLAY_HISTORY": raw})
    assert s.display_history is expected


def test_exclude_categories_parsed():
    s = Settings.from_env({**MINIMAL, "EXCLUDE_CATEGORIES": "fishing,sailing"})
    assert s.exclude_categories == frozenset(
        {ShipCategory.FISHING, ShipCategory.SAILING}
    )


def test_bad_exclude_category_raises_config_error():
    with pytest.raises(ConfigError, match="EXCLUDE_CATEGORIES"):
        Settings.from_env({**MINIMAL, "EXCLUDE_CATEGORIES": "kayak"})


@pytest.mark.parametrize("var,value", [
    ("SHIP_LOG_DAYS", "0"),
    ("EVENT_LOG_HOURS", "-1"),
    ("MAX_SHIPS", "0"),
    ("RENDER_FPS", "0"),
    ("MATRIX_BRIGHTNESS", "101"),
    ("HTTP_PORT", "70000"),
    ("SHIP_LOG_DAYS", "seven"),
    ("MIN_LENGTH_METERS", "-5"),
    ("DISPLAY_DRIVER", "oled"),
])
def test_invalid_values_rejected(var, value):
    with pytest.raises(ConfigError, match=var):
        Settings.from_env({**MINIMAL, var: value})


def test_redacted_never_exposes_the_api_key():
    s = Settings.from_env(MINIMAL)
    blob = s.redacted()
    assert "secret-key-value" not in repr(blob)
    assert blob["ais_stream_api_key"] == "***redacted***"
    # The parsed corners are surfaced so a transposed BBOX paste is visible.
    assert blob["bbox"] == {
        "lon_min": -122.527428, "lat_min": 47.859476,
        "lon_max": -122.323322, "lat_max": 47.910359,
    }


def test_repr_does_not_leak_the_key():
    s = Settings.from_env(MINIMAL)
    assert "secret-key-value" not in repr(s)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.config'`

- [ ] **Step 3: Implement `ship_observer/config.py`**

```python
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
```

Note: `field(repr=False)` on `ais_stream_api_key` is what keeps the key out of tracebacks and log lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Write `.env.example`**

```bash
# Required
AIS_STREAM_API_KEY=your-key-here
# lon_min,lat_min,lon_max,lat_max  (bboxfinder.com order)
BBOX=-122.527428,47.859476,-122.323322,47.910359

# Retention
SHIP_LOG_DAYS=7
EVENT_LOG_HOURS=48

# Display behaviour
DISPLAY_HISTORY=false
SHIP_TIMEOUT_SECONDS=900
MAX_SHIPS=3
STALE_SECONDS=120

# Noise control - start permissive, tune from /api/traffic-summary after a week
MIN_LENGTH_METERS=0
EXCLUDE_CATEGORIES=
PRIORITY_SELECTION=true

# Panel
DISPLAY_DRIVER=auto
MATRIX_ROWS=64
MATRIX_COLS=64
MATRIX_BRIGHTNESS=60
MATRIX_GPIO_SLOWDOWN=4
MATRIX_HARDWARE_MAPPING=adafruit-hat-pwm
RENDER_FPS=15
WEB_FPS=10

# Service
HTTP_HOST=0.0.0.0
HTTP_PORT=8080
DB_PATH=/var/lib/ship-observer/ships.db
RECORD_RAW_PATH=
LOG_LEVEL=INFO
```

- [ ] **Step 6: Commit**

```bash
git add ship_observer/config.py tests/test_config.py .env.example
git commit -m "feat: environment configuration with fail-fast validation"
```

---

## Task 4: AIS Envelope Parsing

Parsing is separated from transport so it can be tested without a socket. This is the task that handles AISStream's Go-formatted timestamps and the fact that position and identity arrive in different message types.

**Files:**
- Create: `ship_observer/ais_client.py` (parsing half only)
- Test: `tests/test_ais_parsing.py`

**Interfaces:**
- Consumes: nothing beyond stdlib
- Produces: `AisMessage` (frozen dataclass: `message_type`, `mmsi`, `received_at`, `meta_name`, `lat`, `lon`, `payload`), `parse_time_utc(raw: str) -> datetime | None`, `parse_envelope(envelope: dict, received_at: datetime) -> AisMessage | None`, `POSITION_REPORT`, `SHIP_STATIC_DATA` constants

- [ ] **Step 1: Write the failing test**

`tests/test_ais_parsing.py`:

```python
from datetime import datetime, timezone

import pytest

from ship_observer.ais_client import (
    POSITION_REPORT,
    SHIP_STATIC_DATA,
    AisMessage,
    parse_envelope,
    parse_time_utc,
)

NOW = datetime(2026, 8, 26, 17, 5, 0, tzinfo=timezone.utc)

POSITION_ENVELOPE = {
    "MessageType": "PositionReport",
    "MetaData": {
        "MMSI": 366123456,
        "ShipName": "POLAR RESOLUTE       ",
        "latitude": 47.8801,
        "longitude": -122.4102,
        "time_utc": "2026-08-26 17:04:11.123456789 +0000 UTC",
    },
    "Message": {
        "PositionReport": {
            "UserID": 366123456,
            "Latitude": 47.8801,
            "Longitude": -122.4102,
            "Sog": 12.4,
            "Cog": 176.2,
            "TrueHeading": 175,
            "NavigationalStatus": 0,
        }
    },
}

STATIC_ENVELOPE = {
    "MessageType": "ShipStaticData",
    "MetaData": {
        "MMSI": 366123456,
        "ShipName": "POLAR RESOLUTE       ",
        "latitude": 47.8801,
        "longitude": -122.4102,
        "time_utc": "2026-08-26 17:04:20.000000000 +0000 UTC",
    },
    "Message": {
        "ShipStaticData": {
            "UserID": 366123456,
            "Name": "POLAR RESOLUTE       ",
            "CallSign": "WCX8834  ",
            "Destination": "CHERRY PT            ",
            "Type": 80,
            "ImoNumber": 9312345,
            "Dimension": {"A": 180, "B": 60, "C": 16, "D": 16},
            "MaximumStaticDraught": 12.5,
            "Eta": {"Month": 8, "Day": 27, "Hour": 6, "Minute": 30},
        }
    },
}


def test_parse_time_utc_handles_go_format_with_nanoseconds():
    ts = parse_time_utc("2026-08-26 17:04:11.123456789 +0000 UTC")
    assert ts == datetime(2026, 8, 26, 17, 4, 11, 123456, tzinfo=timezone.utc)
    assert ts.tzinfo is not None


def test_parse_time_utc_handles_missing_fractional_part():
    ts = parse_time_utc("2026-08-26 17:04:11 +0000 UTC")
    assert ts == datetime(2026, 8, 26, 17, 4, 11, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw", ["", "not a time", "2026-13-45 99:99:99 +0000 UTC", None])
def test_parse_time_utc_returns_none_on_garbage(raw):
    assert parse_time_utc(raw) is None


def test_parse_position_report():
    msg = parse_envelope(POSITION_ENVELOPE, NOW)
    assert isinstance(msg, AisMessage)
    assert msg.message_type == POSITION_REPORT
    assert msg.mmsi == 366123456
    assert msg.lat == pytest.approx(47.8801)
    assert msg.lon == pytest.approx(-122.4102)
    # AIS pads strings to fixed width; trailing space must be stripped.
    assert msg.meta_name == "POLAR RESOLUTE"
    assert msg.received_at == datetime(2026, 8, 26, 17, 4, 11, 123456, tzinfo=timezone.utc)
    assert msg.payload["Sog"] == pytest.approx(12.4)


def test_parse_static_data():
    msg = parse_envelope(STATIC_ENVELOPE, NOW)
    assert msg.message_type == SHIP_STATIC_DATA
    assert msg.payload["Type"] == 80
    assert msg.payload["CallSign"] == "WCX8834  "  # payload is verbatim


def test_received_at_falls_back_to_arrival_when_timestamp_is_garbage():
    envelope = {
        **POSITION_ENVELOPE,
        "MetaData": {**POSITION_ENVELOPE["MetaData"], "time_utc": "wat"},
    }
    assert parse_envelope(envelope, NOW).received_at == NOW


@pytest.mark.parametrize("envelope", [
    {},
    {"MessageType": "PositionReport"},                       # no MetaData
    {"MessageType": "PositionReport", "MetaData": {}},       # no MMSI
    {"MessageType": "UnknownThing", "MetaData": {"MMSI": 1}},
    {"MessageType": "PositionReport", "MetaData": {"MMSI": 1}, "Message": {}},
    {"MessageType": "PositionReport", "MetaData": {"MMSI": "abc"}, "Message": {"PositionReport": {}}},
])
def test_malformed_envelopes_return_none_and_never_raise(envelope):
    assert parse_envelope(envelope, NOW) is None


def test_blank_ship_name_becomes_none():
    envelope = {
        **POSITION_ENVELOPE,
        "MetaData": {**POSITION_ENVELOPE["MetaData"], "ShipName": "          "},
    }
    assert parse_envelope(envelope, NOW).meta_name is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ais_parsing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.ais_client'`

- [ ] **Step 3: Implement the parsing half of `ship_observer/ais_client.py`**

```python
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
    except (KeyError, TypeError, ValueError):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ais_parsing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/ais_client.py tests/test_ais_parsing.py
git commit -m "feat: AIS envelope and timestamp parsing"
```

---

## Task 5: AIS Websocket Client with Reconnect

**Files:**
- Modify: `ship_observer/ais_client.py` (append the transport half)
- Test: `tests/test_ais_client.py`

**Interfaces:**
- Consumes: `Settings` (Task 3), `AisMessage`/`parse_envelope` (Task 4)
- Produces: `AisClient(settings, on_event=None, connect=None, backoff_base=1.0, backoff_max=60.0)` with `async def stream() -> AsyncIterator[AisMessage]`, `build_subscription(settings) -> dict`, properties `connected: bool` and `last_message_at: datetime | None`

`on_event` is a callback `(level: str, category: str, message: str, detail: dict | None) -> None`. It is synchronous and must never block; `__main__` supplies one that schedules a storage write.

`connect` is injected so tests can supply a fake. Default is `websockets.connect`.

- [ ] **Step 1: Write the failing test**

`tests/test_ais_client.py`:

```python
import asyncio
import json
from datetime import datetime, timezone

import pytest

from ship_observer.ais_client import AisClient, build_subscription
from ship_observer.config import Settings

MINIMAL = {
    "AIS_STREAM_API_KEY": "test-key",
    "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
}

POSITION_JSON = json.dumps({
    "MessageType": "PositionReport",
    "MetaData": {"MMSI": 366123456, "ShipName": "TEST SHIP",
                 "latitude": 47.88, "longitude": -122.41,
                 "time_utc": "2026-08-26 17:04:11.000000000 +0000 UTC"},
    "Message": {"PositionReport": {"UserID": 366123456, "Sog": 10.0}},
})


class FakeSocket:
    """Yields queued frames, then raises `error` (or ends the stream)."""

    def __init__(self, frames, error=None):
        self._frames = list(frames)
        self._error = error
        self.sent: list[str] = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False


def make_connect(sockets):
    """Returns a connect() that hands out each socket in turn."""
    queue = list(sockets)
    attempts = []

    def connect(url, **kwargs):
        attempts.append(url)
        return queue.pop(0) if queue else FakeSocket([])

    connect.attempts = attempts
    return connect


def test_build_subscription_shape():
    s = Settings.from_env(MINIMAL)
    sub = build_subscription(s)
    assert sub["APIKey"] == "test-key"
    assert sub["FilterMessageTypes"] == ["PositionReport", "ShipStaticData"]
    # AISStream requires latitude-first corner pairs.
    assert sub["BoundingBoxes"] == [[[47.859476, -122.527428],
                                     [47.910359, -122.323322]]]
    assert set(sub) == {"APIKey", "BoundingBoxes", "FilterMessageTypes"}


async def test_subscription_is_sent_immediately_on_connect():
    s = Settings.from_env(MINIMAL)
    sock = FakeSocket([POSITION_JSON])
    client = AisClient(s, connect=make_connect([sock]))

    messages = [m async for m in _take(client.stream(), 1)]

    assert len(sock.sent) == 1
    assert json.loads(sock.sent[0])["APIKey"] == "test-key"
    assert messages[0].mmsi == 366123456


async def test_malformed_frames_are_skipped_without_killing_the_stream():
    s = Settings.from_env(MINIMAL)
    sock = FakeSocket(["not json", json.dumps({"MessageType": "Nope"}), POSITION_JSON])
    client = AisClient(s, connect=make_connect([sock]))

    messages = [m async for m in _take(client.stream(), 1)]

    assert len(messages) == 1
    assert messages[0].mmsi == 366123456


async def test_reconnects_after_a_drop_and_resubscribes():
    s = Settings.from_env(MINIMAL)
    first = FakeSocket([POSITION_JSON], error=ConnectionError("dropped"))
    second = FakeSocket([POSITION_JSON])
    events = []
    client = AisClient(
        s,
        on_event=lambda *a: events.append(a),
        connect=make_connect([first, second]),
        backoff_base=0.0,
        backoff_max=0.0,
    )

    messages = [m async for m in _take(client.stream(), 2)]

    assert len(messages) == 2
    assert len(second.sent) == 1, "must resubscribe on the new connection"
    categories = {e[1] for e in events}
    assert categories == {"ws"}
    assert any("disconnect" in e[2].lower() for e in events)


async def test_backoff_grows_then_resets_after_a_successful_message():
    s = Settings.from_env(MINIMAL)
    client = AisClient(s, connect=make_connect([]), backoff_base=1.0, backoff_max=60.0)
    assert client._next_backoff() == pytest.approx(1.0, rel=0.5)
    assert client._next_backoff() == pytest.approx(2.0, rel=0.5)
    assert client._next_backoff() == pytest.approx(4.0, rel=0.5)
    client._reset_backoff()
    assert client._next_backoff() == pytest.approx(1.0, rel=0.5)


async def test_backoff_is_capped():
    s = Settings.from_env(MINIMAL)
    client = AisClient(s, connect=make_connect([]), backoff_base=1.0, backoff_max=5.0)
    for _ in range(20):
        delay = client._next_backoff()
    assert delay <= 5.0


async def test_last_message_at_tracks_the_newest_message():
    s = Settings.from_env(MINIMAL)
    client = AisClient(s, connect=make_connect([FakeSocket([POSITION_JSON])]))
    assert client.last_message_at is None
    async for _ in _take(client.stream(), 1):
        pass
    assert client.last_message_at == datetime(
        2026, 8, 26, 17, 4, 11, tzinfo=timezone.utc
    )


async def _take(agen, n):
    """Yield the first n items, then close the generator."""
    count = 0
    async for item in agen:
        yield item
        count += 1
        if count >= n:
            break
    await agen.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ais_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'AisClient'`

- [ ] **Step 3: Append the transport half to `ship_observer/ais_client.py`**

```python
import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator, Callable

import websockets

from .config import Settings

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
```

The top of the file must now carry the union of both halves' imports. After this
step the import block reads exactly:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ais_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/ais_client.py tests/test_ais_client.py
git commit -m "feat: AIS websocket client with jittered exponential reconnect"
```

---

## Task 6: Vessel Registry

**Files:**
- Create: `ship_observer/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `Settings`, `Vessel`, `ShipCategory`, `AisMessage`, `classify`, `priority_for`
- Produces:
  - `RegistryChange` (frozen dataclass: `vessel: Vessel`, `entered: bool`, `departed: bool`, `static_resolved_now: bool`)
  - `VesselRegistry(settings, clock=None)` with `apply(msg) -> RegistryChange`, `prune(now=None) -> list[Vessel]`, `live() -> list[Vessel]`, `departed() -> list[Vessel]`, `close_all(now) -> list[Vessel]`
  - `live()` is sorted by `entered_at` descending (newest entrant first); `departed()` is most-recently-departed first, capped at `max_ships`

- [ ] **Step 1: Write the failing test**

`tests/test_registry.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.ais_client import AisMessage
from ship_observer.config import Settings
from ship_observer.models import ShipCategory
from ship_observer.registry import VesselRegistry

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)
MINIMAL = {
    "AIS_STREAM_API_KEY": "k",
    "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
}
IN_BOX = (47.88, -122.41)
OUT_OF_BOX = (47.70, -122.41)


def settings(**overrides):
    return Settings.from_env({**MINIMAL, **overrides})


def position(mmsi, at, lat=IN_BOX[0], lon=IN_BOX[1], sog=10.0, name="TEST SHIP"):
    return AisMessage(
        message_type="PositionReport", mmsi=mmsi, received_at=at, meta_name=name,
        lat=lat, lon=lon,
        payload={"Sog": sog, "Cog": 180.0, "TrueHeading": 179, "NavigationalStatus": 0},
    )


def static(mmsi, at, ship_type=80, name="POLAR RESOLUTE", call_sign="WCX8834",
           destination="CHERRY PT", a=180, b=60):
    return AisMessage(
        message_type="ShipStaticData", mmsi=mmsi, received_at=at, meta_name=name,
        lat=IN_BOX[0], lon=IN_BOX[1],
        payload={"Name": name, "CallSign": call_sign, "Destination": destination,
                 "Type": ship_type, "ImoNumber": 9312345,
                 "Dimension": {"A": a, "B": b, "C": 16, "D": 16},
                 "MaximumStaticDraught": 12.5,
                 "Eta": {"Month": 8, "Day": 27, "Hour": 6, "Minute": 30}},
    )


def test_first_message_enters_the_vessel():
    r = VesselRegistry(settings())
    change = r.apply(position(1, T0))
    assert change.entered is True
    assert change.vessel.mmsi == 1
    assert change.vessel.entered_at == T0
    assert len(r.live()) == 1


def test_meta_name_gives_a_provisional_name_before_static_data_arrives():
    """Otherwise a ship is anonymous for its first six minutes."""
    r = VesselRegistry(settings())
    v = r.apply(position(1, T0, name="EVER GIVEN")).vessel
    assert v.name == "EVER GIVEN"
    assert v.static_resolved is False
    assert v.category is ShipCategory.UNKNOWN
    assert v.priority == 20  # provisional tier 3


def test_static_data_merges_over_position_data():
    r = VesselRegistry(settings())
    r.apply(position(1, T0, name="EVER GIVEN"))
    change = r.apply(static(1, T0 + timedelta(seconds=30)))
    v = change.vessel
    assert change.static_resolved_now is True
    assert v.name == "POLAR RESOLUTE"
    assert v.call_sign == "WCX8834"
    assert v.destination == "CHERRY PT"
    assert v.category is ShipCategory.TANKER
    assert v.priority == 30
    assert v.length_m == pytest.approx(240.0)   # A + B
    assert v.beam_m == pytest.approx(32.0)      # C + D
    assert v.static_resolved is True
    # Position data survives the merge.
    assert v.last_lat == pytest.approx(47.88)


def test_second_static_message_does_not_report_resolved_again():
    r = VesselRegistry(settings())
    r.apply(static(1, T0))
    assert r.apply(static(1, T0 + timedelta(minutes=6))).static_resolved_now is False


def test_position_tracking_accumulates():
    r = VesselRegistry(settings())
    r.apply(position(1, T0, sog=8.0))
    r.apply(position(1, T0 + timedelta(seconds=10), sog=14.0))
    v = r.apply(position(1, T0 + timedelta(seconds=20), sog=11.0)).vessel
    assert v.position_count == 3
    assert v.max_sog == pytest.approx(14.0)
    assert v.last_seen == T0 + timedelta(seconds=20)
    assert v.first_lat == pytest.approx(47.88)


def test_position_outside_the_box_departs_immediately():
    """AISStream can emit edge positions; waiting 15 minutes would be wrong."""
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    change = r.apply(position(1, T0 + timedelta(seconds=10), lat=OUT_OF_BOX[0]))
    assert change.departed is True
    assert change.vessel.depart_reason == "left_bbox"
    assert r.live() == []
    assert [v.mmsi for v in r.departed()] == [1]


def test_prune_expires_silent_vessels():
    r = VesselRegistry(settings(SHIP_TIMEOUT_SECONDS="900"))
    r.apply(position(1, T0))
    r.apply(position(2, T0 + timedelta(minutes=10)))

    departed = r.prune(now=T0 + timedelta(minutes=16))

    assert [v.mmsi for v in departed] == [1]
    assert [v.mmsi for v in r.live()] == [2]
    assert departed[0].depart_reason == "timeout"
    assert departed[0].departed_at == T0 + timedelta(minutes=16)


def test_prune_is_exclusive_at_the_boundary():
    r = VesselRegistry(settings(SHIP_TIMEOUT_SECONDS="900"))
    r.apply(position(1, T0))
    assert r.prune(now=T0 + timedelta(seconds=900)) == []
    assert r.prune(now=T0 + timedelta(seconds=901)) != []


def test_live_is_ordered_newest_entrant_first():
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    r.apply(position(2, T0 + timedelta(minutes=1)))
    r.apply(position(3, T0 + timedelta(minutes=2)))
    r.apply(position(1, T0 + timedelta(minutes=3)))  # update, not a re-entry
    assert [v.mmsi for v in r.live()] == [3, 2, 1]


def test_reentry_after_departure_starts_a_new_visit():
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    r.prune(now=T0 + timedelta(minutes=20))

    change = r.apply(position(1, T0 + timedelta(minutes=30)))

    assert change.entered is True
    assert change.vessel.entered_at == T0 + timedelta(minutes=30)
    assert change.vessel.position_count == 1
    assert change.vessel.log_id is None, "new visit needs its own ship_log row"


def test_departed_deque_is_capped_and_newest_first():
    r = VesselRegistry(settings(MAX_SHIPS="3"))
    for mmsi in range(1, 6):
        r.apply(position(mmsi, T0 + timedelta(minutes=mmsi)))
        r.prune(now=T0 + timedelta(minutes=mmsi + 16))
    assert [v.mmsi for v in r.departed()] == [5, 4, 3]


def test_close_all_departs_everything_for_shutdown():
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    r.apply(position(2, T0))
    closed = r.close_all(now=T0 + timedelta(minutes=1))
    assert {v.mmsi for v in closed} == {1, 2}
    assert all(v.depart_reason == "shutdown" for v in closed)
    assert r.live() == []


def test_message_with_no_coordinates_still_updates_last_seen():
    r = VesselRegistry(settings())
    r.apply(position(1, T0))
    change = r.apply(static(1, T0 + timedelta(minutes=5)))
    assert change.departed is False
    assert change.vessel.last_seen == T0 + timedelta(minutes=5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.registry'`

- [ ] **Step 3: Implement `ship_observer/registry.py`**

```python
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .ais_client import POSITION_REPORT, SHIP_STATIC_DATA, AisMessage
from .config import Settings
from .models import ShipCategory, Vessel
from .shiptypes import classify, priority_for


@dataclass(frozen=True)
class RegistryChange:
    vessel: Vessel
    entered: bool = False
    departed: bool = False
    static_resolved_now: bool = False


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.replace("@", " ").strip()
    return stripped or None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _format_eta(eta: Any) -> str | None:
    """AIS ETA has no year. Render as MM-DD HH:MM, or None if unset."""
    if not isinstance(eta, dict):
        return None
    month, day = eta.get("Month"), eta.get("Day")
    hour, minute = eta.get("Hour"), eta.get("Minute")
    if not all(isinstance(v, int) for v in (month, day, hour, minute)):
        return None
    if month == 0 or day == 0:
        return None
    return f"{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


class VesselRegistry:
    """In-memory state for vessels currently inside the bounding box.

    One Vessel per *visit*: a re-entry after departure is a new Vessel with a
    fresh entered_at and no log_id, so storage opens a new ship_log row.
    """

    def __init__(self, settings: Settings,
                 clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._live: dict[int, Vessel] = {}
        self._departed: deque[Vessel] = deque(maxlen=max(settings.max_ships, 1))

    def apply(self, msg: AisMessage) -> RegistryChange:
        vessel = self._live.get(msg.mmsi)
        entered = vessel is None
        if vessel is None:
            vessel = Vessel(mmsi=msg.mmsi, entered_at=msg.received_at,
                            last_seen=msg.received_at)
            self._live[msg.mmsi] = vessel

        vessel.last_seen = max(vessel.last_seen, msg.received_at)

        if msg.meta_name and not vessel.static_resolved:
            vessel.name = msg.meta_name

        static_resolved_now = False
        if msg.message_type == POSITION_REPORT:
            self._apply_position(vessel, msg)
        elif msg.message_type == SHIP_STATIC_DATA:
            static_resolved_now = not vessel.static_resolved
            self._apply_static(vessel, msg)

        # A position outside the box means the vessel has left; do not wait
        # out SHIP_TIMEOUT_SECONDS.
        if (msg.lat is not None and msg.lon is not None
                and not self._settings.bbox.contains(msg.lat, msg.lon)):
            self._depart(vessel, msg.received_at, "left_bbox")
            return RegistryChange(vessel, entered=entered, departed=True,
                                  static_resolved_now=static_resolved_now)

        return RegistryChange(vessel, entered=entered,
                              static_resolved_now=static_resolved_now)

    def _apply_position(self, vessel: Vessel, msg: AisMessage) -> None:
        p = msg.payload
        vessel.position_count += 1
        lat = msg.lat if msg.lat is not None else _number(p.get("Latitude"))
        lon = msg.lon if msg.lon is not None else _number(p.get("Longitude"))
        if lat is not None and lon is not None:
            if vessel.first_lat is None:
                vessel.first_lat, vessel.first_lon = lat, lon
            vessel.last_lat, vessel.last_lon = lat, lon
        sog = _number(p.get("Sog"))
        if sog is not None and (vessel.max_sog is None or sog > vessel.max_sog):
            vessel.max_sog = sog
        vessel.last_cog = _number(p.get("Cog"))
        heading = p.get("TrueHeading")
        vessel.last_heading = heading if isinstance(heading, int) else None
        status = p.get("NavigationalStatus")
        vessel.nav_status = status if isinstance(status, int) else None
        vessel.raw_position = dict(p)

    def _apply_static(self, vessel: Vessel, msg: AisMessage) -> None:
        p = msg.payload
        vessel.name = _clean(p.get("Name")) or vessel.name
        vessel.call_sign = _clean(p.get("CallSign")) or vessel.call_sign
        vessel.destination = _clean(p.get("Destination")) or vessel.destination

        ship_type = p.get("Type")
        vessel.ship_type = ship_type if isinstance(ship_type, int) else None
        vessel.category = classify(vessel.ship_type)
        vessel.priority = priority_for(vessel.category)

        imo = p.get("ImoNumber")
        vessel.imo = imo if isinstance(imo, int) and imo > 0 else None

        dim = p.get("Dimension")
        if isinstance(dim, dict):
            a, b = _number(dim.get("A")), _number(dim.get("B"))
            c, d = _number(dim.get("C")), _number(dim.get("D"))
            vessel.length_m = a + b if a is not None and b is not None else None
            vessel.beam_m = c + d if c is not None and d is not None else None

        vessel.draught_m = _number(p.get("MaximumStaticDraught"))
        vessel.eta = _format_eta(p.get("Eta"))
        vessel.static_resolved = True
        vessel.raw_static = dict(p)

    def _depart(self, vessel: Vessel, when: datetime, reason: str) -> None:
        self._live.pop(vessel.mmsi, None)
        vessel.departed_at = when
        vessel.depart_reason = reason
        self._departed.appendleft(vessel)

    def prune(self, now: datetime | None = None) -> list[Vessel]:
        """Expire vessels silent for longer than SHIP_TIMEOUT_SECONDS."""
        now = now or self._clock()
        cutoff = now - timedelta(seconds=self._settings.ship_timeout_seconds)
        expired = [v for v in self._live.values() if v.last_seen < cutoff]
        for vessel in sorted(expired, key=lambda v: v.last_seen):
            self._depart(vessel, now, "timeout")
        return expired

    def close_all(self, now: datetime | None = None) -> list[Vessel]:
        """Close every open visit. Called on shutdown so no visit is left open."""
        now = now or self._clock()
        vessels = list(self._live.values())
        for vessel in vessels:
            self._depart(vessel, now, "shutdown")
        return vessels

    def live(self) -> list[Vessel]:
        """Newest entrant first - the spec's display ordering."""
        return sorted(self._live.values(), key=lambda v: v.entered_at, reverse=True)

    def departed(self) -> list[Vessel]:
        """Most recently departed first, capped at MAX_SHIPS."""
        return list(self._departed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/registry.py tests/test_registry.py
git commit -m "feat: vessel registry with visit lifecycle and departure detection"
```

---

## Task 7: Slot Selection and Prioritization

The spec's rule: **priority decides which vessels get slots; recency orders the ones that were selected.** With `MAX_SHIPS` or fewer vessels present, this is indistinguishable from pure recency.

**Files:**
- Create: `ship_observer/selection.py`
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: `Settings`, `Vessel`, `ShipCategory`
- Produces: `Slots` (frozen dataclass: `live: list[Vessel]`, `history: list[Vessel]`, `show_divider: bool`), `select_slots(live, departed, settings, capacity) -> Slots`, `is_eligible(vessel, settings) -> bool`

- [ ] **Step 1: Write the failing test**

`tests/test_selection.py`:

```python
from datetime import datetime, timedelta, timezone

from ship_observer.config import Settings
from ship_observer.models import ShipCategory, Vessel
from ship_observer.selection import is_eligible, select_slots

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)
MINIMAL = {"AIS_STREAM_API_KEY": "k",
           "BBOX": "-122.527428,47.859476,-122.323322,47.910359"}


def settings(**overrides):
    return Settings.from_env({**MINIMAL, **overrides})


def vessel(mmsi, minutes, category=ShipCategory.CARGO, priority=30,
           length=200.0, resolved=True):
    at = T0 + timedelta(minutes=minutes)
    return Vessel(mmsi=mmsi, entered_at=at, last_seen=at, name=f"SHIP {mmsi}",
                  category=category, priority=priority, length_m=length,
                  static_resolved=resolved)


def test_fewer_than_capacity_is_pure_recency():
    """With <= MAX_SHIPS vessels, behaviour matches the original spec exactly."""
    vessels = [vessel(1, 0, ShipCategory.SAILING, 10, 12.0),
               vessel(2, 1, ShipCategory.CARGO, 30)]
    slots = select_slots(vessels, [], settings(), capacity=3)
    assert [v.mmsi for v in slots.live] == [2, 1]  # newest first
    assert slots.history == []
    assert slots.show_divider is False


def test_priority_decides_which_vessels_get_slots():
    vessels = [
        vessel(1, 4, ShipCategory.SAILING, 10, 12.0),
        vessel(2, 3, ShipCategory.FISHING, 10, 18.0),
        vessel(3, 2, ShipCategory.CARGO, 30),
        vessel(4, 1, ShipCategory.PASSENGER, 40, 120.0),
        vessel(5, 0, ShipCategory.TANKER, 30),
    ]
    slots = select_slots(vessels, [], settings(), capacity=3)
    # Selected: passenger (40), then the two 30s. Ordered newest-first.
    assert [v.mmsi for v in slots.live] == [3, 4, 5]


def test_within_a_tier_the_newest_entrant_wins_the_slot():
    vessels = [vessel(1, 0, ShipCategory.CARGO, 30),
               vessel(2, 1, ShipCategory.CARGO, 30),
               vessel(3, 2, ShipCategory.CARGO, 30),
               vessel(4, 3, ShipCategory.CARGO, 30)]
    slots = select_slots(vessels, [], settings(), capacity=3)
    assert [v.mmsi for v in slots.live] == [4, 3, 2]


def test_priority_selection_disabled_reverts_to_pure_recency():
    vessels = [vessel(1, 3, ShipCategory.SAILING, 10, 12.0),
               vessel(2, 2, ShipCategory.SAILING, 10, 12.0),
               vessel(3, 1, ShipCategory.SAILING, 10, 12.0),
               vessel(4, 0, ShipCategory.PASSENGER, 40, 120.0)]
    slots = select_slots(vessels, [], settings(PRIORITY_SELECTION="false"), capacity=3)
    assert [v.mmsi for v in slots.live] == [1, 2, 3]


def test_min_length_gate_excludes_small_resolved_vessels():
    s = settings(MIN_LENGTH_METERS="50")
    assert is_eligible(vessel(1, 0, ShipCategory.SAILING, 10, 12.0), s) is False
    assert is_eligible(vessel(2, 0, ShipCategory.CARGO, 30, 200.0), s) is True


def test_unresolved_vessels_are_never_hard_gated():
    """ShipStaticData takes ~6 minutes; gating on it would blank real ships."""
    s = settings(MIN_LENGTH_METERS="50", EXCLUDE_CATEGORIES="unknown")
    unresolved = vessel(1, 0, ShipCategory.UNKNOWN, 20, length=None, resolved=False)
    assert is_eligible(unresolved, s) is True


def test_exclude_categories_gate():
    s = settings(EXCLUDE_CATEGORIES="fishing,sailing")
    assert is_eligible(vessel(1, 0, ShipCategory.FISHING, 10, 18.0), s) is False
    assert is_eligible(vessel(2, 0, ShipCategory.CARGO, 30), s) is True


def test_filtered_vessels_do_not_occupy_slots():
    vessels = [vessel(1, 2, ShipCategory.SAILING, 10, 12.0),
               vessel(2, 1, ShipCategory.FISHING, 10, 18.0),
               vessel(3, 0, ShipCategory.CARGO, 30)]
    slots = select_slots(vessels, [], settings(MIN_LENGTH_METERS="50"), capacity=3)
    assert [v.mmsi for v in slots.live] == [3]


def test_history_backfills_remaining_slots_when_enabled():
    live = [vessel(1, 5), vessel(2, 4)]
    departed = [vessel(3, 3), vessel(4, 2)]
    slots = select_slots(live, departed, settings(DISPLAY_HISTORY="true"), capacity=3)
    assert [v.mmsi for v in slots.live] == [1, 2]
    assert [v.mmsi for v in slots.history] == [3]
    assert slots.show_divider is True


def test_no_divider_when_live_ships_fill_every_slot():
    live = [vessel(1, 3), vessel(2, 2), vessel(3, 1)]
    slots = select_slots(live, [vessel(9, 0)], settings(DISPLAY_HISTORY="true"),
                         capacity=3)
    assert len(slots.live) == 3
    assert slots.history == []
    assert slots.show_divider is False


def test_all_history_when_the_box_is_empty():
    departed = [vessel(1, 3), vessel(2, 2), vessel(3, 1), vessel(4, 0)]
    slots = select_slots([], departed, settings(DISPLAY_HISTORY="true"), capacity=3)
    assert slots.live == []
    assert [v.mmsi for v in slots.history] == [1, 2, 3]
    assert slots.show_divider is True


def test_history_disabled_shows_nothing_extra():
    slots = select_slots([], [vessel(1, 0)], settings(DISPLAY_HISTORY="false"),
                         capacity=3)
    assert slots.live == [] and slots.history == []
    assert slots.show_divider is False


def test_capacity_clamps_below_max_ships():
    live = [vessel(i, i) for i in range(5)]
    slots = select_slots(live, [], settings(MAX_SHIPS="3"), capacity=2)
    assert len(slots.live) == 2


def test_history_is_filtered_too():
    departed = [vessel(1, 1, ShipCategory.SAILING, 10, 12.0), vessel(2, 0)]
    slots = select_slots([], departed,
                         settings(DISPLAY_HISTORY="true", MIN_LENGTH_METERS="50"),
                         capacity=3)
    assert [v.mmsi for v in slots.history] == [2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.selection'`

- [ ] **Step 3: Implement `ship_observer/selection.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .models import Vessel


@dataclass(frozen=True)
class Slots:
    live: list[Vessel] = field(default_factory=list)
    history: list[Vessel] = field(default_factory=list)
    show_divider: bool = False

    @property
    def total(self) -> int:
        return len(self.live) + len(self.history)


def is_eligible(vessel: Vessel, settings: Settings) -> bool:
    """Hard display gates. The log is never filtered - only the panel is.

    A vessel whose ShipStaticData has not arrived is always eligible: static
    data can take six minutes, and gating on it would hide real traffic.
    """
    if not vessel.static_resolved:
        return True
    if vessel.category in settings.exclude_categories:
        return False
    if settings.min_length_meters > 0:
        if vessel.length_m is None or vessel.length_m < settings.min_length_meters:
            return False
    return True


def _select(vessels: list[Vessel], settings: Settings, limit: int) -> list[Vessel]:
    """Pick up to `limit` vessels, then order the winners newest-entrant-first."""
    if limit <= 0:
        return []
    eligible = [v for v in vessels if is_eligible(v, settings)]
    if settings.priority_selection:
        # Highest priority wins a slot; ties broken by most recent entry.
        ranked = sorted(eligible, key=lambda v: (v.priority, v.entered_at),
                        reverse=True)
    else:
        ranked = sorted(eligible, key=lambda v: v.entered_at, reverse=True)
    chosen = ranked[:limit]
    return sorted(chosen, key=lambda v: v.entered_at, reverse=True)


def select_slots(live: list[Vessel], departed: list[Vessel],
                 settings: Settings, capacity: int) -> Slots:
    """Assign display slots.

    `capacity` is what the panel can physically fit; the effective slot count
    is min(MAX_SHIPS, capacity).
    """
    limit = min(settings.max_ships, capacity)
    chosen_live = _select(live, settings, limit)

    remaining = limit - len(chosen_live)
    if not settings.display_history or remaining <= 0:
        return Slots(live=chosen_live, history=[], show_divider=False)

    # `departed` is already most-recently-departed first; preserve that order.
    history = [v for v in departed if is_eligible(v, settings)][:remaining]
    return Slots(live=chosen_live, history=history, show_divider=bool(history))
```

Note: history preserves departure order and is *not* re-sorted by `entered_at`, because "last seen" means most recently gone.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_selection.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/selection.py tests/test_selection.py
git commit -m "feat: priority slot selection with recency ordering"
```

---

## Task 8: Storage — Schema and Visit Lifecycle

SQLite work is synchronous but wrapped in `asyncio.to_thread` so the event loop never blocks on the SD card. A single connection is used with `check_same_thread=False` and a lock, because `to_thread` uses a pool.

**Files:**
- Create: `ship_observer/storage.py`
- Test: `tests/test_storage_visits.py`

**Interfaces:**
- Consumes: `Vessel`, `ShipCategory`
- Produces: `Storage(path: Path)` with `async open()`, `async close()`, `async begin_visit(v) -> int`, `async update_visit(v) -> None`, `async end_visit(v, reason) -> None`, `async log_event(level, category, message, detail=None) -> None`, and `SCHEMA` constant

- [ ] **Step 1: Write the failing test**

`tests/test_storage_visits.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.models import ShipCategory, Vessel
from ship_observer.storage import Storage

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(tmp_path):
    s = Storage(tmp_path / "test.db")
    await s.open()
    yield s
    await s.close()


def make_vessel(mmsi=366123456, **overrides):
    base = dict(mmsi=mmsi, entered_at=T0, last_seen=T0, name="POLAR RESOLUTE",
                call_sign="WCX8834", destination="CHERRY PT", ship_type=80,
                category=ShipCategory.TANKER, priority=30, imo=9312345,
                length_m=240.0, beam_m=32.0, draught_m=12.5,
                first_lat=47.88, first_lon=-122.41,
                last_lat=47.89, last_lon=-122.40, max_sog=14.2,
                position_count=5, static_resolved=True)
    base.update(overrides)
    return Vessel(**base)


async def test_open_creates_schema_and_enables_wal(store):
    tables = await store._fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'")
    assert {"ship_log", "event_log"} <= {r["name"] for r in tables}
    mode = await store._fetchall("PRAGMA journal_mode")
    assert mode[0]["journal_mode"].lower() == "wal"


async def test_open_is_idempotent(tmp_path):
    for _ in range(2):
        s = Storage(tmp_path / "t.db")
        await s.open()
        await s.close()


async def test_open_creates_missing_parent_directories(tmp_path):
    s = Storage(tmp_path / "deep" / "nested" / "t.db")
    await s.open()
    await s.close()
    assert (tmp_path / "deep" / "nested" / "t.db").exists()


async def test_begin_visit_returns_a_row_id_and_persists_metadata(store):
    v = make_vessel()
    log_id = await store.begin_visit(v)
    assert isinstance(log_id, int)

    rows = await store._fetchall("SELECT * FROM ship_log")
    assert len(rows) == 1
    row = rows[0]
    assert row["mmsi"] == 366123456
    assert row["name"] == "POLAR RESOLUTE"
    assert row["call_sign"] == "WCX8834"
    assert row["destination"] == "CHERRY PT"
    assert row["category"] == "tanker"
    assert row["length_m"] == pytest.approx(240.0)
    assert row["departed_at"] is None
    assert row["entered_at"] == T0.isoformat()


async def test_update_visit_updates_in_place(store):
    v = make_vessel()
    v.log_id = await store.begin_visit(v)

    v.last_seen = T0 + timedelta(minutes=5)
    v.position_count = 42
    v.destination = "SEATTLE"
    v.displayed = True
    await store.update_visit(v)

    rows = await store._fetchall("SELECT * FROM ship_log")
    assert len(rows) == 1, "update must not insert a second row"
    assert rows[0]["position_count"] == 42
    assert rows[0]["destination"] == "SEATTLE"
    assert rows[0]["displayed"] == 1


async def test_update_visit_without_a_log_id_is_a_no_op(store):
    await store.update_visit(make_vessel())   # log_id is None
    assert await store._fetchall("SELECT * FROM ship_log") == []


async def test_end_visit_records_departure(store):
    v = make_vessel()
    v.log_id = await store.begin_visit(v)
    v.departed_at = T0 + timedelta(minutes=20)
    await store.end_visit(v, "timeout")

    row = (await store._fetchall("SELECT * FROM ship_log"))[0]
    assert row["departed_at"] == (T0 + timedelta(minutes=20)).isoformat()
    assert row["depart_reason"] == "timeout"


async def test_reentry_creates_a_second_row(store):
    first = make_vessel()
    first.log_id = await store.begin_visit(first)
    first.departed_at = T0 + timedelta(minutes=20)
    await store.end_visit(first, "timeout")

    second = make_vessel(entered_at=T0 + timedelta(minutes=30),
                         last_seen=T0 + timedelta(minutes=30))
    await store.begin_visit(second)

    rows = await store._fetchall("SELECT * FROM ship_log ORDER BY entered_at")
    assert len(rows) == 2
    assert rows[0]["departed_at"] is not None
    assert rows[1]["departed_at"] is None


async def test_raw_payloads_round_trip_as_json(store):
    v = make_vessel(raw_static={"Type": 80, "Name": "X"},
                    raw_position={"Sog": 12.4})
    await store.begin_visit(v)
    row = (await store._fetchall("SELECT * FROM ship_log"))[0]
    import json
    assert json.loads(row["raw_static"])["Type"] == 80
    assert json.loads(row["raw_position"])["Sog"] == pytest.approx(12.4)


async def test_unresolved_vessel_stores_null_type_and_unknown_category(store):
    v = make_vessel(ship_type=None, category=ShipCategory.UNKNOWN,
                    static_resolved=False, length_m=None)
    await store.begin_visit(v)
    row = (await store._fetchall("SELECT * FROM ship_log"))[0]
    assert row["ship_type"] is None
    assert row["category"] == "unknown"
    assert row["static_resolved"] == 0


async def test_log_event_persists_level_category_and_json_detail(store):
    await store.log_event("WARN", "ws", "disconnected", {"attempt": 3})
    row = (await store._fetchall("SELECT * FROM event_log"))[0]
    assert row["level"] == "WARN"
    assert row["category"] == "ws"
    assert row["message"] == "disconnected"
    import json
    assert json.loads(row["detail"])["attempt"] == 3


async def test_log_event_accepts_no_detail(store):
    await store.log_event("INFO", "display", "started")
    assert (await store._fetchall("SELECT * FROM event_log"))[0]["detail"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage_visits.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.storage'`

- [ ] **Step 3: Implement `ship_observer/storage.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage_visits.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/storage.py tests/test_storage_visits.py
git commit -m "feat: SQLite storage with per-visit ship log"
```

---

## Task 9: Storage — Retention, Queries, and Traffic Summary

The traffic summary is the instrument for answering "is recreational traffic actually noisy here, and what should `MIN_LENGTH_METERS` be?"

**Files:**
- Modify: `ship_observer/storage.py` (append)
- Test: `tests/test_storage_queries.py`

**Interfaces:**
- Consumes: Task 8's `Storage`
- Produces: `async prune(ship_log_days, event_log_hours) -> tuple[int, int]`, `async query_ships(since=None, until=None, category=None, limit=200) -> list[dict]`, `async query_events(since=None, level=None, category=None, limit=200) -> list[dict]`, `async traffic_summary(window_seconds) -> dict`, `parse_window(raw: str) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_storage_queries.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.models import ShipCategory, Vessel
from ship_observer.storage import Storage, parse_window

NOW = datetime.now(timezone.utc)


@pytest.fixture
async def store(tmp_path):
    s = Storage(tmp_path / "t.db")
    await s.open()
    yield s
    await s.close()


async def add_visit(store, mmsi, entered, dwell_minutes=30,
                    category=ShipCategory.CARGO, length=200.0, resolved=True,
                    displayed=False):
    v = Vessel(mmsi=mmsi, entered_at=entered,
               last_seen=entered + timedelta(minutes=dwell_minutes),
               departed_at=entered + timedelta(minutes=dwell_minutes),
               depart_reason="timeout", name=f"SHIP {mmsi}",
               category=category, priority=30, length_m=length,
               static_resolved=resolved, displayed=displayed)
    v.log_id = await store.begin_visit(v)
    return v


@pytest.mark.parametrize("raw,seconds", [
    ("7d", 604800), ("48h", 172800), ("30m", 1800), ("90s", 90), ("1d", 86400),
])
def test_parse_window(raw, seconds):
    assert parse_window(raw) == seconds


@pytest.mark.parametrize("raw", ["", "7", "d7", "-1d", "7y", "abc"])
def test_parse_window_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_window(raw)


async def test_prune_respects_both_retention_windows(store):
    await add_visit(store, 1, NOW - timedelta(days=9))
    await add_visit(store, 2, NOW - timedelta(days=1))
    await store._execute(
        "INSERT INTO event_log (ts, level, category, message) VALUES (?,?,?,?)",
        ((NOW - timedelta(hours=72)).isoformat(), "INFO", "ws", "old"))
    await store.log_event("INFO", "ws", "recent")

    ships_deleted, events_deleted = await store.prune(ship_log_days=7,
                                                      event_log_hours=48)

    assert (ships_deleted, events_deleted) == (1, 1)
    assert [r["mmsi"] for r in await store._fetchall("SELECT mmsi FROM ship_log")] == [2]
    messages = [r["message"] for r in await store._fetchall("SELECT message FROM event_log")]
    assert messages == ["recent"]


async def test_prune_on_an_empty_database_is_safe(store):
    assert await store.prune(7, 48) == (0, 0)


async def test_query_ships_filters_and_orders_newest_first(store):
    await add_visit(store, 1, NOW - timedelta(hours=5), category=ShipCategory.CARGO)
    await add_visit(store, 2, NOW - timedelta(hours=1), category=ShipCategory.TANKER)
    await add_visit(store, 3, NOW - timedelta(hours=3), category=ShipCategory.CARGO)

    assert [r["mmsi"] for r in await store.query_ships()] == [2, 3, 1]
    assert [r["mmsi"] for r in await store.query_ships(category="cargo")] == [3, 1]
    assert [r["mmsi"] for r in await store.query_ships(
        since=NOW - timedelta(hours=4))] == [2, 3]
    assert len(await store.query_ships(limit=1)) == 1


async def test_query_events_filters_by_level_and_category(store):
    await store.log_event("INFO", "ws", "connected")
    await store.log_event("WARN", "ws", "dropped")
    await store.log_event("ERROR", "display", "render failed")

    assert len(await store.query_events()) == 3
    assert [r["message"] for r in await store.query_events(level="WARN")] == ["dropped"]
    assert [r["message"] for r in await store.query_events(category="display")] == [
        "render failed"]


async def test_query_events_level_filter_is_a_minimum_severity(store):
    await store.log_event("DEBUG", "ws", "d")
    await store.log_event("INFO", "ws", "i")
    await store.log_event("ERROR", "ws", "e")
    messages = {r["message"] for r in await store.query_events(level="INFO")}
    assert messages == {"i", "e"}


async def test_traffic_summary_counts_by_category(store):
    await add_visit(store, 1, NOW - timedelta(hours=2), category=ShipCategory.CARGO)
    await add_visit(store, 2, NOW - timedelta(hours=3), category=ShipCategory.CARGO)
    await add_visit(store, 3, NOW - timedelta(hours=4), category=ShipCategory.SAILING,
                    length=12.0)
    await add_visit(store, 4, NOW - timedelta(days=30), category=ShipCategory.TANKER)

    summary = await store.traffic_summary(window_seconds=86400)

    assert summary["total_visits"] == 3, "the 30-day-old visit is outside the window"
    by_cat = {row["category"]: row["visits"] for row in summary["by_category"]}
    assert by_cat == {"cargo": 2, "sailing": 1}


async def test_traffic_summary_length_histogram_buckets_by_ten_metres(store):
    await add_visit(store, 1, NOW - timedelta(hours=1), length=12.0)
    await add_visit(store, 2, NOW - timedelta(hours=1), length=18.0)
    await add_visit(store, 3, NOW - timedelta(hours=1), length=205.0)

    buckets = {row["bucket_m"]: row["visits"]
               for row in (await store.traffic_summary(86400))["length_histogram"]}

    assert buckets[10] == 2
    assert buckets[200] == 1


async def test_traffic_summary_reports_dwell_and_unresolved_counts(store):
    await add_visit(store, 1, NOW - timedelta(hours=2), dwell_minutes=10)
    await add_visit(store, 2, NOW - timedelta(hours=2), dwell_minutes=30)
    await add_visit(store, 3, NOW - timedelta(hours=2), dwell_minutes=50,
                    resolved=False, length=None)

    summary = await store.traffic_summary(86400)

    dwell = {row["category"]: row for row in summary["dwell_by_category"]}
    assert dwell["cargo"]["median_minutes"] == pytest.approx(30.0, abs=0.1)
    assert summary["unresolved_static_visits"] == 1


async def test_traffic_summary_reports_visits_per_hour(store):
    await add_visit(store, 1, NOW - timedelta(hours=1))
    await add_visit(store, 2, NOW - timedelta(hours=1))
    hours = {row["hour"]: row["visits"] for row in
             (await store.traffic_summary(86400))["visits_by_hour"]}
    assert sum(hours.values()) == 2


async def test_traffic_summary_on_empty_database_returns_zeros(store):
    summary = await store.traffic_summary(86400)
    assert summary["total_visits"] == 0
    assert summary["by_category"] == []
    assert summary["length_histogram"] == []
    assert summary["unresolved_static_visits"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage_queries.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_window'`

- [ ] **Step 3: Append to `ship_observer/storage.py`**

```python
import re
import statistics

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


# --- append these methods to the Storage class ------------------------------

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage_queries.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite for regressions**

Run: `pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ship_observer/storage.py tests/test_storage_queries.py
git commit -m "feat: log retention, queries, and traffic summary aggregates"
```

---

## Task 10: Canvas and Bitmap Primitives

**Files:**
- Create: `ship_observer/render/__init__.py`, `ship_observer/render/canvas.py`
- Test: `tests/render/test_canvas.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `RGB = tuple[int, int, int]`
  - `Bitmap` (frozen: `width`, `height`, `pixels: tuple[RGB | None, ...]`, `Bitmap.from_art(art, palette)`)
  - `Canvas(width, height)` with `clear()`, `set_pixel(x, y, rgb)`, `hline(y, x0, x1, rgb)`, `blit(bitmap, x, y)`, `to_bytes() -> bytes`, `dim(factor)`, `get_pixel(x, y) -> RGB`

- [ ] **Step 1: Write the failing test**

`tests/render/test_canvas.py`:

```python
import pytest

from ship_observer.render.canvas import Bitmap, Canvas

RED = (255, 0, 0)
GREEN = (0, 255, 0)


def test_new_canvas_is_black():
    c = Canvas(4, 3)
    assert c.to_bytes() == bytes(4 * 3 * 3)
    assert c.get_pixel(0, 0) == (0, 0, 0)


def test_set_and_get_pixel():
    c = Canvas(4, 3)
    c.set_pixel(1, 2, RED)
    assert c.get_pixel(1, 2) == RED
    assert c.get_pixel(0, 0) == (0, 0, 0)


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (4, 0), (0, 3), (99, 99)])
def test_out_of_bounds_writes_are_silently_clipped(x, y):
    """Clipping is the norm here - scrolling text runs off both edges."""
    c = Canvas(4, 3)
    c.set_pixel(x, y, RED)
    assert c.to_bytes() == bytes(4 * 3 * 3)


def test_to_bytes_is_row_major_rgb():
    c = Canvas(2, 2)
    c.set_pixel(0, 0, RED)
    c.set_pixel(1, 1, GREEN)
    assert c.to_bytes() == bytes([255, 0, 0,  0, 0, 0,
                                  0, 0, 0,    0, 255, 0])


def test_clear_resets_every_pixel():
    c = Canvas(2, 2)
    c.set_pixel(0, 0, RED)
    c.clear()
    assert c.to_bytes() == bytes(2 * 2 * 3)


def test_hline_is_inclusive_and_clipped():
    c = Canvas(4, 2)
    c.hline(0, 1, 2, RED)
    assert [c.get_pixel(x, 0) for x in range(4)] == [(0, 0, 0), RED, RED, (0, 0, 0)]
    c.hline(1, -5, 99, GREEN)
    assert all(c.get_pixel(x, 1) == GREEN for x in range(4))


def test_dim_scales_every_channel():
    c = Canvas(1, 1)
    c.set_pixel(0, 0, (200, 100, 50))
    c.dim(0.5)
    assert c.get_pixel(0, 0) == (100, 50, 25)


def test_bitmap_from_art_maps_palette_characters():
    bitmap = Bitmap.from_art(["#.", ".o"], {"#": RED, "o": GREEN})
    assert bitmap.width == 2 and bitmap.height == 2
    assert bitmap.pixels == (RED, None, None, GREEN)


def test_bitmap_from_art_rejects_ragged_rows():
    with pytest.raises(ValueError, match="same width"):
        Bitmap.from_art(["##", "#"], {"#": RED})


def test_bitmap_from_art_rejects_unmapped_characters():
    with pytest.raises(ValueError, match="'x'"):
        Bitmap.from_art(["#x"], {"#": RED})


def test_blit_skips_transparent_pixels():
    c = Canvas(3, 2)
    c.set_pixel(1, 0, GREEN)
    c.blit(Bitmap.from_art(["#.", "##"], {"#": RED}), 0, 0)
    assert c.get_pixel(0, 0) == RED
    assert c.get_pixel(1, 0) == GREEN, "transparent pixel must not overwrite"
    assert c.get_pixel(0, 1) == RED and c.get_pixel(1, 1) == RED


def test_blit_clips_at_the_edges():
    c = Canvas(2, 2)
    c.blit(Bitmap.from_art(["##", "##"], {"#": RED}), 1, 1)
    assert c.get_pixel(1, 1) == RED
    assert c.get_pixel(0, 0) == (0, 0, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/render/test_canvas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.render'`

- [ ] **Step 3: Implement `ship_observer/render/canvas.py`**

Create `ship_observer/render/__init__.py` (empty) and `tests/render/__init__.py` is not needed (pytest rootdir config handles it).

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

RGB = tuple[int, int, int]
BLACK: RGB = (0, 0, 0)


@dataclass(frozen=True)
class Bitmap:
    """A small sprite. `None` pixels are transparent."""

    width: int
    height: int
    pixels: tuple[RGB | None, ...]

    @classmethod
    def from_art(cls, art: Sequence[str], palette: dict[str, RGB]) -> "Bitmap":
        """Build from string art. '.' is always transparent."""
        if not art:
            raise ValueError("art must have at least one row")
        width = len(art[0])
        if any(len(row) != width for row in art):
            raise ValueError("every art row must be the same width")
        pixels: list[RGB | None] = []
        for row in art:
            for char in row:
                if char == ".":
                    pixels.append(None)
                elif char in palette:
                    pixels.append(palette[char])
                else:
                    raise ValueError(f"art character {char!r} is not in the palette")
        return cls(width=width, height=len(art), pixels=tuple(pixels))


class Canvas:
    """A mutable RGB framebuffer. Writes outside the bounds are dropped."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._buf = bytearray(width * height * 3)

    def clear(self) -> None:
        self._buf[:] = bytes(len(self._buf))

    def set_pixel(self, x: int, y: int, rgb: RGB) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        offset = (y * self.width + x) * 3
        self._buf[offset:offset + 3] = bytes(rgb)

    def get_pixel(self, x: int, y: int) -> RGB:
        offset = (y * self.width + x) * 3
        return tuple(self._buf[offset:offset + 3])  # type: ignore[return-value]

    def hline(self, y: int, x0: int, x1: int, rgb: RGB) -> None:
        """Inclusive on both ends."""
        for x in range(max(0, min(x0, x1)), min(self.width - 1, max(x0, x1)) + 1):
            self.set_pixel(x, y, rgb)

    def blit(self, bitmap: Bitmap, x: int, y: int) -> None:
        for row in range(bitmap.height):
            for col in range(bitmap.width):
                pixel = bitmap.pixels[row * bitmap.width + col]
                if pixel is not None:
                    self.set_pixel(x + col, y + row, pixel)

    def dim(self, factor: float) -> None:
        """Scale every channel. Used to signal a stale AIS feed."""
        self._buf[:] = bytes(int(value * factor) for value in self._buf)

    def to_bytes(self) -> bytes:
        return bytes(self._buf)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/render/test_canvas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/render/ tests/render/
git commit -m "feat: canvas framebuffer and bitmap primitives"
```

---

## Task 11: 4x6 Bitmap Font

Rather than hand-authoring glyphs, vendor the public-domain X11 `4x6.bdf` that ships with `rpi-rgb-led-matrix` and parse it. This gives full ASCII coverage and correct metrics for free.

**Files:**
- Create: `ship_observer/render/font.py`, `ship_observer/render/fonts/4x6.bdf`
- Test: `tests/render/test_font.py`

**Interfaces:**
- Consumes: `Canvas`, `RGB` (Task 10)
- Produces: `FONT_W = 4`, `FONT_H = 6`, `text_width(text) -> int`, `max_chars(pixels) -> int`, `draw_text(canvas, text, x, y, rgb, clip_x0=None, clip_x1=None) -> None`, `Font.default() -> Font`

- [ ] **Step 1: Obtain the font file**

```bash
mkdir -p ship_observer/render/fonts
curl -fsSL -o ship_observer/render/fonts/4x6.bdf \
  https://raw.githubusercontent.com/hzeller/rpi-rgb-led-matrix/master/fonts/4x6.bdf
head -20 ship_observer/render/fonts/4x6.bdf   # verify it is a BDF, not an HTML error page
```

Expected: the file begins with `STARTFONT 2.1` and contains `FONTBOUNDINGBOX 4 6 0 -1`.

- [ ] **Step 2: Write the failing test**

`tests/render/test_font.py`:

```python
import pytest

from ship_observer.render.canvas import Canvas
from ship_observer.render.font import FONT_H, FONT_W, Font, draw_text, max_chars, text_width

WHITE = (255, 255, 255)


def test_font_cell_dimensions_match_the_spec():
    assert (FONT_W, FONT_H) == (4, 6)


def test_max_chars_for_a_64px_panel():
    """16 characters across is the constraint the whole layout is built on."""
    assert max_chars(64) == 16
    assert max_chars(54) == 13   # the name field, indented past the icon
    assert max_chars(3) == 0


def test_text_width_is_cell_width_times_length():
    assert text_width("ABC") == 12
    assert text_width("") == 0


def test_font_covers_every_character_ais_can_emit():
    font = Font.default()
    for code in range(32, 127):
        assert font.glyph(chr(code)) is not None, f"missing glyph for {chr(code)!r}"


def test_unmappable_characters_fall_back_rather_than_raising():
    font = Font.default()
    assert font.glyph("é") is not None   # é -> fallback box


def test_draw_text_marks_pixels_inside_its_advance_box():
    c = Canvas(64, 8)
    draw_text(c, "A", 0, 0, WHITE)
    lit = [(x, y) for y in range(8) for x in range(64)
           if c.get_pixel(x, y) != (0, 0, 0)]
    assert lit, "drawing 'A' must light at least one pixel"
    assert all(0 <= x < FONT_W and 0 <= y < FONT_H for x, y in lit)


def test_draw_text_advances_one_cell_per_character():
    c = Canvas(64, 8)
    draw_text(c, "AA", 0, 0, WHITE)
    first = {(x, y) for y in range(8) for x in range(FONT_W)
             if c.get_pixel(x, y) != (0, 0, 0)}
    second = {(x - FONT_W, y) for y in range(8) for x in range(FONT_W, FONT_W * 2)
              if c.get_pixel(x, y) != (0, 0, 0)}
    assert first == second


def test_space_draws_nothing():
    c = Canvas(64, 8)
    draw_text(c, " ", 0, 0, WHITE)
    assert c.to_bytes() == bytes(64 * 8 * 3)


def test_draw_text_uses_the_requested_colour():
    c = Canvas(64, 8)
    draw_text(c, "A", 0, 0, (12, 34, 56))
    lit = {c.get_pixel(x, y) for y in range(8) for x in range(64)
           if c.get_pixel(x, y) != (0, 0, 0)}
    assert lit == {(12, 34, 56)}


def test_negative_x_clips_instead_of_wrapping():
    """Scrolling text is drawn at negative offsets every frame."""
    c = Canvas(64, 8)
    draw_text(c, "AAAA", -FONT_W, 0, WHITE)
    assert all(c.get_pixel(x, y) == (0, 0, 0)
               for y in range(8) for x in range(60, 64))


def test_clip_window_confines_drawing():
    c = Canvas(64, 8)
    draw_text(c, "AAAAAAAA", 0, 0, WHITE, clip_x0=10, clip_x1=20)
    lit_x = [x for y in range(8) for x in range(64) if c.get_pixel(x, y) != (0, 0, 0)]
    assert lit_x, "something must be drawn inside the window"
    assert all(10 <= x <= 20 for x in lit_x)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/render/test_font.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.render.font'`

- [ ] **Step 4: Implement `ship_observer/render/font.py`**

```python
from __future__ import annotations

import functools
from pathlib import Path

from .canvas import RGB, Canvas

FONT_W = 4
FONT_H = 6
FONT_PATH = Path(__file__).parent / "fonts" / "4x6.bdf"
FALLBACK_CHAR = "?"


def text_width(text: str) -> int:
    return len(text) * FONT_W


def max_chars(pixels: int) -> int:
    """How many characters fit in a box this wide."""
    return max(0, pixels // FONT_W)


class Font:
    """A parsed BDF font.

    Each glyph is a tuple of row bitmasks, MSB-first from the left edge of the
    cell. Only the bits within FONT_W matter.
    """

    def __init__(self, glyphs: dict[str, tuple[int, ...]]) -> None:
        self._glyphs = glyphs

    @classmethod
    @functools.lru_cache(maxsize=1)
    def default(cls) -> "Font":
        return cls.from_bdf(FONT_PATH)

    @classmethod
    def from_bdf(cls, path: Path) -> "Font":
        glyphs: dict[str, tuple[int, ...]] = {}
        codepoint: int | None = None
        rows: list[int] | None = None

        for line in path.read_text(encoding="latin-1").splitlines():
            line = line.strip()
            if line.startswith("ENCODING "):
                codepoint = int(line.split()[1])
            elif line == "BITMAP":
                rows = []
            elif line == "ENDCHAR":
                if codepoint is not None and rows is not None and 0 <= codepoint < 0x110000:
                    glyphs[chr(codepoint)] = tuple(rows)
                codepoint, rows = None, None
            elif rows is not None and line:
                # BDF pads each row to a whole number of bytes; the glyph is
                # left-aligned in the high bits.
                value = int(line, 16)
                shift = (len(line) * 4) - FONT_W
                rows.append((value >> shift) if shift > 0 else value)

        if not glyphs:
            raise ValueError(f"no glyphs parsed from {path}")
        return cls(glyphs)

    def glyph(self, char: str) -> tuple[int, ...]:
        return self._glyphs.get(char) or self._glyphs.get(FALLBACK_CHAR) or ()


def draw_text(canvas: Canvas, text: str, x: int, y: int, rgb: RGB,
              clip_x0: int | None = None, clip_x1: int | None = None) -> None:
    """Draw `text` with its top-left cell corner at (x, y).

    Negative x clips rather than wrapping, which is what makes horizontal
    scrolling work. clip_x0/clip_x1 are inclusive column bounds.
    """
    font = Font.default()
    low = 0 if clip_x0 is None else clip_x0
    high = canvas.width - 1 if clip_x1 is None else clip_x1

    for index, char in enumerate(text):
        cell_x = x + index * FONT_W
        if cell_x > high or cell_x + FONT_W <= low:
            continue
        for row_index, bits in enumerate(font.glyph(char)):
            if row_index >= FONT_H:
                break
            for col in range(FONT_W):
                if bits & (1 << (FONT_W - 1 - col)):
                    px = cell_x + col
                    if low <= px <= high:
                        canvas.set_pixel(px, y + row_index, rgb)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/render/test_font.py -v`
Expected: PASS

If `test_font_covers_every_character_ais_can_emit` fails, the downloaded BDF is not the expected file — re-check step 1.

- [ ] **Step 6: Commit**

```bash
git add ship_observer/render/font.py ship_observer/render/fonts/ tests/render/test_font.py
git commit -m "feat: 4x6 BDF bitmap font with clipping text renderer"
```

---

## Task 12: Category Icons

**Files:**
- Create: `ship_observer/render/icons.py`
- Test: `tests/render/test_icons.py`

**Interfaces:**
- Consumes: `Bitmap` (Task 10), `ShipCategory`
- Produces: `ICON_W = 8`, `ICON_H = 8`, `icon_for(category) -> Bitmap`, `CATEGORY_COLOR: dict[ShipCategory, RGB]`

- [ ] **Step 1: Write the failing test**

`tests/render/test_icons.py`:

```python
import pytest

from ship_observer.models import ShipCategory
from ship_observer.render.icons import CATEGORY_COLOR, ICON_H, ICON_W, icon_for


def test_icon_dimensions_match_the_layout_budget():
    assert (ICON_W, ICON_H) == (8, 8)


@pytest.mark.parametrize("category", list(ShipCategory))
def test_every_category_has_an_icon_of_the_right_size(category):
    icon = icon_for(category)
    assert icon.width == ICON_W and icon.height == ICON_H
    assert len(icon.pixels) == ICON_W * ICON_H


@pytest.mark.parametrize("category", list(ShipCategory))
def test_every_icon_lights_at_least_one_pixel(category):
    assert any(p is not None for p in icon_for(category).pixels)


@pytest.mark.parametrize("category", list(ShipCategory))
def test_every_category_has_a_colour(category):
    r, g, b = CATEGORY_COLOR[category]
    assert all(0 <= c <= 255 for c in (r, g, b))


def test_icons_are_visually_distinct():
    """Two categories rendering identically would make the panel useless."""
    shapes = {}
    for category in ShipCategory:
        key = tuple(p is not None for p in icon_for(category).pixels)
        shapes.setdefault(key, []).append(category.value)
    duplicates = {k: v for k, v in shapes.items() if len(v) > 1}
    assert not duplicates, f"identical icon shapes: {list(duplicates.values())}"


def test_icon_for_is_cached():
    assert icon_for(ShipCategory.CARGO) is icon_for(ShipCategory.CARGO)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/render/test_icons.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.render.icons'`

- [ ] **Step 3: Implement `ship_observer/render/icons.py`**

```python
from __future__ import annotations

import functools

from ..models import ShipCategory
from .canvas import RGB, Bitmap

ICON_W = 8
ICON_H = 8

# '#' is the hull, drawn in the category colour. '*' is an accent, drawn
# brighter. '.' is transparent.
CATEGORY_COLOR: dict[ShipCategory, RGB] = {
    ShipCategory.PASSENGER: (80, 200, 255),
    ShipCategory.CARGO: (255, 170, 60),
    ShipCategory.TANKER: (255, 90, 90),
    ShipCategory.TUG: (200, 140, 255),
    ShipCategory.FISHING: (120, 220, 140),
    ShipCategory.SAILING: (230, 230, 230),
    ShipCategory.PLEASURE: (255, 220, 120),
    ShipCategory.PATROL: (255, 60, 60),
    ShipCategory.MILITARY: (150, 170, 190),
    ShipCategory.OTHER: (140, 140, 140),
    ShipCategory.UNKNOWN: (90, 90, 90),
}

ACCENT: RGB = (255, 255, 255)

_ART: dict[ShipCategory, list[str]] = {
    ShipCategory.PASSENGER: [   # ferry: boxy superstructure on a wide hull
        "........",
        "..####..",
        "..#..#..",
        ".######.",
        ".#....#.",
        "########",
        ".######.",
        "..####..",
    ],
    ShipCategory.CARGO: [       # box boat: stacked containers
        "........",
        "#.##.##.",
        "########",
        "#.##.##.",
        "########",
        "########",
        ".######.",
        "..####..",
    ],
    ShipCategory.TANKER: [      # long low hull with a midships manifold
        "........",
        "........",
        "......#.",
        "..#...##",
        "########",
        "########",
        ".######.",
        "........",
    ],
    ShipCategory.TUG: [         # short with a tall wheelhouse
        "........",
        "...##...",
        "...##...",
        "..####..",
        ".######.",
        "########",
        ".#####..",
        "........",
    ],
    ShipCategory.FISHING: [     # trawler with net boom
        "..#.....",
        "..#..#..",
        "..#..#..",
        "..####..",
        ".######.",
        "########",
        ".#####..",
        "........",
    ],
    ShipCategory.SAILING: [     # triangular sail
        "...#....",
        "..##....",
        ".###....",
        "####....",
        "...#....",
        "########",
        ".######.",
        "........",
    ],
    ShipCategory.PLEASURE: [    # low powerboat with a windshield
        "........",
        "........",
        "....##..",
        "...####.",
        "..######",
        "########",
        ".#####..",
        "........",
    ],
    ShipCategory.PATROL: [      # light bar
        "..*..*..",
        "..####..",
        "..#..#..",
        ".######.",
        "########",
        "########",
        ".#####..",
        "........",
    ],
    ShipCategory.MILITARY: [    # mast and low profile
        "....#...",
        "....#...",
        "..#.#...",
        "..###...",
        ".#####..",
        "########",
        ".######.",
        "........",
    ],
    ShipCategory.OTHER: [       # generic hull
        "........",
        "........",
        "........",
        "...##...",
        "..####..",
        "########",
        ".######.",
        "........",
    ],
    ShipCategory.UNKNOWN: [     # generic hull with a question mark above
        "..###...",
        "....#...",
        "...#....",
        "........",
        "...#....",
        "########",
        ".######.",
        "........",
    ],
}


@functools.lru_cache(maxsize=None)
def icon_for(category: ShipCategory) -> Bitmap:
    art = _ART[category]
    palette = {"#": CATEGORY_COLOR[category], "*": ACCENT}
    return Bitmap.from_art(art, palette)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/render/test_icons.py -v`
Expected: PASS

If `test_icons_are_visually_distinct` fails, two art blocks are identical — adjust one until they differ.

- [ ] **Step 5: Commit**

```bash
git add ship_observer/render/icons.py tests/render/test_icons.py
git commit -m "feat: 8x8 category icons"
```

---

## Task 13: Scroll State Machine

**Files:**
- Create: `ship_observer/render/scroll.py`
- Test: `tests/render/test_scroll.py`

**Interfaces:**
- Consumes: `text_width` (Task 11)
- Produces: `Scroller(speed_px_s=12.0, pause_s=1.5)` with `offset_for(key, text, box_width, dt) -> int` and `retain(keys) -> None`

- [ ] **Step 1: Write the failing test**

`tests/render/test_scroll.py`:

```python
from ship_observer.render.scroll import Scroller

KEY = (366123456, "name")


def test_text_that_fits_never_moves():
    s = Scroller()
    for _ in range(200):
        assert s.offset_for(KEY, "SHORT", box_width=64, dt=0.1) == 0


def test_overflowing_text_holds_then_scrolls_left():
    s = Scroller(speed_px_s=10.0, pause_s=1.0)
    text = "A" * 40   # 160 px in a 64 px box

    assert s.offset_for(KEY, text, 64, dt=0.5) == 0, "still in the opening hold"
    assert s.offset_for(KEY, text, 64, dt=0.6) == 0, "hold ends exactly at 1.0s"
    s.offset_for(KEY, text, 64, dt=1.0)
    assert s.offset_for(KEY, text, 64, dt=0.0) < 0, "must have moved left"


def test_scroll_stops_at_the_end_of_the_text():
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    text = "A" * 40
    for _ in range(50):
        offset = s.offset_for(KEY, text, 64, dt=0.1)
    assert offset == -(40 * 4 - 64), "must stop with the last character flush right"


def test_scroll_returns_to_the_start_after_the_trailing_hold():
    s = Scroller(speed_px_s=1000.0, pause_s=0.5)
    text = "A" * 40
    for _ in range(20):
        s.offset_for(KEY, text, 64, dt=0.1)   # reach the end and hold
    for _ in range(20):
        offset = s.offset_for(KEY, text, 64, dt=0.1)
    assert offset == 0, "cycle must return to the start"


def test_changing_the_text_resets_the_scroll():
    """A destination update must not leave the scroller mid-travel."""
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    for _ in range(10):
        s.offset_for(KEY, "A" * 40, 64, dt=0.1)
    assert s.offset_for(KEY, "B" * 40, 64, dt=0.0) == 0


def test_shrinking_the_box_starts_a_scroll():
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    assert s.offset_for(KEY, "A" * 12, box_width=64, dt=0.1) == 0
    for _ in range(5):
        offset = s.offset_for(KEY, "A" * 12, box_width=20, dt=0.1)
    assert offset < 0


def test_keys_are_independent():
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    a, b = (1, "name"), (2, "name")
    for _ in range(10):
        s.offset_for(a, "A" * 40, 64, dt=0.1)
    assert s.offset_for(b, "A" * 40, 64, dt=0.0) == 0


def test_retain_drops_state_for_departed_vessels():
    s = Scroller()
    s.offset_for((1, "name"), "A" * 40, 64, dt=0.1)
    s.offset_for((2, "name"), "A" * 40, 64, dt=0.1)
    s.retain({(1, "name")})
    assert set(s._states) == {(1, "name")}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/render/test_scroll.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.render.scroll'`

- [ ] **Step 3: Implement `ship_observer/render/scroll.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable

from .font import text_width

HOLD_START = "hold_start"
SCROLLING = "scrolling"
HOLD_END = "hold_end"


@dataclass
class _State:
    text: str
    box_width: int
    phase: str = HOLD_START
    offset: float = 0.0
    timer: float = 0.0


class Scroller:
    """Per-field horizontal scrolling.

    Fields that fit stay perfectly still, which keeps the panel calm; only
    overflowing text moves. State is keyed by (mmsi, field) and resets whenever
    the text or the box width changes.
    """

    def __init__(self, speed_px_s: float = 12.0, pause_s: float = 1.5) -> None:
        self._speed = speed_px_s
        self._pause = pause_s
        self._states: dict[Hashable, _State] = {}

    def offset_for(self, key: Hashable, text: str, box_width: int,
                   dt: float) -> int:
        overflow = text_width(text) - box_width
        if overflow <= 0:
            self._states.pop(key, None)
            return 0

        state = self._states.get(key)
        if state is None or state.text != text or state.box_width != box_width:
            state = _State(text=text, box_width=box_width)
            self._states[key] = state

        state.timer += dt
        if state.phase == HOLD_START:
            if state.timer >= self._pause:
                state.phase = SCROLLING
                state.timer = 0.0
        elif state.phase == SCROLLING:
            state.offset -= self._speed * dt
            if state.offset <= -overflow:
                state.offset = float(-overflow)
                state.phase = HOLD_END
                state.timer = 0.0
        elif state.phase == HOLD_END:
            if state.timer >= self._pause:
                state.phase = HOLD_START
                state.offset = 0.0
                state.timer = 0.0

        return int(state.offset)

    def retain(self, keys: set[Hashable]) -> None:
        """Drop state for fields no longer on screen, so the dict cannot grow
        without bound over days of uptime.
        """
        for key in list(self._states):
            if key not in keys:
                del self._states[key]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/render/test_scroll.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/render/scroll.py tests/render/test_scroll.py
git commit -m "feat: per-field horizontal scroll state machine"
```

---

## Task 14: Layout

This is the task that makes the arithmetic in Section 8.1 of the spec real: three 19 px blocks plus a 7 px divider is exactly 64 px.

**Files:**
- Create: `ship_observer/render/layout.py`
- Test: `tests/render/test_layout.py`

**Interfaces:**
- Consumes: `Canvas`, `RGB`, `draw_text`, `text_width` (Task 11), `icon_for` (Task 12), `Scroller` (Task 13), `Slots` (Task 7), `Vessel` (Task 1)
- Produces: `BLOCK_H = 19`, `DIVIDER_H = 7`, `ICON_X = 0`, `TEXT_X = 10`, `NAME_BOX_W = 54`, `LINE2_BOX_W = 64`, `capacity(panel_height) -> int`, `render_frame(canvas, slots, scroller, dt, stale=False) -> None`, `format_line2(vessel) -> str`

- [ ] **Step 1: Write the failing test**

`tests/render/test_layout.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.models import ShipCategory, Vessel
from ship_observer.render.canvas import Canvas
from ship_observer.render.layout import (
    BLOCK_H,
    DIVIDER_H,
    capacity,
    format_line2,
    render_frame,
)
from ship_observer.render.scroll import Scroller
from ship_observer.selection import Slots

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)


def vessel(mmsi=1, name="EVER GIVEN", call_sign="H3RC", destination="SEATTLE",
           category=ShipCategory.CARGO):
    return Vessel(mmsi=mmsi, entered_at=T0, last_seen=T0, name=name,
                  call_sign=call_sign, destination=destination,
                  category=category, static_resolved=True)


def rows_with_content(canvas):
    return {y for y in range(canvas.height) for x in range(canvas.width)
            if canvas.get_pixel(x, y) != (0, 0, 0)}


def test_block_arithmetic_fills_the_panel_exactly():
    """3 * 19 + 7 == 64. This is the constraint the whole layout rests on."""
    assert BLOCK_H * 3 + DIVIDER_H == 64


@pytest.mark.parametrize("height,expected", [(64, 3), (32, 1), (128, 6), (18, 0)])
def test_capacity_is_derived_from_panel_height(height, expected):
    assert capacity(height) == expected


def test_empty_slots_render_a_black_panel():
    c = Canvas(64, 64)
    render_frame(c, Slots(), Scroller(), dt=0.1)
    assert c.to_bytes() == bytes(64 * 64 * 3)


def test_one_ship_occupies_only_the_first_block():
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel()]), Scroller(), dt=0.1)
    assert max(rows_with_content(c)) < BLOCK_H


def test_three_ships_stay_within_the_panel():
    c = Canvas(64, 64)
    slots = Slots(live=[vessel(1), vessel(2, name="WSF PUYALLUP"),
                        vessel(3, name="POLAR RESOLUTE")])
    render_frame(c, slots, Scroller(), dt=0.1)
    assert max(rows_with_content(c)) < BLOCK_H * 3


def test_blocks_are_stacked_at_multiples_of_block_height():
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel(1), vessel(2)]), Scroller(), dt=0.1)
    rows = rows_with_content(c)
    assert any(r < BLOCK_H for r in rows)
    assert any(BLOCK_H <= r < BLOCK_H * 2 for r in rows)
    assert not any(r >= BLOCK_H * 2 for r in rows)


def test_divider_appears_between_live_and_history():
    c = Canvas(64, 64)
    slots = Slots(live=[vessel(1), vessel(2)], history=[vessel(3)],
                  show_divider=True)
    render_frame(c, slots, Scroller(), dt=0.1)
    rows = rows_with_content(c)
    divider_top = BLOCK_H * 2
    assert divider_top in rows, "the divider rule must be drawn"
    assert any(r >= divider_top + DIVIDER_H for r in rows), "history block missing"
    assert max(rows) < 64


def test_two_live_plus_one_history_fills_exactly_64_rows():
    c = Canvas(64, 64)
    slots = Slots(live=[vessel(1), vessel(2)], history=[vessel(3)],
                  show_divider=True)
    render_frame(c, slots, Scroller(), dt=0.1)
    assert BLOCK_H * 2 + DIVIDER_H + BLOCK_H == 64


def test_history_only_starts_with_the_divider_at_the_top():
    c = Canvas(64, 64)
    slots = Slots(history=[vessel(1), vessel(2), vessel(3)], show_divider=True)
    render_frame(c, slots, Scroller(), dt=0.1)
    assert 0 in rows_with_content(c)


def test_no_divider_is_drawn_when_show_divider_is_false():
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel(1)], show_divider=False), Scroller(), dt=0.1)
    assert max(rows_with_content(c)) < BLOCK_H


def test_stale_dims_the_whole_frame():
    bright, dim = Canvas(64, 64), Canvas(64, 64)
    render_frame(bright, Slots(live=[vessel()]), Scroller(), dt=0.1, stale=False)
    render_frame(dim, Slots(live=[vessel()]), Scroller(), dt=0.1, stale=True)
    assert sum(dim.to_bytes()) < sum(bright.to_bytes())
    assert sum(dim.to_bytes()) > 0


@pytest.mark.parametrize("call_sign,destination,expected", [
    ("H3RC", "SEATTLE", "H3RC > SEATTLE"),
    (None, "SEATTLE", "> SEATTLE"),
    ("H3RC", None, "H3RC"),
    (None, None, ""),
])
def test_format_line2(call_sign, destination, expected):
    v = vessel(call_sign=call_sign, destination=destination)
    assert format_line2(v) == expected


def test_unnamed_vessel_falls_back_to_its_mmsi():
    c = Canvas(64, 64)
    v = vessel(name=None)
    render_frame(c, Slots(live=[v]), Scroller(), dt=0.1)
    assert rows_with_content(c), "an unnamed vessel must still render something"


def test_scroller_state_is_pruned_to_on_screen_fields():
    scroller = Scroller()
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel(1, name="A" * 40)]), scroller, dt=0.1)
    render_frame(c, Slots(live=[vessel(2, name="B" * 40)]), scroller, dt=0.1)
    assert all(key[0] == 2 for key in scroller._states)


def test_render_clears_the_canvas_between_frames():
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel(1), vessel(2), vessel(3)]), Scroller(), dt=0.1)
    render_frame(c, Slots(), Scroller(), dt=0.1)
    assert c.to_bytes() == bytes(64 * 64 * 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/render/test_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.render.layout'`

- [ ] **Step 3: Implement `ship_observer/render/layout.py`**

```python
from __future__ import annotations

from typing import Hashable

from ..models import Vessel
from ..selection import Slots
from .canvas import RGB, Canvas
from .font import draw_text, text_width
from .icons import icon_for
from .scroll import Scroller

# Section 8.1 of the spec: 3 * BLOCK_H + DIVIDER_H == 64 exactly.
BLOCK_H = 19
DIVIDER_H = 7

ICON_X = 0
TEXT_X = 10
NAME_BOX_W = 64 - TEXT_X    # 54 px -> 13 characters
LINE2_BOX_W = 64            # 16 characters

NAME_Y_OFFSET = 1           # centres the 6 px text in the 8 px icon band
LINE2_Y_OFFSET = 9
SEPARATOR_Y_OFFSET = 16

NAME_COLOR: RGB = (255, 255, 255)
CALLSIGN_COLOR: RGB = (90, 200, 210)
DEST_COLOR: RGB = (255, 180, 60)
SEPARATOR_COLOR: RGB = (40, 40, 40)
DIVIDER_COLOR: RGB = (120, 120, 120)
DIVIDER_TEXT_COLOR: RGB = (180, 180, 180)

DIVIDER_LABEL = "LAST SEEN"
STALE_DIM = 0.5


def capacity(panel_height: int) -> int:
    """How many ship blocks physically fit. Derived, never hard-coded."""
    return max(0, panel_height // BLOCK_H)


def format_line2(vessel: Vessel) -> str:
    """`CALLSIGN > DESTINATION`, degrading gracefully when either is missing."""
    call_sign = vessel.call_sign or ""
    destination = f"> {vessel.destination}" if vessel.destination else ""
    return " ".join(part for part in (call_sign, destination) if part)


def _draw_scrolling(canvas: Canvas, key: Hashable, text: str, x: int, y: int,
                    box_width: int, rgb: RGB, scroller: Scroller,
                    dt: float) -> None:
    offset = scroller.offset_for(key, text, box_width, dt)
    draw_text(canvas, text, x + offset, y, rgb,
              clip_x0=x, clip_x1=x + box_width - 1)


def _draw_block(canvas: Canvas, vessel: Vessel, y: int, scroller: Scroller,
                dt: float) -> None:
    canvas.blit(icon_for(vessel.category), ICON_X, y)

    _draw_scrolling(canvas, (vessel.mmsi, "name"), vessel.display_name,
                    TEXT_X, y + NAME_Y_OFFSET, NAME_BOX_W, NAME_COLOR,
                    scroller, dt)

    line2 = format_line2(vessel)
    if line2:
        # Callsign and destination are drawn as one scrolling string so they
        # travel together, then re-coloured by character position.
        offset = scroller.offset_for((vessel.mmsi, "line2"), line2,
                                     LINE2_BOX_W, dt)
        split = len(vessel.call_sign or "")
        draw_text(canvas, line2[:split], offset, y + LINE2_Y_OFFSET,
                  CALLSIGN_COLOR, clip_x0=0, clip_x1=LINE2_BOX_W - 1)
        draw_text(canvas, line2[split:], offset + text_width(line2[:split]),
                  y + LINE2_Y_OFFSET, DEST_COLOR,
                  clip_x0=0, clip_x1=LINE2_BOX_W - 1)

    canvas.hline(y + SEPARATOR_Y_OFFSET, 0, canvas.width - 1, SEPARATOR_COLOR)


def _draw_divider(canvas: Canvas, y: int) -> None:
    canvas.hline(y, 0, canvas.width - 1, DIVIDER_COLOR)
    label_x = max(0, (canvas.width - text_width(DIVIDER_LABEL)) // 2)
    draw_text(canvas, DIVIDER_LABEL, label_x, y + 1, DIVIDER_TEXT_COLOR)


def render_frame(canvas: Canvas, slots: Slots, scroller: Scroller,
                 dt: float, stale: bool = False) -> None:
    """Draw one complete frame. The canvas is cleared first."""
    canvas.clear()

    y = 0
    for vessel in slots.live:
        if y + BLOCK_H > canvas.height:
            break
        _draw_block(canvas, vessel, y, scroller, dt)
        y += BLOCK_H

    if slots.show_divider and slots.history:
        if y + DIVIDER_H <= canvas.height:
            _draw_divider(canvas, y)
            y += DIVIDER_H
            for vessel in slots.history:
                if y + BLOCK_H > canvas.height:
                    break
                _draw_block(canvas, vessel, y, scroller, dt)
                y += BLOCK_H

    # Bound the scroller's memory: only fields actually on screen survive.
    on_screen = {(v.mmsi, field)
                 for v in (*slots.live, *slots.history)
                 for field in ("name", "line2")}
    scroller.retain(on_screen)

    if stale:
        canvas.dim(STALE_DIM)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/render/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ship_observer/render/layout.py tests/render/test_layout.py
git commit -m "feat: panel layout with blocks, divider, and stale dimming"
```

---

## Task 15: Display Drivers

`SwapOnVSync()` blocks until the next vsync, so the HUB75 driver owns a thread with a single-slot latest-frame buffer. The async render loop drops frames into the slot and never blocks; the thread renders whatever is current and discards anything stale.

**Files:**
- Create: `ship_observer/drivers/__init__.py`, `base.py`, `null.py`, `rgbmatrix.py`
- Test: `tests/test_drivers.py`

**Interfaces:**
- Consumes: `Settings`
- Produces: `DisplayDriver` Protocol (`width`, `height`, `show(frame: bytes)`, `close()`), `NullDriver(width, height)` with a `frames` list, `create_driver(settings, on_event=None) -> DisplayDriver`

- [ ] **Step 1: Write the failing test**

`tests/test_drivers.py`:

```python
import sys

import pytest

from ship_observer.config import Settings
from ship_observer.drivers import create_driver
from ship_observer.drivers.null import NullDriver

MINIMAL = {"AIS_STREAM_API_KEY": "k",
           "BBOX": "-122.527428,47.859476,-122.323322,47.910359"}


def test_null_driver_records_frames():
    d = NullDriver(64, 64)
    frame = bytes(64 * 64 * 3)
    d.show(frame)
    assert d.frames == [frame]
    assert (d.width, d.height) == (64, 64)
    d.close()


def test_explicit_null_driver_is_honoured():
    s = Settings.from_env({**MINIMAL, "DISPLAY_DRIVER": "null"})
    assert isinstance(create_driver(s), NullDriver)


def test_auto_falls_back_to_null_off_a_pi():
    """Development happens on WSL2; rgbmatrix will not import there."""
    s = Settings.from_env({**MINIMAL, "DISPLAY_DRIVER": "auto"})
    driver = create_driver(s)
    assert isinstance(driver, NullDriver)
    assert (driver.width, driver.height) == (64, 64)


def test_explicit_rgbmatrix_falls_back_and_logs_an_error(monkeypatch):
    """A missing library must not stop the web UI from coming up - that is
    how the failure gets diagnosed remotely.
    """
    monkeypatch.setitem(sys.modules, "rgbmatrix", None)
    s = Settings.from_env({**MINIMAL, "DISPLAY_DRIVER": "rgbmatrix"})
    events = []
    driver = create_driver(s, on_event=lambda *a: events.append(a))
    assert isinstance(driver, NullDriver)
    assert any(e[0] == "ERROR" and e[1] == "display" for e in events)


def test_driver_dimensions_follow_chain_and_parallel():
    s = Settings.from_env({**MINIMAL, "DISPLAY_DRIVER": "null",
                           "MATRIX_CHAIN": "2", "MATRIX_PARALLEL": "1"})
    driver = create_driver(s)
    assert (driver.width, driver.height) == (128, 64)


def test_null_driver_rejects_wrong_sized_frames():
    d = NullDriver(8, 8)
    with pytest.raises(ValueError, match="expected"):
        d.show(bytes(10))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_drivers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.drivers'`

- [ ] **Step 3: Implement the drivers**

`ship_observer/drivers/base.py`:

```python
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DisplayDriver(Protocol):
    width: int
    height: int

    def show(self, frame: bytes) -> None:
        """Present one frame. `frame` is width*height*3 bytes, row-major RGB."""

    def close(self) -> None:
        """Release hardware. Safe to call more than once."""
```

`ship_observer/drivers/null.py`:

```python
from __future__ import annotations


class NullDriver:
    """Records frames instead of displaying them. Used for development on
    non-Pi hosts, for CI, and as the fallback when rgbmatrix is unavailable.
    """

    def __init__(self, width: int, height: int, keep: int = 1) -> None:
        self.width = width
        self.height = height
        self._keep = keep
        self.frames: list[bytes] = []
        self.closed = False

    def show(self, frame: bytes) -> None:
        expected = self.width * self.height * 3
        if len(frame) != expected:
            raise ValueError(f"frame is {len(frame)} bytes, expected {expected}")
        self.frames.append(frame)
        if len(self.frames) > self._keep:
            del self.frames[:-self._keep]

    def close(self) -> None:
        self.closed = True
```

`ship_observer/drivers/rgbmatrix.py`:

```python
from __future__ import annotations

import logging
import threading

from ..config import Settings

log = logging.getLogger(__name__)


class RgbMatrixDriver:
    """HUB75 panel via hzeller/rpi-rgb-led-matrix.

    SwapOnVSync() blocks until the next vsync, so it runs on its own thread
    behind a single-slot buffer. The async render loop never waits on it, and
    frames that arrive faster than the panel can show them are simply dropped.
    """

    def __init__(self, settings: Settings) -> None:
        # Imported lazily: this module must never be imported on a non-Pi host.
        from rgbmatrix import RGBMatrix, RGBMatrixOptions

        options = RGBMatrixOptions()
        options.rows = settings.matrix_rows
        options.cols = settings.matrix_cols
        options.chain_length = settings.matrix_chain
        options.parallel = settings.matrix_parallel
        options.brightness = settings.matrix_brightness
        options.gpio_slowdown = settings.matrix_gpio_slowdown
        options.hardware_mapping = settings.matrix_hardware_mapping
        options.drop_privileges = False

        self._matrix = RGBMatrix(options=options)
        self._canvas = self._matrix.CreateFrameCanvas()
        self.width = settings.panel_width
        self.height = settings.panel_height

        self._pending: bytes | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hub75",
                                        daemon=True)
        self._thread.start()

    def show(self, frame: bytes) -> None:
        """Non-blocking. Replaces any frame not yet drawn."""
        with self._lock:
            self._pending = frame
        self._wake.set()

    def _run(self) -> None:
        from PIL import Image  # provided by the rgbmatrix build

        while not self._stop.is_set():
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            with self._lock:
                frame, self._pending = self._pending, None
            if frame is None:
                continue
            try:
                image = Image.frombytes("RGB", (self.width, self.height), frame)
                self._canvas.SetImage(image)
                self._canvas = self._matrix.SwapOnVSync(self._canvas)
            except Exception:
                log.exception("failed to present a frame")

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2.0)
        try:
            self._matrix.Clear()
        except Exception:
            log.debug("matrix clear failed during shutdown", exc_info=True)
```

`ship_observer/drivers/__init__.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_drivers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/drivers/ tests/test_drivers.py
git commit -m "feat: display drivers with threaded HUB75 output and null fallback"
```

---

## Task 16: Web API

**Files:**
- Create: `ship_observer/web/__init__.py`, `ship_observer/web/server.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `Settings`, `VesselRegistry`, `Storage`, `Slots`, `select_slots`, `capacity`
- Produces:
  - `AppState` — mutable holder shared with `__main__`: `settings`, `registry`, `storage`, `latest_frame: bytes | None`, `connected: bool`, `last_message_at: datetime | None`, `slots: Slots`, `started_at`, plus `vessel_json(v, slot) -> dict` and `state_json() -> dict`
  - `create_app(state: AppState) -> web.Application`
  - `async broadcast_frame(app, frame)` / `async broadcast_state(app)`

- [ ] **Step 1: Write the failing test**

`tests/test_web_api.py`:

```python
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ship_observer.config import Settings
from ship_observer.models import ShipCategory, Vessel
from ship_observer.registry import VesselRegistry
from ship_observer.storage import Storage
from ship_observer.web.server import AppState, create_app

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)
MINIMAL = {"AIS_STREAM_API_KEY": "super-secret",
           "BBOX": "-122.527428,47.859476,-122.323322,47.910359"}


@pytest.fixture
async def client(tmp_path):
    settings = Settings.from_env({**MINIMAL, "DB_PATH": str(tmp_path / "t.db")})
    storage = Storage(settings.db_path)
    await storage.open()
    state = AppState(settings=settings,
                     registry=VesselRegistry(settings),
                     storage=storage)
    test_client = TestClient(TestServer(create_app(state)))
    await test_client.start_server()
    test_client.app_state = state
    yield test_client
    await test_client.close()
    await storage.close()


def vessel(mmsi=1, **overrides):
    base = dict(mmsi=mmsi, entered_at=T0, last_seen=T0, name="EVER GIVEN",
                call_sign="H3RC", destination="SEATTLE",
                category=ShipCategory.CARGO, priority=30, length_m=400.0,
                static_resolved=True)
    base.update(overrides)
    return Vessel(**base)


async def test_index_serves_html(client):
    response = await client.get("/")
    assert response.status == 200
    assert "text/html" in response.headers["Content-Type"]
    assert "canvas" in (await response.text()).lower()


async def test_healthz_reports_liveness_and_message_age(client):
    client.app_state.last_message_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    client.app_state.connected = True
    body = await (await client.get("/healthz")).json()
    assert body["ok"] is True
    assert body["connected"] is True
    assert 4 <= body["last_message_age_seconds"] <= 10


async def test_healthz_when_no_message_has_arrived(client):
    body = await (await client.get("/healthz")).json()
    assert body["last_message_age_seconds"] is None


async def test_state_never_exposes_the_api_key(client):
    text = await (await client.get("/api/state")).text()
    assert "super-secret" not in text
    assert json.loads(text)["config"]["ais_stream_api_key"] == "***redacted***"


async def test_state_surfaces_the_parsed_bounding_box(client):
    """A transposed BBOX paste has to be visible somewhere."""
    body = await (await client.get("/api/state")).json()
    assert body["config"]["bbox"]["lat_min"] == pytest.approx(47.859476)


async def test_state_lists_live_vessels_with_slot_assignment(client):
    registry = client.app_state.registry
    registry._live[1] = vessel(1)
    registry._live[2] = vessel(2, name="SAILBOAT", category=ShipCategory.SAILING,
                               priority=10, length_m=11.0, entered_at=T0)
    body = await (await client.get("/api/state")).json()
    assert {v["mmsi"] for v in body["live"]} == {1, 2}
    assert all("slot" in v and "eligible" in v for v in body["live"])


async def test_state_marks_filtered_vessels_so_exclusions_are_explicable(client, tmp_path):
    settings = Settings.from_env({**MINIMAL, "MIN_LENGTH_METERS": "50",
                                  "DB_PATH": str(tmp_path / "u.db")})
    client.app_state.settings = settings
    client.app_state.registry = VesselRegistry(settings)
    client.app_state.registry._live[2] = vessel(
        2, category=ShipCategory.SAILING, priority=10, length_m=11.0)
    body = await (await client.get("/api/state")).json()
    row = body["live"][0]
    assert row["eligible"] is False
    assert row["slot"] is None
    assert "min_length" in row["filtered_reason"]


async def test_ships_endpoint_returns_logged_visits(client):
    v = vessel(7)
    await client.app_state.storage.begin_visit(v)
    body = await (await client.get("/api/ships")).json()
    assert [row["mmsi"] for row in body["ships"]] == [7]


async def test_ships_endpoint_accepts_filters(client):
    await client.app_state.storage.begin_visit(vessel(1))
    await client.app_state.storage.begin_visit(
        vessel(2, category=ShipCategory.TANKER))
    body = await (await client.get("/api/ships?category=tanker&limit=10")).json()
    assert [row["mmsi"] for row in body["ships"]] == [2]


async def test_events_endpoint(client):
    await client.app_state.storage.log_event("WARN", "ws", "dropped", {"n": 1})
    body = await (await client.get("/api/events?level=WARN")).json()
    assert body["events"][0]["message"] == "dropped"
    assert body["events"][0]["detail"] == {"n": 1}


async def test_traffic_summary_endpoint(client):
    await client.app_state.storage.begin_visit(vessel(1))
    body = await (await client.get("/api/traffic-summary?window=7d")).json()
    assert body["total_visits"] == 1
    assert body["window_seconds"] == 604800


async def test_traffic_summary_rejects_a_bad_window(client):
    response = await client.get("/api/traffic-summary?window=banana")
    assert response.status == 400
    assert "window" in (await response.json())["error"]


async def test_websocket_pushes_state_then_frames(client):
    client.app_state.latest_frame = bytes([1, 2, 3] * (64 * 64))
    async with client.ws_connect("/ws/frames") as ws:
        first = json.loads(await ws.receive_str())
        assert first["type"] == "state"
        second = json.loads(await ws.receive_str())
        assert second["type"] == "frame"
        assert base64.b64decode(second["rgb"]) == client.app_state.latest_frame
        assert second["width"] == 64 and second["height"] == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.web'`

- [ ] **Step 3: Implement `ship_observer/web/server.py`**

```python
from __future__ import annotations

import base64
import json
import logging
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from ..config import Settings
from ..models import Vessel
from ..registry import VesselRegistry
from ..render.layout import capacity
from ..selection import Slots, is_eligible, select_slots
from ..storage import Storage, parse_window

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# AppKey's optional second argument is a *type*; AppState is defined below,
# so both keys are declared untyped rather than with a forward reference.
WEBSOCKETS_KEY = web.AppKey("websockets")
STATE_KEY = web.AppKey("state")


@dataclass
class AppState:
    """Everything the web layer reads. Mutated by __main__'s tasks."""

    settings: Settings
    registry: VesselRegistry
    storage: Storage
    latest_frame: bytes | None = None
    connected: bool = False
    last_message_at: datetime | None = None
    slots: Slots = field(default_factory=Slots)
    dropped_frames: int = 0
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))

    def filtered_reason(self, vessel: Vessel) -> str | None:
        s = self.settings
        if not vessel.static_resolved:
            return None
        if vessel.category in s.exclude_categories:
            return f"excluded_category:{vessel.category.value}"
        if s.min_length_meters > 0 and (
                vessel.length_m is None or vessel.length_m < s.min_length_meters):
            return f"min_length:{s.min_length_meters}m"
        return None

    def vessel_json(self, vessel: Vessel, slot: int | None) -> dict[str, Any]:
        return {
            "mmsi": vessel.mmsi,
            "name": vessel.name,
            "display_name": vessel.display_name,
            "call_sign": vessel.call_sign,
            "destination": vessel.destination,
            "ship_type": vessel.ship_type,
            "category": vessel.category.value,
            "priority": vessel.priority,
            "length_m": vessel.length_m,
            "beam_m": vessel.beam_m,
            "draught_m": vessel.draught_m,
            "eta": vessel.eta,
            "imo": vessel.imo,
            "lat": vessel.last_lat,
            "lon": vessel.last_lon,
            "sog": vessel.max_sog,
            "cog": vessel.last_cog,
            "heading": vessel.last_heading,
            "nav_status": vessel.nav_status,
            "entered_at": vessel.entered_at.isoformat(),
            "last_seen": vessel.last_seen.isoformat(),
            "departed_at": (vessel.departed_at.isoformat()
                            if vessel.departed_at else None),
            "depart_reason": vessel.depart_reason,
            "position_count": vessel.position_count,
            "static_resolved": vessel.static_resolved,
            "eligible": is_eligible(vessel, self.settings),
            "filtered_reason": self.filtered_reason(vessel),
            "slot": slot,
        }

    def state_json(self) -> dict[str, Any]:
        live = self.registry.live()
        departed = self.registry.departed()
        slots = select_slots(live, departed, self.settings,
                             capacity(self.settings.panel_height))
        slot_index = {v.mmsi: i for i, v in enumerate(slots.live)}
        history_index = {v.mmsi: i for i, v in enumerate(slots.history)}
        now = datetime.now(timezone.utc)
        age = ((now - self.last_message_at).total_seconds()
               if self.last_message_at else None)

        return {
            "type": "state",
            "now": now.isoformat(),
            "connected": self.connected,
            "stale": age is None or age > self.settings.stale_seconds,
            "last_message_age_seconds": age,
            "uptime_seconds": (now - self.started_at).total_seconds(),
            "dropped_frames": self.dropped_frames,
            "config": self.settings.redacted(),
            "capacity": capacity(self.settings.panel_height),
            "show_divider": slots.show_divider,
            "live": [self.vessel_json(v, slot_index.get(v.mmsi)) for v in live],
            "history": [self.vessel_json(v, history_index.get(v.mmsi))
                        for v in departed],
        }


def _frame_json(state: AppState) -> dict[str, Any] | None:
    if state.latest_frame is None:
        return None
    return {
        "type": "frame",
        "width": state.settings.panel_width,
        "height": state.settings.panel_height,
        "rgb": base64.b64encode(state.latest_frame).decode("ascii"),
    }


def _since(request: web.Request) -> datetime | None:
    raw = request.query.get("since")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def _healthz(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    age = ((datetime.now(timezone.utc) - state.last_message_at).total_seconds()
           if state.last_message_at else None)
    return web.json_response({
        "ok": True,
        "connected": state.connected,
        "last_message_age_seconds": age,
        "uptime_seconds": (datetime.now(timezone.utc)
                           - state.started_at).total_seconds(),
    })


async def _state(request: web.Request) -> web.Response:
    return web.json_response(request.app[STATE_KEY].state_json())


async def _ships(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    rows = await state.storage.query_ships(
        since=_since(request),
        category=request.query.get("category"),
        limit=int(request.query.get("limit", 200)),
    )
    return web.json_response({"ships": rows})


async def _events(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    rows = await state.storage.query_events(
        since=_since(request),
        level=request.query.get("level"),
        category=request.query.get("category"),
        limit=int(request.query.get("limit", 200)),
    )
    for row in rows:
        if row.get("detail"):
            try:
                row["detail"] = json.loads(row["detail"])
            except ValueError:
                pass
    return web.json_response({"events": rows})


async def _traffic_summary(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    try:
        window = parse_window(request.query.get("window", "7d"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response(await state.storage.traffic_summary(window))


async def _websocket(request: web.Request) -> web.WebSocketResponse:
    state: AppState = request.app[STATE_KEY]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    request.app[WEBSOCKETS_KEY].add(ws)
    try:
        await ws.send_json(state.state_json())
        frame = _frame_json(state)
        if frame is not None:
            await ws.send_json(frame)
        async for message in ws:
            if message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        request.app[WEBSOCKETS_KEY].discard(ws)
    return ws


async def _broadcast(app: web.Application, payload: dict[str, Any]) -> None:
    for ws in list(app[WEBSOCKETS_KEY]):
        if ws.closed:
            continue
        try:
            await ws.send_json(payload)
        except Exception:
            log.debug("dropping a websocket client", exc_info=True)


async def broadcast_frame(app: web.Application, state: AppState) -> None:
    payload = _frame_json(state)
    if payload is not None:
        await _broadcast(app, payload)


async def broadcast_state(app: web.Application, state: AppState) -> None:
    await _broadcast(app, state.state_json())


def create_app(state: AppState) -> web.Application:
    app = web.Application()
    app[STATE_KEY] = state
    app[WEBSOCKETS_KEY] = weakref.WeakSet()
    app.add_routes([
        web.get("/", _index),
        web.get("/healthz", _healthz),
        web.get("/api/state", _state),
        web.get("/api/ships", _ships),
        web.get("/api/events", _events),
        web.get("/api/traffic-summary", _traffic_summary),
        web.get("/ws/frames", _websocket),
        web.static("/static", STATIC_DIR),
    ])
    return app
```

- [ ] **Step 4: Create a placeholder `ship_observer/web/static/index.html`**

Task 17 replaces this. It exists now only so the index route resolves:

```html
<!doctype html>
<html><head><title>Ship Observer</title></head>
<body><canvas id="panel"></canvas></body></html>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_web_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ship_observer/web/ tests/test_web_api.py
git commit -m "feat: aiohttp debug API with websocket frame push"
```

---

## Task 17: Debug Page

No build step, no external assets — the Pi serves this over the LAN and it must work from a cold cache.

**Files:**
- Create: `ship_observer/web/static/index.html`, `app.js`, `style.css`
- Test: `tests/test_web_static.py`

**Interfaces:**
- Consumes: `/api/*` and `/ws/frames` from Task 16
- Produces: no Python interface

- [ ] **Step 1: Write the failing test**

`tests/test_web_static.py`:

```python
import re
from pathlib import Path

import pytest

STATIC = Path("ship_observer/web/static")


def test_all_static_files_exist():
    for name in ("index.html", "app.js", "style.css"):
        assert (STATIC / name).is_file(), f"{name} is missing"


def test_index_references_local_assets_only():
    """The Pi serves this over the LAN; it must work from a cold cache."""
    html = (STATIC / "index.html").read_text()
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert external == [], f"external assets are not allowed: {external}"


def test_index_mounts_every_panel_the_spec_requires():
    html = (STATIC / "index.html").read_text()
    for element_id in ("panel", "live-table", "traffic-summary",
                       "event-log", "config"):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_app_js_connects_to_the_frame_websocket():
    js = (STATIC / "app.js").read_text()
    assert "/ws/frames" in js
    assert "putImageData" in js, "the mirror must be pixel-accurate, not scaled art"


def test_app_js_polls_every_api_endpoint():
    js = (STATIC / "app.js").read_text()
    for endpoint in ("/api/traffic-summary", "/api/events"):
        assert endpoint in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_static.py -v`
Expected: FAIL — `app.js is missing`

- [ ] **Step 3: Write `ship_observer/web/static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ship Observer</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header>
    <h1>Ship Observer</h1>
    <div id="status" class="status">connecting&hellip;</div>
  </header>

  <main>
    <section class="card mirror">
      <h2>Panel mirror</h2>
      <canvas id="panel" width="64" height="64"></canvas>
      <p class="hint">Pixel-accurate mirror of the LED matrix.</p>
    </section>

    <section class="card">
      <h2>Vessels in the box</h2>
      <table id="live-table">
        <thead>
          <tr><th>Slot</th><th>Name</th><th>MMSI</th><th>Category</th>
              <th>Pri</th><th>Len</th><th>Call sign</th><th>Destination</th>
              <th>Status</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </section>

    <section class="card">
      <h2>Traffic summary
        <select id="window">
          <option value="1d">24 hours</option>
          <option value="7d" selected>7 days</option>
        </select>
      </h2>
      <div id="traffic-summary"></div>
    </section>

    <section class="card">
      <h2>Events
        <select id="level">
          <option value="">all</option>
          <option value="INFO" selected>info+</option>
          <option value="WARN">warn+</option>
          <option value="ERROR">error</option>
        </select>
      </h2>
      <div id="event-log"></div>
    </section>

    <section class="card">
      <h2>Configuration</h2>
      <div id="config"></div>
    </section>
  </main>

  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write `ship_observer/web/static/app.js`**

```javascript
"use strict";

const SCALE = 8;
const canvas = document.getElementById("panel");
const ctx = canvas.getContext("2d");
let imageData = null;

function drawFrame(msg) {
  const { width, height, rgb } = msg;
  if (canvas.width !== width * SCALE) {
    canvas.width = width * SCALE;
    canvas.height = height * SCALE;
    ctx.imageSmoothingEnabled = false;
  }
  const binary = atob(rgb);
  if (!imageData || imageData.width !== width) {
    imageData = ctx.createImageData(width, height);
  }
  for (let i = 0, j = 0; i < width * height; i++) {
    imageData.data[j++] = binary.charCodeAt(i * 3);
    imageData.data[j++] = binary.charCodeAt(i * 3 + 1);
    imageData.data[j++] = binary.charCodeAt(i * 3 + 2);
    imageData.data[j++] = 255;
  }
  // Draw at 1:1 into an offscreen buffer, then scale up with smoothing off so
  // every LED pixel stays a hard square.
  const off = new OffscreenCanvas(width, height);
  off.getContext("2d").putImageData(imageData, 0, 0);
  ctx.drawImage(off, 0, 0, canvas.width, canvas.height);
}

function renderStatus(state) {
  const el = document.getElementById("status");
  const age = state.last_message_age_seconds;
  el.textContent = state.connected
    ? `connected · last message ${age === null ? "never" : age.toFixed(0) + "s ago"}`
    : "disconnected";
  el.className = "status " + (state.stale ? "bad" : "good");
}

function renderLive(state) {
  const body = document.querySelector("#live-table tbody");
  const rows = [...state.live, ...state.history.map((v) => ({ ...v, past: true }))];
  body.innerHTML = "";
  for (const v of rows) {
    const tr = document.createElement("tr");
    if (!v.eligible) tr.className = "filtered";
    if (v.past) tr.classList.add("past");
    const status = v.past
      ? `departed (${v.depart_reason})`
      : v.filtered_reason || (v.static_resolved ? "shown" : "awaiting static");
    const cells = [
      v.slot === null || v.slot === undefined ? "—" : v.slot + 1,
      v.display_name, v.mmsi, v.category, v.priority,
      v.length_m === null ? "—" : Math.round(v.length_m) + "m",
      v.call_sign || "—", v.destination || "—", status,
    ];
    for (const value of cells) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function renderConfig(state) {
  const c = state.config;
  const bbox = c.bbox;
  document.getElementById("config").innerHTML = `
    <dl>
      <dt>Bounding box</dt>
      <dd>lon ${bbox.lon_min} → ${bbox.lon_max}<br>
          lat ${bbox.lat_min} → ${bbox.lat_max}</dd>
      <dt>Panel</dt><dd>${c.panel.width}×${c.panel.height}, ${c.display_driver}</dd>
      <dt>Slots</dt><dd>${c.max_ships} (capacity ${state.capacity})</dd>
      <dt>History</dt><dd>${c.display_history ? "on" : "off"}</dd>
      <dt>Min length</dt><dd>${c.min_length_meters} m</dd>
      <dt>Excluded</dt><dd>${c.exclude_categories.join(", ") || "none"}</dd>
      <dt>Priority selection</dt><dd>${c.priority_selection ? "on" : "off"}</dd>
      <dt>Retention</dt><dd>ships ${c.ship_log_days}d · events ${c.event_log_hours}h</dd>
    </dl>`;
}

async function refreshTraffic() {
  const window_ = document.getElementById("window").value;
  const data = await (await fetch(`/api/traffic-summary?window=${window_}`)).json();
  const cats = data.by_category
    .map((r) => `<tr><td>${r.category}</td><td>${r.visits}</td>
                     <td>${r.displayed_visits || 0}</td></tr>`)
    .join("");
  const lengths = data.length_histogram
    .map((r) => `<tr><td>${r.bucket_m}–${r.bucket_m + 9} m</td>
                     <td>${r.visits}</td></tr>`)
    .join("");
  const dwell = data.dwell_by_category
    .map((r) => `<tr><td>${r.category}</td><td>${r.median_minutes} min</td>
                     <td>${r.p90_minutes} min</td></tr>`)
    .join("");
  document.getElementById("traffic-summary").innerHTML = `
    <p>${data.total_visits} visits · ${data.unresolved_static_visits} never
       resolved static data</p>
    <div class="grid">
      <table><thead><tr><th>Category</th><th>Visits</th><th>Shown</th></tr></thead>
        <tbody>${cats}</tbody></table>
      <table><thead><tr><th>Length</th><th>Visits</th></tr></thead>
        <tbody>${lengths}</tbody></table>
      <table><thead><tr><th>Category</th><th>Median dwell</th><th>p90</th></tr></thead>
        <tbody>${dwell}</tbody></table>
    </div>`;
}

async function refreshEvents() {
  const level = document.getElementById("level").value;
  const data = await (await fetch(`/api/events?limit=100&level=${level}`)).json();
  document.getElementById("event-log").innerHTML = data.events
    .map((e) => `<div class="event ${e.level}">
        <span class="ts">${e.ts.slice(11, 19)}</span>
        <span class="lvl">${e.level}</span>
        <span class="cat">${e.category}</span>
        <span class="msg">${e.message}</span></div>`)
    .join("");
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/ws/frames`);
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "frame") {
      drawFrame(msg);
    } else if (msg.type === "state") {
      renderStatus(msg);
      renderLive(msg);
      renderConfig(msg);
    }
  };
  ws.onclose = () => {
    document.getElementById("status").textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}

document.getElementById("window").addEventListener("change", refreshTraffic);
document.getElementById("level").addEventListener("change", refreshEvents);
connect();
refreshTraffic();
refreshEvents();
setInterval(refreshTraffic, 60000);
setInterval(refreshEvents, 5000);
```

- [ ] **Step 5: Write `ship_observer/web/static/style.css`**

```css
:root {
  --bg: #0d1117; --card: #161b22; --line: #30363d;
  --fg: #e6edf3; --muted: #8b949e;
  --good: #3fb950; --bad: #f85149; --warn: #d29922;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1rem; background: var(--bg); color: var(--fg);
  font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
header { display: flex; align-items: baseline; gap: 1rem; margin-bottom: 1rem; }
h1 { font-size: 1.1rem; margin: 0; letter-spacing: 0.08em; text-transform: uppercase; }
h2 { font-size: 0.85rem; margin: 0 0 0.75rem; color: var(--muted);
     text-transform: uppercase; letter-spacing: 0.08em;
     display: flex; gap: 0.5rem; align-items: center; }
.status { font-size: 0.85rem; color: var(--muted); }
.status.good { color: var(--good); }
.status.bad { color: var(--bad); }
main { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); }
.card { background: var(--card); border: 1px solid var(--line);
        border-radius: 6px; padding: 1rem; overflow-x: auto; }
.mirror { display: flex; flex-direction: column; align-items: center; }
canvas { image-rendering: pixelated; border: 1px solid var(--line); background: #000; }
.hint { color: var(--muted); font-size: 0.75rem; margin: 0.5rem 0 0; }
table { border-collapse: collapse; width: 100%; font-size: 0.8rem; }
th, td { text-align: left; padding: 0.25rem 0.5rem; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: normal; }
tr.filtered td { color: var(--muted); text-decoration: line-through; }
tr.past td { opacity: 0.6; font-style: italic; }
.grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
.event { display: grid; grid-template-columns: 4rem 3.5rem 5rem 1fr;
         gap: 0.5rem; font-size: 0.78rem; padding: 0.15rem 0;
         border-bottom: 1px solid var(--line); }
.event .ts, .event .cat { color: var(--muted); }
.event.WARN .lvl { color: var(--warn); }
.event.ERROR .lvl { color: var(--bad); }
#event-log { max-height: 22rem; overflow-y: auto; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.25rem 1rem;
     margin: 0; font-size: 0.8rem; }
dt { color: var(--muted); }
dd { margin: 0; }
select { background: var(--bg); color: var(--fg); border: 1px solid var(--line);
         border-radius: 4px; padding: 0.1rem 0.3rem; font: inherit; font-size: 0.75rem; }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_web_static.py tests/test_web_api.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add ship_observer/web/static/ tests/test_web_static.py
git commit -m "feat: debug page with pixel mirror, vessel table, and traffic summary"
```

---

## Task 18: Service Wiring

The five concurrent tasks, signal handling, and the storage-write policy that keeps logging failures from taking down the display.

**Files:**
- Create: `ship_observer/__main__.py`, `ship_observer/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: everything from Tasks 1–17
- Produces: `Service(settings, driver=None, client=None)` with `async run()`, `async ingest_loop()`, `async render_loop()`, `async registry_prune_loop()`, `async retention_loop()`, `record_event(level, category, message, detail=None)`; and in `__main__.py`, `main() -> int` (synchronous, no arguments) wrapping `async _run() -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_service.py`:

```python
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.ais_client import AisMessage
from ship_observer.config import Settings
from ship_observer.drivers.null import NullDriver
from ship_observer.service import Service

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)
IN_BOX = (47.88, -122.41)


def env(tmp_path, **overrides):
    return {"AIS_STREAM_API_KEY": "k",
            "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
            "DB_PATH": str(tmp_path / "t.db"),
            "DISPLAY_DRIVER": "null",
            **overrides}


class FakeClient:
    """Stands in for AisClient: yields queued messages, then idles."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.connected = True
        self.last_message_at = None
        self.dropped_frames = 0

    async def stream(self):
        for message in self._messages:
            self.last_message_at = message.received_at
            yield message
        while True:
            await asyncio.sleep(3600)


def position(mmsi, at, name="TEST SHIP", lat=IN_BOX[0], lon=IN_BOX[1]):
    return AisMessage(message_type="PositionReport", mmsi=mmsi, received_at=at,
                      meta_name=name, lat=lat, lon=lon,
                      payload={"Sog": 12.0, "Cog": 180.0})


def static(mmsi, at, ship_type=70):
    return AisMessage(message_type="ShipStaticData", mmsi=mmsi, received_at=at,
                      meta_name="EVER GIVEN", lat=IN_BOX[0], lon=IN_BOX[1],
                      payload={"Name": "EVER GIVEN", "CallSign": "H3RC",
                               "Destination": "SEATTLE", "Type": ship_type,
                               "Dimension": {"A": 300, "B": 100, "C": 30, "D": 30}})


@pytest.fixture
async def service(tmp_path):
    s = Service(Settings.from_env(env(tmp_path)),
                driver=NullDriver(64, 64),
                client=FakeClient([]))
    await s.start()
    yield s
    await s.stop()


async def test_ingest_opens_a_visit_row_on_entry(tmp_path):
    settings = Settings.from_env(env(tmp_path))
    svc = Service(settings, driver=NullDriver(64, 64),
                  client=FakeClient([position(1, T0)]))
    await svc.start()
    try:
        task = asyncio.create_task(svc.ingest_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        rows = await svc.storage._fetchall("SELECT * FROM ship_log")
        assert len(rows) == 1
        assert rows[0]["mmsi"] == 1
        assert rows[0]["departed_at"] is None
    finally:
        await svc.stop()


async def test_static_data_updates_the_same_visit_row(tmp_path):
    settings = Settings.from_env(env(tmp_path))
    svc = Service(settings, driver=NullDriver(64, 64),
                  client=FakeClient([position(1, T0),
                                     static(1, T0 + timedelta(seconds=30))]))
    await svc.start()
    try:
        task = asyncio.create_task(svc.ingest_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        rows = await svc.storage._fetchall("SELECT * FROM ship_log")
        assert len(rows) == 1, "static data must not open a second visit"
        assert rows[0]["category"] == "cargo"
        assert rows[0]["call_sign"] == "H3RC"
        assert rows[0]["length_m"] == pytest.approx(400.0)
    finally:
        await svc.stop()


async def test_render_loop_produces_frames_and_publishes_them(service):
    service.state.registry._live[1] = _vessel()
    task = asyncio.create_task(service.render_loop())
    await asyncio.sleep(0.25)
    task.cancel()
    assert service.driver.frames, "the driver must receive frames"
    assert service.state.latest_frame == service.driver.frames[-1]
    assert len(service.state.latest_frame) == 64 * 64 * 3


async def test_render_loop_marks_displayed_vessels(service):
    vessel = _vessel()
    service.state.registry._live[1] = vessel
    vessel.log_id = await service.storage.begin_visit(vessel)
    task = asyncio.create_task(service.render_loop())
    await asyncio.sleep(0.25)
    task.cancel()
    assert vessel.displayed is True


async def test_registry_prune_loop_closes_visits(tmp_path):
    settings = Settings.from_env(env(tmp_path, SHIP_TIMEOUT_SECONDS="30"))
    svc = Service(settings, driver=NullDriver(64, 64), client=FakeClient([]))
    await svc.start()
    try:
        vessel = _vessel(last_seen=datetime.now(timezone.utc) - timedelta(minutes=5))
        vessel.log_id = await svc.storage.begin_visit(vessel)
        svc.state.registry._live[1] = vessel

        await svc.prune_once()

        row = (await svc.storage._fetchall("SELECT * FROM ship_log"))[0]
        assert row["departed_at"] is not None
        assert row["depart_reason"] == "timeout"
    finally:
        await svc.stop()


async def test_retention_loop_deletes_expired_rows(tmp_path):
    settings = Settings.from_env(env(tmp_path, SHIP_LOG_DAYS="1"))
    svc = Service(settings, driver=NullDriver(64, 64), client=FakeClient([]))
    await svc.start()
    try:
        old = _vessel(entered_at=datetime.now(timezone.utc) - timedelta(days=3))
        await svc.storage.begin_visit(old)
        await svc.retention_once()
        assert await svc.storage._fetchall("SELECT * FROM ship_log") == []
    finally:
        await svc.stop()


async def test_storage_failures_never_stop_the_service(service, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(service.storage, "begin_visit", boom)
    service.client = FakeClient([position(1, T0)])
    task = asyncio.create_task(service.ingest_loop())
    await asyncio.sleep(0.1)
    assert not task.done(), "ingest must survive a storage failure"
    task.cancel()


async def test_shutdown_closes_every_open_visit(tmp_path):
    settings = Settings.from_env(env(tmp_path))
    svc = Service(settings, driver=NullDriver(64, 64), client=FakeClient([]))
    await svc.start()
    vessel = _vessel()
    vessel.log_id = await svc.storage.begin_visit(vessel)
    svc.state.registry._live[1] = vessel

    await svc.stop()

    storage = svc.storage
    await storage.open()
    row = (await storage._fetchall("SELECT * FROM ship_log"))[0]
    await storage.close()
    assert row["depart_reason"] == "shutdown"
    assert row["departed_at"] is not None


def _vessel(**overrides):
    from ship_observer.models import ShipCategory, Vessel
    now = datetime.now(timezone.utc)
    base = dict(mmsi=1, entered_at=now, last_seen=now, name="EVER GIVEN",
                call_sign="H3RC", destination="SEATTLE",
                category=ShipCategory.CARGO, priority=30, length_m=400.0,
                static_resolved=True)
    base.update(overrides)
    return Vessel(**base)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.service'`

- [ ] **Step 3: Implement `ship_observer/service.py`**

```python
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from .ais_client import AisClient
from .config import Settings
from .drivers import create_driver
from .drivers.base import DisplayDriver
from .models import Vessel
from .registry import VesselRegistry
from .render.canvas import Canvas
from .render.layout import capacity, render_frame
from .render.scroll import Scroller
from .selection import select_slots
from .storage import Storage
from .web.server import AppState, broadcast_frame, broadcast_state, create_app

log = logging.getLogger(__name__)

REGISTRY_PRUNE_INTERVAL = 30.0     # seconds
RETENTION_INTERVAL = 3600.0        # hourly
VISIT_UPDATE_INTERVAL = 30.0       # how often an open visit row is rewritten


class Service:
    """Owns every concurrent task and the objects they share."""

    def __init__(self, settings: Settings,
                 driver: DisplayDriver | None = None,
                 client: object | None = None) -> None:
        self.settings = settings
        self.storage = Storage(settings.db_path)
        self.registry = VesselRegistry(settings)
        self.driver = driver
        self.client = client
        self.scroller = Scroller()
        self.canvas = Canvas(settings.panel_width, settings.panel_height)
        self.state: AppState | None = None
        self.app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._raw_file = None
        self._last_visit_write: dict[int, float] = {}
        self._render_errors: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        await self.storage.open()
        if self.driver is None:
            self.driver = create_driver(self.settings, self.record_event)
        if self.client is None:
            self.client = AisClient(self.settings, on_event=self.record_event)
        self.state = AppState(settings=self.settings, registry=self.registry,
                              storage=self.storage)
        self.app = create_app(self.state)
        if self.settings.record_raw_path:
            path = Path(self.settings.record_raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._raw_file = path.open("a", encoding="utf-8")

    async def stop(self) -> None:
        # Close every open visit so no ship_log row is left dangling.
        for vessel in self.registry.close_all():
            await self._safe(self.storage.end_visit(vessel, "shutdown"))
        if self._runner is not None:
            await self._runner.cleanup()
        if self.driver is not None:
            self.driver.close()
        if self._raw_file is not None:
            self._raw_file.close()
        await self.storage.close()

    def record_event(self, level: str, category: str, message: str,
                     detail: dict | None = None) -> None:
        """Synchronous callback for AisClient and create_driver.

        Schedules the write rather than awaiting it, so a slow SD card can
        never stall the websocket consumer.
        """
        log.log(logging.getLevelName(level if level != "WARN" else "WARNING"),
                "[%s] %s", category, message)
        try:
            asyncio.get_running_loop().create_task(
                self._safe(self.storage.log_event(level, category, message, detail)))
        except RuntimeError:
            pass   # no loop yet: startup logging only

    @staticmethod
    async def _safe(coro) -> None:
        """Await a storage write, swallowing failures.

        Losing a log row is acceptable; taking down the display is not.
        """
        try:
            await coro
        except Exception:
            log.exception("storage write failed")

    # -- tasks -------------------------------------------------------------

    async def ingest_loop(self) -> None:
        assert self.state is not None and self.client is not None
        async for message in self.client.stream():
            self.state.connected = getattr(self.client, "connected", True)
            self.state.last_message_at = message.received_at

            if self._raw_file is not None:
                self._raw_file.write(json.dumps({
                    "received_at": message.received_at.isoformat(),
                    "message_type": message.message_type,
                    "mmsi": message.mmsi,
                    "meta_name": message.meta_name,
                    "lat": message.lat, "lon": message.lon,
                    "payload": message.payload,
                }) + "\n")
                self._raw_file.flush()

            change = self.registry.apply(message)
            vessel = change.vessel

            if change.entered:
                vessel.log_id = await self._begin_visit(vessel)
                self.record_event("INFO", "registry",
                                  f"entered: {vessel.display_name}",
                                  {"mmsi": vessel.mmsi})
            if change.departed:
                await self._safe(self.storage.end_visit(vessel,
                                                        vessel.depart_reason or "left_bbox"))
                self.record_event("INFO", "registry",
                                  f"departed: {vessel.display_name}",
                                  {"mmsi": vessel.mmsi,
                                   "reason": vessel.depart_reason})
            elif change.static_resolved_now or self._should_write(vessel):
                await self._safe(self.storage.update_visit(vessel))

    async def _begin_visit(self, vessel: Vessel) -> int | None:
        try:
            return await self.storage.begin_visit(vessel)
        except Exception:
            log.exception("could not open a ship_log row for %s", vessel.mmsi)
            return None

    def _should_write(self, vessel: Vessel) -> bool:
        """Throttle in-place visit updates - this is an SD card."""
        now = time.monotonic()
        last = self._last_visit_write.get(vessel.mmsi, 0.0)
        if now - last < VISIT_UPDATE_INTERVAL:
            return False
        self._last_visit_write[vessel.mmsi] = now
        return True

    async def render_loop(self) -> None:
        assert self.state is not None and self.driver is not None
        interval = 1.0 / self.settings.render_fps
        web_interval = 1.0 / self.settings.web_fps
        last = time.monotonic()
        last_web = 0.0

        while True:
            now = time.monotonic()
            dt, last = now - last, now

            slots = select_slots(self.registry.live(), self.registry.departed(),
                                 self.settings, capacity(self.settings.panel_height))
            self.state.slots = slots

            age = None
            if self.state.last_message_at is not None:
                age = (datetime.now(timezone.utc)
                       - self.state.last_message_at).total_seconds()
            stale = age is None or age > self.settings.stale_seconds

            try:
                render_frame(self.canvas, slots, self.scroller, dt, stale=stale)
            except Exception as exc:
                # Log each unique failure once; a per-frame exception would
                # otherwise flood the journal at RENDER_FPS.
                key = f"{type(exc).__name__}: {exc}"
                if key not in self._render_errors:
                    self._render_errors.add(key)
                    log.exception("render failed")
                    self.record_event("ERROR", "display", f"render failed: {key}")
                await asyncio.sleep(interval)
                continue

            frame = self.canvas.to_bytes()
            self.state.latest_frame = frame
            self.driver.show(frame)

            for vessel in slots.live:
                if not vessel.displayed:
                    vessel.displayed = True
                    await self._safe(self.storage.update_visit(vessel))

            if self.app is not None and now - last_web >= web_interval:
                last_web = now
                await broadcast_frame(self.app, self.state)
                await broadcast_state(self.app, self.state)

            await asyncio.sleep(max(0.0, interval - (time.monotonic() - now)))

    async def prune_once(self) -> None:
        for vessel in self.registry.prune():
            await self._safe(self.storage.end_visit(vessel, "timeout"))
            self._last_visit_write.pop(vessel.mmsi, None)
            self.record_event("INFO", "registry",
                              f"pruned: {vessel.display_name}",
                              {"mmsi": vessel.mmsi})

    async def registry_prune_loop(self) -> None:
        while True:
            await asyncio.sleep(REGISTRY_PRUNE_INTERVAL)
            await self.prune_once()

    async def retention_once(self) -> None:
        try:
            ships, events = await self.storage.prune(
                self.settings.ship_log_days, self.settings.event_log_hours)
        except Exception:
            log.exception("retention prune failed")
            return
        if ships or events:
            self.record_event("INFO", "storage", "retention prune",
                              {"ships_deleted": ships, "events_deleted": events})

    async def retention_loop(self) -> None:
        while True:
            await self.retention_once()
            await asyncio.sleep(RETENTION_INTERVAL)

    async def serve_web(self) -> None:
        assert self.app is not None
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.settings.http_host,
                           self.settings.http_port)
        await site.start()
        self.record_event("INFO", "web", "listening",
                          {"host": self.settings.http_host,
                           "port": self.settings.http_port})

    async def run(self) -> None:
        await self.start()
        await self.serve_web()
        self.record_event("INFO", "config", "service started",
                          {"bbox": self.settings.bbox.to_aisstream()})
        tasks = [asyncio.create_task(coro) for coro in (
            self.ingest_loop(), self.render_loop(),
            self.registry_prune_loop(), self.retention_loop(),
        )]
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.exception() is not None:
                    log.error("task failed", exc_info=task.exception())
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self.stop()
```

- [ ] **Step 4: Implement `ship_observer/__main__.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_service.py -v`
Expected: PASS

- [ ] **Step 6: Verify the config gate by hand**

```bash
env -u AIS_STREAM_API_KEY BBOX=nonsense python -m ship_observer; echo "exit=$?"
```
Expected: `Configuration error: AIS_STREAM_API_KEY is required but was not set` and `exit=2`

- [ ] **Step 7: Commit**

```bash
git add ship_observer/service.py ship_observer/__main__.py tests/test_service.py
git commit -m "feat: service wiring with five supervised tasks"
```

---

## Task 19: Replay Harness

This is what substitutes for the historical backfill that AISStream cannot provide: it gives backtesting from first boot forward, and the same fixtures drive Task 20's integration tests.

**Files:**
- Create: `ship_observer/replay.py`
- Test: `tests/test_replay.py`

**Interfaces:**
- Consumes: `AisMessage`, `Service`, `NullDriver`
- Produces: `read_session(path) -> Iterator[AisMessage]`, `ReplayClient(messages, speed=0.0)` matching `AisClient`'s `stream()`/`connected`/`last_message_at` interface, `async replay(path, settings, speed) -> dict`, `main(argv=None) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_replay.py`:

```python
import json
from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.config import Settings
from ship_observer.replay import ReplayClient, read_session, replay

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)


def write_session(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def record(mmsi, offset_s, message_type="PositionReport", **payload):
    return {
        "received_at": (T0 + timedelta(seconds=offset_s)).isoformat(),
        "message_type": message_type,
        "mmsi": mmsi,
        "meta_name": "TEST SHIP",
        "lat": 47.88, "lon": -122.41,
        "payload": payload or {"Sog": 10.0},
    }


def test_read_session_parses_jsonl(tmp_path):
    path = write_session(tmp_path / "s.jsonl", [record(1, 0), record(2, 5)])
    messages = list(read_session(path))
    assert [m.mmsi for m in messages] == [1, 2]
    assert messages[0].received_at == T0
    assert messages[0].message_type == "PositionReport"


def test_read_session_skips_corrupt_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(record(1, 0)) + "\nnot json\n{}\n"
                    + json.dumps(record(2, 1)) + "\n")
    assert [m.mmsi for m in read_session(path)] == [1, 2]


def test_read_session_on_an_empty_file(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text("")
    assert list(read_session(path)) == []


async def test_replay_client_yields_then_stops(tmp_path):
    path = write_session(tmp_path / "s.jsonl", [record(1, 0), record(1, 5)])
    client = ReplayClient(list(read_session(path)), speed=0.0)
    messages = [m async for m in client.stream()]
    assert len(messages) == 2
    assert client.last_message_at == T0 + timedelta(seconds=5)


async def test_replay_populates_the_ship_log(tmp_path):
    path = write_session(tmp_path / "s.jsonl", [
        record(1, 0),
        record(1, 30, "ShipStaticData", Name="EVER GIVEN", CallSign="H3RC",
               Destination="SEATTLE", Type=70,
               Dimension={"A": 300, "B": 100, "C": 30, "D": 30}),
        record(2, 60),
    ])
    settings = Settings.from_env({
        "AIS_STREAM_API_KEY": "k",
        "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
        "DB_PATH": str(tmp_path / "replay.db"),
        "DISPLAY_DRIVER": "null",
    })

    result = await replay(path, settings, speed=0.0)

    assert result["messages"] == 3
    assert result["visits"] == 2
    assert result["by_category"]["cargo"] == 1


async def test_replay_never_touches_the_network(tmp_path, monkeypatch):
    """A replay that dialled AISStream would be worse than useless."""
    import websockets

    def explode(*args, **kwargs):
        raise AssertionError("replay must not open a websocket")

    monkeypatch.setattr(websockets, "connect", explode)
    path = write_session(tmp_path / "s.jsonl", [record(1, 0)])
    settings = Settings.from_env({
        "AIS_STREAM_API_KEY": "k",
        "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
        "DB_PATH": str(tmp_path / "r.db"),
        "DISPLAY_DRIVER": "null",
    })
    await replay(path, settings, speed=0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship_observer.replay'`

- [ ] **Step 3: Implement `ship_observer/replay.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_replay.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ship_observer/replay.py tests/test_replay.py
git commit -m "feat: replay harness for recorded AIS sessions"
```

---

## Task 20: End-to-End Integration Tests

A fake AISStream websocket server driving the real client, registry, storage, and renderer.

**Files:**
- Create: `tests/integration/test_end_to_end.py`, `tests/integration/fake_aisstream.py`
- Test: itself

**Interfaces:**
- Consumes: everything
- Produces: `FakeAisStream` — a plain class (NOT an async context manager) exposing `.connect(url, **kwargs)`, `.subscriptions`, `.connections`, `.drop_next_connection()`; the object `.connect()` returns is the async context manager. Also `envelope(mmsi, message_type, **payload)`.

- [ ] **Step 1: Make `tests/integration` a package**

`test_end_to_end.py` imports `from .fake_aisstream import ...`, which requires
an `__init__.py`:

```bash
mkdir -p tests/integration
touch tests/integration/__init__.py
```

- [ ] **Step 3: Write `tests/integration/fake_aisstream.py`**

```python
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
```

- [ ] **Step 3: Write `tests/integration/test_end_to_end.py`**

```python
import asyncio
import json

import pytest

from ship_observer.ais_client import AisClient
from ship_observer.config import Settings
from ship_observer.drivers.null import NullDriver
from ship_observer.service import Service

from .fake_aisstream import FakeAisStream, envelope

CARGO_STATIC = dict(Name="EVER GIVEN", CallSign="H3RC", Destination="SEATTLE",
                    Type=70, Dimension={"A": 300, "B": 100, "C": 30, "D": 30})
SAIL_STATIC = dict(Name="WINDSONG", CallSign="WDX1", Destination="PORT LUDLOW",
                   Type=36, Dimension={"A": 8, "B": 4, "C": 2, "D": 2})


def env(tmp_path, **overrides):
    return {"AIS_STREAM_API_KEY": "test-key",
            "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
            "DB_PATH": str(tmp_path / "e2e.db"),
            "DISPLAY_DRIVER": "null",
            **overrides}


async def run_service(settings, server, seconds=0.3):
    service = Service(settings, driver=NullDriver(settings.panel_width,
                                                  settings.panel_height))
    await service.start()
    service.client = AisClient(settings, on_event=service.record_event,
                               connect=server.connect,
                               backoff_base=0.01, backoff_max=0.01)
    ingest = asyncio.create_task(service.ingest_loop())
    render = asyncio.create_task(service.render_loop())
    await asyncio.sleep(seconds)
    ingest.cancel()
    render.cancel()
    return service


async def test_full_pipeline_from_websocket_to_frame(tmp_path):
    server = FakeAisStream([
        envelope(1, Sog=12.0, _name="EVER GIVEN"),
        envelope(1, "ShipStaticData", **CARGO_STATIC),
    ])
    settings = Settings.from_env(env(tmp_path))

    service = await run_service(settings, server)
    try:
        # The subscription went out in AISStream's exact shape.
        assert server.subscriptions[0]["APIKey"] == "test-key"
        assert server.subscriptions[0]["BoundingBoxes"] == [
            [[47.859476, -122.527428], [47.910359, -122.323322]]]

        rows = await service.storage._fetchall("SELECT * FROM ship_log")
        assert len(rows) == 1
        assert rows[0]["name"] == "EVER GIVEN"
        assert rows[0]["category"] == "cargo"
        assert rows[0]["length_m"] == pytest.approx(400.0)
        assert rows[0]["displayed"] == 1

        assert service.driver.frames
        assert len(service.driver.frames[-1]) == 64 * 64 * 3
        assert any(service.driver.frames[-1]), "the panel must not be black"
    finally:
        await service.stop()


async def test_reconnect_resubscribes_and_logs(tmp_path):
    server = FakeAisStream([envelope(1, Sog=10.0)])
    server.drop_next_connection()
    settings = Settings.from_env(env(tmp_path))

    service = await run_service(settings, server, seconds=0.4)
    try:
        assert server.connections >= 2, "must reconnect after the drop"
        assert len(server.subscriptions) >= 2, "must resubscribe"
        events = await service.storage.query_events(category="ws")
        assert any("disconnected" in e["message"] for e in events)
        assert any("reconnecting" in e["message"] for e in events)
    finally:
        await service.stop()


async def test_small_craft_is_logged_but_kept_off_the_panel(tmp_path):
    """The core noise-control guarantee: filters affect the panel, not the log."""
    server = FakeAisStream([
        envelope(2, Sog=5.0, _name="WINDSONG"),
        envelope(2, "ShipStaticData", **SAIL_STATIC),
    ])
    settings = Settings.from_env(env(tmp_path, MIN_LENGTH_METERS="50"))

    service = await run_service(settings, server)
    try:
        rows = await service.storage._fetchall("SELECT * FROM ship_log")
        assert len(rows) == 1, "the sailboat must still be logged"
        assert rows[0]["category"] == "sailing"
        assert rows[0]["displayed"] == 0, "but never shown"
        assert service.driver.frames[-1] == bytes(64 * 64 * 3), "panel is black"
    finally:
        await service.stop()


async def test_priority_selection_gives_slots_to_the_big_ships(tmp_path):
    frames = []
    for mmsi in (10, 11, 12):    # three sailboats
        frames.append(envelope(mmsi, Sog=5.0))
        frames.append(envelope(mmsi, "ShipStaticData", **SAIL_STATIC))
    frames.append(envelope(20, Sog=14.0))   # then a cargo ship
    frames.append(envelope(20, "ShipStaticData", **CARGO_STATIC))

    settings = Settings.from_env(env(tmp_path))
    service = await run_service(FakeAisStream(frames), settings)
    try:
        assert len(service.registry.live()) == 4
        assert 20 in {v.mmsi for v in service.state.slots.live}, (
            "the cargo ship must hold a slot despite entering last")
        rows = await service.storage._fetchall(
            "SELECT mmsi FROM ship_log ORDER BY mmsi")
        assert [r["mmsi"] for r in rows] == [10, 11, 12, 20], "all four logged"
    finally:
        await service.stop()


async def test_out_of_box_position_departs_and_closes_the_visit(tmp_path):
    server = FakeAisStream([
        envelope(1, Sog=12.0),
        envelope(1, Sog=12.0, _lat=47.70),   # south of the box
    ])
    settings = Settings.from_env(env(tmp_path))

    service = await run_service(server=server, settings=settings)
    try:
        row = (await service.storage._fetchall("SELECT * FROM ship_log"))[0]
        assert row["depart_reason"] == "left_bbox"
        assert row["departed_at"] is not None
        assert service.registry.live() == []
    finally:
        await service.stop()


async def test_recorded_session_replays_to_the_same_result(tmp_path):
    """Recording and replay must round-trip - this is the backtest guarantee."""
    from ship_observer.replay import replay

    recording = tmp_path / "session.jsonl"
    server = FakeAisStream([
        envelope(1, Sog=12.0, _name="EVER GIVEN"),
        envelope(1, "ShipStaticData", **CARGO_STATIC),
    ])
    live_settings = Settings.from_env(
        env(tmp_path, RECORD_RAW_PATH=str(recording)))
    service = await run_service(live_settings, server)
    await service.stop()

    assert recording.exists()
    assert len(recording.read_text().strip().splitlines()) == 2

    replay_settings = Settings.from_env(
        env(tmp_path, DB_PATH=str(tmp_path / "replay.db")))
    result = await replay(recording, replay_settings, speed=0.0)

    assert result["visits"] == 1
    assert result["by_category"] == {"cargo": 1}
```

- [ ] **Step 4: Run the integration tests**

Run: `pytest tests/integration -v`
Expected: PASS

- [ ] **Step 5: Run the entire suite**

Run: `pytest -v`
Expected: PASS, no failures, no errors

- [ ] **Step 6: Commit**

```bash
git add tests/integration/
git commit -m "test: end-to-end pipeline, reconnect, filtering, and replay round-trip"
```

---

## Task 21: Deployment Artifacts

**Files:**
- Create: `deploy/ship-observer.service`, `deploy/install.sh`
- Modify: `pyproject.toml` (add the `pi` extra for Pillow)
- Test: `tests/test_deploy.py`

**Interfaces:**
- Consumes: `ship_observer.__main__`
- Produces: no Python interface

- [ ] **Step 1: Add the `pi` extra to `pyproject.toml`**

`ship_observer/drivers/rgbmatrix.py` imports `PIL.Image` — per-pixel `SetPixel` calls would be far too slow for 4096 pixels at 15 fps, so `SetImage` is the only viable path. Pillow is Pi-only, so it goes in its own extra:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]
pi = ["Pillow>=10.0"]
```

- [ ] **Step 2: Write the failing test**

`tests/test_deploy.py`:

```python
import configparser
import stat
from pathlib import Path

DEPLOY = Path("deploy")


def test_service_unit_exists_and_parses():
    parser = configparser.ConfigParser()
    parser.read(DEPLOY / "ship-observer.service")
    assert parser.has_section("Unit")
    assert parser.has_section("Service")
    assert parser.has_section("Install")


def test_service_restarts_forever():
    parser = configparser.ConfigParser()
    parser.read(DEPLOY / "ship-observer.service")
    assert parser["Service"]["Restart"] == "always"
    assert int(parser["Service"]["RestartSec"]) >= 1


def test_service_waits_for_the_network():
    parser = configparser.ConfigParser()
    parser.read(DEPLOY / "ship-observer.service")
    assert "network-online.target" in parser["Unit"]["After"]
    assert "network-online.target" in parser["Unit"]["Wants"]


def test_service_loads_the_env_file_and_runs_the_module():
    parser = configparser.ConfigParser()
    parser.read(DEPLOY / "ship-observer.service")
    assert ".env" in parser["Service"]["EnvironmentFile"]
    assert "-m ship_observer" in parser["Service"]["ExecStart"]


def test_install_script_is_executable_and_strict():
    script = DEPLOY / "install.sh"
    assert script.is_file()
    assert script.stat().st_mode & stat.S_IXUSR, "install.sh must be executable"
    body = script.read_text()
    assert body.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in body


def test_install_script_covers_every_provisioning_step():
    body = (DEPLOY / "install.sh").read_text()
    for needle in ("snd_bcm2835", "isolcpus", "rpi-rgb-led-matrix",
                   "/var/lib/ship-observer", "pyenv", "systemctl enable"):
        assert needle in body, f"install.sh never mentions {needle}"


def test_install_script_does_not_hardcode_a_secret():
    body = (DEPLOY / "install.sh").read_text()
    assert "AIS_STREAM_API_KEY=" not in body.replace("AIS_STREAM_API_KEY=your", "")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_deploy.py -v`
Expected: FAIL — the `deploy/` files do not exist

- [ ] **Step 4: Write `deploy/ship-observer.service`**

```ini
[Unit]
Description=Ship Observer AIS display
Documentation=file:///opt/ship-observer/docs/raspberry-pi-setup.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# The HUB75 library needs direct GPIO and /dev/mem access, which requires root.
# See docs/raspberry-pi-setup.md for the unprivileged alternative.
User=root
WorkingDirectory=/opt/ship-observer
EnvironmentFile=/opt/ship-observer/.env
ExecStart=/opt/ship-observer/venv/bin/python -m ship_observer
Restart=always
RestartSec=5
TimeoutStopSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ship-observer

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Write `deploy/install.sh`**

```bash
#!/usr/bin/env bash
# Provision a Raspberry Pi to run Ship Observer.
# Follow docs/raspberry-pi-setup.md for the narrated version of these steps.
set -euo pipefail

PYTHON_VERSION="3.11.13"
VENV_NAME="ship_observer"
INSTALL_DIR="/opt/ship-observer"
DATA_DIR="/var/lib/ship-observer"
MATRIX_SRC="${HOME}/src/rpi-rgb-led-matrix"
CMDLINE="/boot/firmware/cmdline.txt"
[[ -f "${CMDLINE}" ]] || CMDLINE="/boot/cmdline.txt"

log() { printf '\n=== %s ===\n' "$1"; }

log "Installing system packages"
sudo apt-get update
sudo apt-get install -y \
  git curl build-essential pkg-config \
  python3-dev cython3 \
  libgraphicsmagick++-dev libwebp-dev \
  libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
  libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev \
  libffi-dev liblzma-dev libjpeg-dev libfreetype6-dev

log "Blacklisting snd_bcm2835"
# The matrix library drives the panel with the same PWM hardware the onboard
# sound uses. Leaving the sound module loaded causes visible flicker.
sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf >/dev/null <<'EOF'
blacklist snd_bcm2835
EOF
sudo update-initramfs -u

log "Reserving CPU core 3 (isolcpus)"
# Pinning the panel's refresh thread to an isolated core removes the jitter
# that shows up as horizontal tearing.
if ! grep -q "isolcpus=3" "${CMDLINE}"; then
  sudo sed -i '1 s/$/ isolcpus=3/' "${CMDLINE}"
  echo "Added isolcpus=3 to ${CMDLINE} (takes effect after reboot)"
else
  echo "isolcpus=3 already present"
fi

log "Installing pyenv and Python ${PYTHON_VERSION}"
if [[ ! -d "${HOME}/.pyenv" ]]; then
  curl -fsSL https://pyenv.run | bash
fi
export PYENV_ROOT="${HOME}/.pyenv"
export PATH="${PYENV_ROOT}/bin:${PATH}"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"
pyenv install -s "${PYTHON_VERSION}"
pyenv virtualenv -f "${PYTHON_VERSION}" "${VENV_NAME}"
VENV_PYTHON="${PYENV_ROOT}/versions/${VENV_NAME}/bin/python"

log "Building rpi-rgb-led-matrix"
# Not on PyPI - it must be built from source against this interpreter.
mkdir -p "$(dirname "${MATRIX_SRC}")"
if [[ ! -d "${MATRIX_SRC}" ]]; then
  git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix.git "${MATRIX_SRC}"
fi
make -C "${MATRIX_SRC}" build-python PYTHON="${VENV_PYTHON}"
make -C "${MATRIX_SRC}" install-python PYTHON="${VENV_PYTHON}"

log "Installing ship-observer"
sudo mkdir -p "${INSTALL_DIR}" "${DATA_DIR}"
sudo chown -R "$(id -u):$(id -g)" "${INSTALL_DIR}"
rsync -a --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '.env' \
  "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/" "${INSTALL_DIR}/"
ln -sfn "${PYENV_ROOT}/versions/${VENV_NAME}" "${INSTALL_DIR}/venv"
"${VENV_PYTHON}" -m pip install --upgrade pip
"${VENV_PYTHON}" -m pip install -e "${INSTALL_DIR}[pi]"

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
  chmod 600 "${INSTALL_DIR}/.env"
  echo "Created ${INSTALL_DIR}/.env - edit it and set AIS_STREAM_API_KEY and BBOX."
fi

log "Installing the systemd unit"
sudo cp "${INSTALL_DIR}/deploy/ship-observer.service" \
  /etc/systemd/system/ship-observer.service
sudo systemctl daemon-reload
sudo systemctl enable ship-observer.service

cat <<EOF

Done. Remaining steps:
  1. Edit ${INSTALL_DIR}/.env and set AIS_STREAM_API_KEY and BBOX.
  2. sudo reboot          (needed for the blacklist and isolcpus)
  3. sudo systemctl start ship-observer
  4. Open http://\$(hostname -I | awk '{print \$1}'):8080/

EOF
```

Then: `chmod +x deploy/install.sh`

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_deploy.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add deploy/ pyproject.toml tests/test_deploy.py
git commit -m "feat: systemd unit and Pi provisioning script"
```

---

## Task 22: Raspberry Pi Setup Runbook

A standalone, follow-along document — usable months from now without reference to the spec or the plan.

**Files:**
- Create: `docs/raspberry-pi-setup.md`
- Test: `tests/test_docs.py`

**Interfaces:**
- Consumes: `deploy/install.sh`, `.env.example`
- Produces: no Python interface

- [ ] **Step 1: Write the failing test**

`tests/test_docs.py`:

```python
import re
from pathlib import Path

DOC = Path("docs/raspberry-pi-setup.md")


def test_the_runbook_exists():
    assert DOC.is_file()


def test_runbook_covers_every_required_section():
    body = DOC.read_text().lower()
    for topic in [
        "raspberry pi imager", "ssh", "power", "wiring",
        "snd_bcm2835", "isolcpus", "pyenv", "rpi-rgb-led-matrix",
        "gpio", "/var/lib/ship-observer", ".env",
        "systemctl", "verification", "troubleshooting",
    ]:
        assert topic in body, f"the runbook never covers {topic}"


def test_runbook_warns_about_panel_power():
    """A 64x64 panel can pull ~4 A. Powering it from the Pi destroys the Pi."""
    body = DOC.read_text().lower()
    assert re.search(r"\b4\s*a\b|\bamp", body)
    assert "separate" in body


def test_runbook_documents_the_env_variables_that_must_be_set():
    body = DOC.read_text()
    assert "AIS_STREAM_API_KEY" in body
    assert "BBOX" in body


def test_runbook_verification_is_layered():
    body = DOC.read_text().lower()
    for check in ("demo", "systemctl status", "/healthz"):
        assert check in body, f"verification never uses {check}"


def test_troubleshooting_covers_the_real_failures():
    body = DOC.read_text().lower()
    for symptom in ("flicker", "blank", "hardware-mapping", "bbox"):
        assert symptom in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_docs.py -v`
Expected: FAIL — `docs/raspberry-pi-setup.md` does not exist

- [ ] **Step 3: Write `docs/raspberry-pi-setup.md`**

````markdown
# Raspberry Pi Setup — Ship Observer

Provision a Pi from a blank SD card to a working deck display. Follow the
sections in order; each one ends in a state you can verify before moving on.

Budget about 90 minutes, most of it waiting on `pyenv install` and the
`rpi-rgb-led-matrix` build.

## What you need

| Item | Notes |
|---|---|
| Raspberry Pi 4 (2 GB or better) | A Pi 3 works; set `MATRIX_GPIO_SLOWDOWN=2` instead of 4 |
| microSD card, 16 GB+ | |
| 64x64 HUB75 RGB LED panel | 1/32 scan, the common P2/P3 type |
| Adafruit RGB Matrix Bonnet or HAT | Direct GPIO wiring works but is far more error-prone |
| **Separate** 5 V power supply, 4 A minimum, for the panel | See the power warning below |
| USB-C supply for the Pi | The official 5 V 3 A one |

### Power — read this before wiring anything

**A 64x64 panel can draw about 4 A at full white.** The Pi's 5 V rail cannot
supply that. Powering the panel from the Pi's GPIO header or USB will brown out
the Pi and can damage it.

Use a **separate** 5 V supply of at least 4 A wired to the panel's own power
terminals. The Adafruit bonnet has a barrel jack for exactly this. The panel and
the Pi must share a common ground — the bonnet handles this for you; if you are
wiring directly to GPIO, connect the grounds yourself.

## 1. Image the SD card

Use **Raspberry Pi Imager**.

1. Choose **Raspberry Pi OS Lite (64-bit)** — Bookworm. No desktop is needed and
   it leaves more CPU for the panel refresh.
2. Click the gear (Advanced options) and set:
   - Hostname: `ship-observer`
   - **Enable SSH**, with password or public-key authentication
   - Username and password
   - Wi-Fi SSID, password, and country — or skip if you are using Ethernet
   - Locale and timezone
3. Write the card, then boot the Pi with it.

Verify:

```bash
ssh <your-user>@ship-observer.local
```

If `.local` does not resolve, find the Pi's address from your router and use
that instead.

## 2. Wire the panel

With the **Adafruit RGB Matrix Bonnet**:

1. Power the Pi down and unplug it.
2. Seat the bonnet on the 40-pin header.
3. Connect the panel's 16-pin HUB75 ribbon to the bonnet's output. The panel's
   input side is usually marked with an arrow pointing away from it — get this
   backwards and the panel simply stays dark.
4. Connect the panel's power harness to the bonnet's screw terminals, watching
   polarity, then plug the 5 V 4 A supply into the bonnet's barrel jack.
5. Power the Pi separately over USB-C.

### The PWM modification (strongly recommended)

Solder a jumper between **GPIO4 and GPIO18** on the bonnet. This lets the
library use hardware PWM, which removes most visible flicker. With the jumper
in place use `MATRIX_HARDWARE_MAPPING=adafruit-hat-pwm`; without it, use
`adafruit-hat`.

## 3. Run the installer

Clone the repo onto the Pi and run the provisioning script:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone <your-repo-url> ~/ship-observer
cd ~/ship-observer
./deploy/install.sh
```

The script does everything in sections 4 through 8. Read on to understand what
it did and how to verify each part — or to do any step by hand if the script
fails partway.

## 4. Blacklist the onboard sound module

The matrix library drives the panel using the same PWM peripheral that the
onboard sound output uses. If `snd_bcm2835` is loaded, the panel flickers.

```bash
sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf <<'EOF'
blacklist snd_bcm2835
EOF
sudo update-initramfs -u
```

Verify after rebooting: `lsmod | grep snd_bcm2835` returns nothing.

## 5. Isolate a CPU core

The panel is refreshed by a tight timing loop. Letting Linux schedule other work
on the same core produces horizontal tearing. Reserving core 3 for it fixes that.

Edit `/boot/firmware/cmdline.txt` (on pre-Bookworm images it is
`/boot/cmdline.txt`). It is a **single line** — append to it, never add a new
line:

```
... rootwait isolcpus=3
```

Verify after rebooting: `cat /sys/devices/system/cpu/isolated` prints `3`.

## 6. Install Python 3.11.13 via pyenv

Raspberry Pi OS Bookworm ships Python 3.11, but pyenv pins the exact version so
the Pi and your development machine agree.

```bash
curl -fsSL https://pyenv.run | bash
```

Add to `~/.bashrc`:

```bash
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"
```

Then, in a fresh shell:

```bash
pyenv install 3.11.13
pyenv virtualenv 3.11.13 ship_observer
```

Verify: `~/.pyenv/versions/ship_observer/bin/python --version` prints
`Python 3.11.13`.

Building CPython on a Pi takes 15–25 minutes. If it fails, a build dependency is
missing — `install.sh` installs the full list.

## 7. Build rpi-rgb-led-matrix

The `rgbmatrix` module is **not on PyPI**. It must be compiled from source
against the exact interpreter that will run the service.

```bash
git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix.git ~/src/rpi-rgb-led-matrix
cd ~/src/rpi-rgb-led-matrix
VENV_PYTHON=~/.pyenv/versions/ship_observer/bin/python
make build-python PYTHON=$VENV_PYTHON
make install-python PYTHON=$VENV_PYTHON
```

Verify:

```bash
~/.pyenv/versions/ship_observer/bin/python -c "import rgbmatrix; print('ok')"
```

## 8. Install the service

```bash
sudo mkdir -p /opt/ship-observer /var/lib/ship-observer
sudo rsync -a --exclude .git --exclude .env ~/ship-observer/ /opt/ship-observer/
sudo ln -sfn ~/.pyenv/versions/ship_observer /opt/ship-observer/venv
sudo /opt/ship-observer/venv/bin/pip install -e '/opt/ship-observer[pi]'
```

`/var/lib/ship-observer` holds the SQLite log and must be writable by whatever
user the service runs as.

### GPIO access: root, or not

The matrix library needs `/dev/mem` and precise timing, which in practice means
**running as root**. The provided unit does exactly that, with
`drop_privileges=False` in the driver.

The alternative is letting the library drop to the `daemon` user after
initialising the hardware — but then `/var/lib/ship-observer` has to be owned by
`daemon`, and the port binding has to happen first. It is more moving parts for
a device on your own LAN. Run as root unless you have a specific reason not to.

### Configure

```bash
sudo cp /opt/ship-observer/.env.example /opt/ship-observer/.env
sudo chmod 600 /opt/ship-observer/.env
sudo nano /opt/ship-observer/.env
```

At minimum, set:

- `AIS_STREAM_API_KEY` — from your aisstream.io account
- `BBOX` — `lon_min,lat_min,lon_max,lat_max`, longitude first. The current deck
  box is `-122.527428,47.859476,-122.323322,47.910359`. Grab new corners from
  [bboxfinder.com](http://bboxfinder.com), which emits them in exactly this
  order.
- `MATRIX_HARDWARE_MAPPING` — `adafruit-hat-pwm` if you did the PWM solder
  jumper, `adafruit-hat` if you did not.
- `MATRIX_GPIO_SLOWDOWN` — `4` on a Pi 4, `2` on a Pi 3.

Then install and enable the unit:

```bash
sudo cp /opt/ship-observer/deploy/ship-observer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ship-observer
sudo reboot
```

The reboot is required: the sound blacklist and `isolcpus` only take effect on
boot.

## 9. Verification

Check each layer in order. If one fails, fix it before moving to the next —
debugging the top of the stack while the bottom is broken wastes hours.

**Layer 1 — the panel itself, independent of this project:**

```bash
cd ~/src/rpi-rgb-led-matrix
sudo ./examples-api-use/demo -D0 --led-rows=64 --led-cols=64 \
  --led-gpio-mapping=adafruit-hat-pwm --led-slowdown-gpio=4
```

Expected: a rotating coloured square. Ctrl-C to stop. If this does not work, no
amount of application debugging will help — go to Troubleshooting.

**Layer 2 — the service starts and stays up:**

```bash
sudo systemctl start ship-observer
sudo systemctl status ship-observer
```

Expected: `active (running)`. If it shows `activating (auto-restart)`, it is
crash-looping — `sudo journalctl -u ship-observer -n 50` will say why.

**Layer 3 — the web interface answers:**

```bash
curl -s http://localhost:8080/healthz
```

Expected: `{"ok": true, "connected": true, "last_message_age_seconds": ...}`.
If `connected` is `false`, the AIS websocket is not up — check the API key.

**Layer 4 — real vessels appear:**

Open `http://ship-observer.local:8080/` in a browser. You should see the panel
mirror, the vessel table, and the event log. The parsed bounding box is shown in
the Configuration panel — **check the corners are what you intended**, since a
transposed paste is otherwise silent.

Puget Sound traffic is not continuous. If the box is empty, that may simply mean
nothing is there right now; the event log will confirm the stream is connected
and receiving.

**Layer 5 — the panel matches the mirror.** Look at the actual LED panel. It
should show what the browser mirror shows.

## 10. Tuning after a week

Let it run for a week, then open the Traffic summary panel on the debug page (or
`curl 'http://localhost:8080/api/traffic-summary?window=7d'`).

Use it to answer:

- **Is recreational traffic noisy?** If sailing and fishing dominate the category
  counts, set `MIN_LENGTH_METERS=50` — that cuts nearly all of it, since ferries,
  cargo, tankers, and warships are all well over 50 m.
- **Is the box right?** Both nearby ferry routes fall outside the current box:
  Edmonds–Kingston runs near 47.80 and Mukilteo–Clinton near 47.95. To catch
  ferries, extend `lat_min` down to about 47.79 or `lat_max` up to about 47.99.
- **Is static data resolving?** A high `unresolved_static_visits` count means
  vessels are passing through faster than their 6-minute `ShipStaticData`
  interval, so they only ever show as generic hulls.

Edit `/opt/ship-observer/.env`, then `sudo systemctl restart ship-observer`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Panel flickers or shimmers | `snd_bcm2835` is still loaded (§4) or `isolcpus` is not applied (§5). Confirm with `lsmod \| grep snd` and `cat /sys/devices/system/cpu/isolated`. Both need a reboot. |
| Faint flicker after the above | Do the GPIO4–GPIO18 solder jumper and switch to `adafruit-hat-pwm`. |
| Panel is completely blank | Check the ribbon is on the panel's **input** side; check the 5 V supply is connected to the panel, not just the Pi; confirm `MATRIX_HARDWARE_MAPPING` matches your board. Test with the `demo` binary first. |
| Colors are wrong (red shows as blue) | Wrong `MATRIX_HARDWARE_MAPPING`. Try `adafruit-hat`, `adafruit-hat-pwm`, and `regular` with the `demo` binary until colors are right, then set that value in `.env`. |
| Top half or bottom half is dark | Wrong row-scan for the panel. Add `--led-multiplexing=1` (then 2, 3…) to the `demo` command to find the right value, then set `MATRIX_ROWS` correctly. |
| Service crash-loops immediately | `journalctl -u ship-observer -n 50`. A `Configuration error:` line names the offending variable. A malformed `BBOX` is the usual culprit — it must be four numbers, longitude first, with `min < max` on both axes. |
| `connected: false` in `/healthz` | The API key is wrong, or the subscription was rejected. The event log on the debug page shows the websocket errors. |
| Panel dims by half on its own | That is deliberate: no AIS message has arrived for `STALE_SECONDS` (default 120). It means the feed is down, not the display. |
| `import rgbmatrix` fails | The build was run against the wrong interpreter. Re-run §7 with `PYTHON=` pointing at `~/.pyenv/versions/ship_observer/bin/python`. |
| `ModuleNotFoundError: PIL` | `pip install -e '/opt/ship-observer[pi]'` — the `pi` extra carries Pillow. |
| Web UI loads but the panel is black | Look at the vessel table. Rows struck through are being filtered by `MIN_LENGTH_METERS` or `EXCLUDE_CATEGORIES`, and the Status column says which. |

## Useful commands

```bash
sudo journalctl -u ship-observer -f          # live logs
sudo systemctl restart ship-observer         # after editing .env
curl -s localhost:8080/api/state | python3 -m json.tool
curl -s 'localhost:8080/api/ships?limit=20' | python3 -m json.tool
sqlite3 /var/lib/ship-observer/ships.db 'SELECT name,category,length_m FROM ship_log ORDER BY entered_at DESC LIMIT 20;'
```

To capture a session for later replay, set `RECORD_RAW_PATH=/var/lib/ship-observer/session.jsonl`
in `.env` and restart. Replay it against a scratch database with:

```bash
/opt/ship-observer/venv/bin/python -m ship_observer.replay \
  --file /var/lib/ship-observer/session.jsonl --db /tmp/replay.db
```
````

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_docs.py -v`
Expected: PASS

- [ ] **Step 5: Run the entire suite one final time**

Run: `pytest -v`
Expected: PASS across every test file

- [ ] **Step 6: Write `README.md`**

```markdown
# Ship Observer Screen

Live AIS vessel display for a 64x64 HUB75 LED matrix on a Raspberry Pi.

Streams from [AISStream.io](https://aisstream.io), filters to a configurable
bounding box, shows the three most interesting vessels on the panel, and serves
a debug page backed by rolling SQLite logs.

- **Pi setup:** [`docs/raspberry-pi-setup.md`](docs/raspberry-pi-setup.md)
- **Design:** [`docs/superpowers/specs/2026-08-26-ship-observer-design.md`](docs/superpowers/specs/2026-08-26-ship-observer-design.md)

## Development (no Pi required)

```bash
pyenv virtualenv 3.11.13 ship_observer
pip install -e ".[dev]"
pytest
```

Everything except `ship_observer/drivers/rgbmatrix.py` runs on any host; the
null driver stands in for the panel, and the debug page's pixel mirror shows
exactly what the LEDs would show.

```bash
cp .env.example .env    # set AIS_STREAM_API_KEY and BBOX
DISPLAY_DRIVER=null DB_PATH=./ships.db python -m ship_observer
open http://localhost:8080/
```
```

- [ ] **Step 7: Commit**

```bash
git add docs/raspberry-pi-setup.md README.md tests/test_docs.py
git commit -m "docs: Raspberry Pi setup runbook and README"
```

---

## Appendix: Execution Order and Dependencies

```
1  models ──┬── 2  shiptypes ──┬── 3  config ──┬── 5  ais client
            │                  │               ├── 6  registry
            │                  └───────────────┼── 7  selection
            │                                  ├── 8  storage ── 9  queries
            └── 4  ais parsing ────────────────┘

10 canvas ──┬── 11 font ──┬── 13 scroll ──┐
            └── 12 icons ─┴───────────────┴── 14 layout

3 + 14 ── 15 drivers ──┐
6 + 7 + 9 + 14 ────────┴── 16 web api ── 17 debug page
                                       └── 18 service ──┬── 19 replay
                                                        └── 20 integration
18 ── 21 deploy ── 22 runbook
```

Tasks 1–9 and 10–14 are two independent chains and can proceed in parallel if
two workers are available. Everything from 15 onward is sequential.
