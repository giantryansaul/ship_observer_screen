# Ship Observer Screen — Design

**Date:** 2026-08-26
**Status:** Approved for planning

A Raspberry Pi microservice that streams live AIS vessel data from AISStream.io,
filters it to a configurable bounding box, and displays up to three ships on a
64x64 HUB75 RGB LED matrix. It also serves an HTML debug page and keeps rolling
SQLite logs for tuning and troubleshooting.

---

## 1. Goals and Non-Goals

### Goals

- Show ships visible from the deck on an LED panel, at a glance.
- Prioritize large, interesting vessels (ferries, cruise, cargo, tankers, naval)
  over recreational traffic, without discarding the latter from the record.
- Make the display tunable without physical access to the panel.
- Retain enough history to diagnose problems and to answer "what actually goes
  past here?" empirically.

### Non-Goals

- Historical backfill of the past week. Verified unavailable: AISStream.io is
  live-only and states that "events are not durably replayed"; NOAA/MarineCadastre
  publishes 3-4+ months behind. Only paid vendors offer last-week history. The
  project instead records from first boot and provides a replay harness.
- Chart plotting, vessel tracks, or collision logic.
- Multi-panel or multi-location support.

---

## 2. Architecture

A single `asyncio` process under one systemd unit. Five concurrent tasks share an
in-memory `VesselRegistry`; SQLite is the durable log, never the hot path.

The two pruners are distinct and easy to confuse: the **registry pruner** expires
vessels from memory after `SHIP_TIMEOUT_SECONDS` (15 min), while the **retention
pruner** deletes old rows from SQLite on an hourly cycle (Section 9).

```
                  wss://stream.aisstream.io/v0/stream
                                 |
                        [ ais_client task ]
                                 |  parsed envelopes
                                 v
                        [ VesselRegistry ]  <-- registry pruner (15 min)
                          |            |
             live+departed|            | visit records / events
                          v            v
                   [ render task ]  [ storage (SQLite, WAL) ]
                     |         |            ^  <-- retention pruner (hourly)
                     |         |            ^
        latest-frame |         | frames     | queries
             slot    v         v            |
         [ matrix thread ]  [ aiohttp web server ] --> browser
                  |
          64x64 HUB75 panel
```

### Why a single process

Considered and rejected: (B) split collector and display processes over local
IPC — two units, an IPC contract, and a second failure mode, for resilience that
`Restart=always` already provides; (C) collector writes SQLite and renderer polls
it — turns a 15 fps render loop into a 15 Hz read against an SD card and adds
latency to the thing that should feel live.

### Threading

`rgb-led-matrix`'s `SwapOnVSync()` blocks until the next vsync, which would stall
the event loop. The `rgbmatrix` driver therefore runs a dedicated thread holding a
single-slot "latest frame" buffer. The async render loop drops the newest frame
into the slot and never blocks; the thread picks up whatever is current and drops
stale frames. Everything else is single-threaded async.

---

## 3. Module Layout

```
ship_observer/
  __init__.py
  __main__.py          wiring, signal handling, asyncio.run
  config.py            env parsing + validation, fails fast at startup
  models.py            Vessel, BoundingBox, ShipCategory, Priority
  ais_client.py        websocket client: subscribe, reconnect w/ backoff, parse
  registry.py          MMSI merge, prune, departed history, slot selection
  shiptypes.py         AIS type code -> category -> icon + priority tier
  storage.py           SQLite schema, writes, retention, summary queries
  replay.py            replay recorded JSONL sessions through the pipeline
  render/
    canvas.py          64x64 RGB framebuffer primitives
    font.py            4x6 bitmap font
    icons.py           8x8 category icon bitmaps
    scroll.py          per-field horizontal scroll state machine
    layout.py          slots + LAST SEEN divider -> canvas
  drivers/
    base.py            DisplayDriver protocol
    rgbmatrix.py       HUB75 via rpi-rgb-led-matrix, own thread
    null.py            no-op driver for dev/CI on non-Pi hosts
  web/
    server.py          aiohttp routes + websocket
    static/            index.html, app.js, style.css
deploy/
  ship-observer.service
  install.sh
tests/
```

Every module except `drivers/rgbmatrix.py` must import and run on a non-Pi host.
Development happens on WSL2; the null driver is what makes the pipeline testable.

---

## 4. Configuration

All configuration is environment variables, loaded from `.env` via `python-dotenv`.
`config.py` validates everything at startup and exits with a clear message on
error rather than crash-looping.

| Variable | Default | Notes |
|---|---|---|
| `AIS_STREAM_API_KEY` | *(required)* | From `.env`; never logged. |
| `BBOX` | *(required)* | `lon_min,lat_min,lon_max,lat_max`. Current value: `-122.527428,47.859476,-122.323322,47.910359` |
| `SHIP_LOG_DAYS` | `7` | Retention for `ship_log`. |
| `EVENT_LOG_HOURS` | `48` | Retention for `event_log`. |
| `DISPLAY_HISTORY` | `false` | Enables the LAST SEEN section. |
| `SHIP_TIMEOUT_SECONDS` | `900` | 15 min silence = departed. |
| `MAX_SHIPS` | `3` | Display slots. Clamped to the panel's block capacity (3 at 64x64); see Section 8.1. |
| `MIN_LENGTH_METERS` | `0` | Hard gate, `0` = off. |
| `EXCLUDE_CATEGORIES` | *(empty)* | Comma list, e.g. `fishing,sailing`. |
| `PRIORITY_SELECTION` | `true` | `false` reverts to pure recency selection. |
| `STALE_SECONDS` | `120` | No AIS message for this long dims the panel. |
| `RENDER_FPS` | `15` | Panel render loop rate. |
| `WEB_FPS` | `10` | Throttled frame rate pushed to browsers. |
| `DISPLAY_DRIVER` | `auto` | `auto` \| `rgbmatrix` \| `null` |
| `MATRIX_ROWS` | `64` | |
| `MATRIX_COLS` | `64` | |
| `MATRIX_CHAIN` | `1` | |
| `MATRIX_PARALLEL` | `1` | |
| `MATRIX_BRIGHTNESS` | `60` | 0-100. |
| `MATRIX_GPIO_SLOWDOWN` | `4` | 4 suits Pi 4; 2 for Pi 3. |
| `MATRIX_HARDWARE_MAPPING` | `adafruit-hat-pwm` | |
| `HTTP_HOST` | `0.0.0.0` | |
| `HTTP_PORT` | `8080` | |
| `DB_PATH` | `/var/lib/ship-observer/ships.db` | |
| `RECORD_RAW_PATH` | *(empty)* | When set, append raw envelopes as JSONL for replay. |
| `LOG_LEVEL` | `INFO` | |

### Bounding box handling

`BBOX` is longitude-first (bboxfinder.com order) so values paste in directly.
AISStream expects latitude-first corner pairs, so `config.py` converts:

```
BBOX=-122.527428,47.859476,-122.323322,47.910359
  -> BoundingBoxes: [[[47.859476, -122.527428], [47.910359, -122.323322]]]
```

Validation: four floats; latitudes in [-90, 90] and longitudes in [-180, 180];
`min < max` on both axes. The parsed corners are rendered on the debug page so a
transposed paste is immediately visible.

**Note on the current box:** it spans Point No Point to the Snohomish shore near
Picnic Point — the main deep-draft lane for Seattle and Everett traffic. Both
nearby ferry routes fall *outside* it (Edmonds-Kingston at ~47.80, Mukilteo-Clinton
at ~47.95), so ferry sightings will be rare until the box is widened. This is a
deliberate starting point, not a defect.

---

## 5. AIS Ingest

**Endpoint:** `wss://stream.aisstream.io/v0/stream`. The subscription message must
be sent within 3 seconds of connecting:

```json
{
  "APIKey": "<key>",
  "BoundingBoxes": [[[47.859476, -122.527428], [47.910359, -122.323322]]],
  "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
}
```

AISStream accepts only `APIKey`, `BoundingBoxes`, `FiltersShipMMSI`, and
`FilterMessageTypes`. **There is no server-side ship-type filter**, so all
type-based filtering is local (Section 7).

### The two message types carry different halves of what we need

| | `PositionReport` | `ShipStaticData` |
|---|---|---|
| Cadence | every 2-10 s | every ~6 min |
| Provides | `Latitude`, `Longitude`, `Sog`, `Cog`, `TrueHeading`, `NavigationalStatus` | `Name`, `CallSign`, `Destination`, `Type`, `ImoNumber`, `Dimension`, `Eta`, `MaximumStaticDraught` |

Neither alone is sufficient: position reports have no name, callsign, or
destination. Both envelopes carry `MetaData` with `MMSI`, `ShipName`, `latitude`,
`longitude`, and `time_utc`, so a newly-arrived vessel gets a provisional name
immediately instead of being anonymous for six minutes.

`MetaData.time_utc` is Go-formatted with nanoseconds and a trailing zone label
(`2026-08-26 17:04:11.123456789 +0000 UTC`) and needs a dedicated parser; ingest
timestamps fall back to arrival time if parsing fails.

`Dimension` is `{A, B, C, D}` = distances to bow, stern, port, starboard.
Length = `A + B`, beam = `C + D`.

### Connection handling

Exponential backoff with jitter, 1 s to 60 s ceiling, reset on a successful
subscription. Every connect, disconnect, and backoff writes an `event_log` row.
While disconnected the display keeps rendering the last known state; once no
message has arrived for `STALE_SECONDS`, the whole frame dims to 50% so a dead
feed is visible from the deck. A `websockets` ping/pong keepalive detects silent
half-open connections.

---

## 6. Vessel Registry

Keyed by MMSI. Each `Vessel` merges the latest position and the latest static
data, and records `entered_at` (first message of this visit), `last_seen`, and a
position counter.

**Entering** the box is the first message from an MMSI, because AISStream only
sends what is inside the subscribed box. **Departing** is detected two ways:

1. Silence for `SHIP_TIMEOUT_SECONDS` (900 s), or
2. A position report that falls outside the bounding box on a local check — which
   catches a vessel steaming out immediately rather than 15 minutes later.

Departed vessels move to a bounded `departed` deque (most recent first, capped at
`MAX_SHIPS`), which is what feeds the LAST SEEN section. A vessel that re-enters
after departing starts a **new visit** with a new `entered_at` and a new
`ship_log` row.

---

## 7. Prioritization and Noise Control

AISStream cannot filter by type, and recreational traffic in Puget Sound is heavy
in summer. Three mechanisms, layered so that nothing is lost from the record:

**The log always records every vessel, regardless of display filters.** Tuning
decisions are made from complete data.

### 7.1 Selection by priority, ordering by recency

Priority decides *which* `MAX_SHIPS` vessels get slots. The original rule — newest
entrant on top, others pushed down — still orders the vessels that were selected.
With `MAX_SHIPS` or fewer vessels present, behavior is identical to pure recency.

| Tier | Priority | Categories (AIS type codes) |
|---|---|---|
| 1 | 40 | Military (35), Passenger/Ferry (60-69), SAR (51), Law enforcement (55) |
| 2 | 30 | Cargo (70-79), Tanker (80-89) |
| 3 | 20 | Tug & Towing (31, 32, 52), Pilot (50), other commercial (33, 34, 53, 54, 58) |
| 4 | 10 | Fishing (30), Sailing (36), Pleasure craft (37) |
| — | 20 | **Unknown** — no `ShipStaticData` yet (provisional Tier 3) |

Set `PRIORITY_SELECTION=false` to revert to pure recency selection.

### 7.2 Hard gates, defaulted off

- `MIN_LENGTH_METERS` — hull length from `Dimension`. Ferries run 100-140 m, cargo
  200-300 m, warships 100-180 m; sailboats are 8-15 m and most fishing boats under
  25 m, so `50` cuts recreational traffic in one knob. Ships at `0` so the first
  week shows the unfiltered picture.
- `EXCLUDE_CATEGORIES` — comma-separated category names, empty by default.

**Unknown vessels are never hard-gated.** Since `ShipStaticData` takes up to six
minutes, gates apply only once static data resolves; until then a vessel shows
with a generic icon at provisional Tier 3. Otherwise a cargo ship would be
invisible for its first six minutes in the box.

### 7.3 The tuning instrument

`GET /api/traffic-summary?window=7d`, plus a panel on the debug page:

- Visit counts by category
- Hull-length histogram (10 m buckets)
- Visits per hour of day
- Median and p90 dwell time in the box, by category
- Count of visits that never resolved static data

This is what answers "is recreational traffic actually noisy here, and what should
`MIN_LENGTH_METERS` be?" after a few days of real data.

---

## 8. Display

64x64, one HUB75 panel. Font is 4x6 (3x5 glyph + 1 px spacing), giving 16
characters across the full width.

### 8.1 Geometry

A ship block is **19 px**; the LAST SEEN divider is **7 px**. Three blocks plus a
divider is exactly 64 px, so every history configuration fits without scaling:

```
 3 live, history off        2 live + 1 departed         0 live + 3 departed
 +----------------+ y=0     +----------------+ y=0      +----------------+ y=0
 | block 0    19  |         | block 0    19  |          |--- LAST SEEN 7 |
 | block 1    19  |         | block 1    19  |          | block 0    19  |
 | block 2    19  |         |--- LAST SEEN 7 |          | block 1    19  |
 | (7 px spare)   |         | block 2    19  |          | block 2    19  |
 +----------------+ y=63    +----------------+ y=63     +----------------+ y=63
```

Blocks lay out top-down from y=0; any remainder is left blank at the bottom.

**Capacity is derived, not hard-coded.** `layout.py` computes
`capacity = panel_height // BLOCK_H` (3 at 64 px) and the effective slot count is
`min(MAX_SHIPS, capacity)`. Setting `MAX_SHIPS` above capacity is clamped with a
WARN event rather than an error, so a taller panel is a config change and not a
rewrite.

Within a 19 px block, at block origin `y`:

| Rows | Content |
|---|---|
| `y+0 .. y+7` | 8x8 category icon at `x=0..7`; ship **name** at `x=10`, vertically centered (rows `y+1..y+6`), 54 px wide = 13 chars |
| `y+9 .. y+14` | `CALLSIGN > DESTINATION` at `x=0`, full 64 px = 16 chars |
| `y+16` | 1 px dim separator rule |
| `y+17 .. y+18` | blank |

The divider is a 1 px rule at its top row with `LAST SEEN` (9 chars = 36 px)
centered at `x=14` below it.

### 8.2 Scrolling

Any field wider than its box scrolls horizontally; fields that fit stay perfectly
still. Per-field state machine in `scroll.py`: hold 1.5 s at the left, scroll at
~12 px/s to the right edge, hold 1.5 s, snap back. State is keyed by
`(mmsi, field)` and resets whenever the text changes, so a destination update
doesn't leave the scroller mid-travel.

This is why the render loop runs continuously at `RENDER_FPS` rather than
redrawing only on AIS updates.

### 8.3 Icons and color

8x8 bitmaps per category, authored as string art in `icons.py` and parsed to
pixels. Categories follow the requested mapping, with 36 (sailing) and 37
(pleasure craft) drawn distinctly since they are distinguishable, and a generic
hull for anything uncategorized or not yet resolved.

| Category | Codes | Icon |
|---|---|---|
| Passenger / Ferry | 60-69 | ferry silhouette |
| Cargo | 70-79 | box boat |
| Tanker | 80-89 | long low hull |
| Tug & Towing | 31, 32, 52 | small tug |
| Fishing | 30 | trawler |
| Sailing | 36 | sailboat |
| Pleasure | 37 | powerboat |
| SAR / Law | 51, 55 | patrol with light bar |
| Military | 35 | warship |
| Other / Unknown | rest | generic hull |

Color: icons tinted per category, name in white, callsign in dim cyan,
destination in amber. Stale feed dims the entire frame to 50%.

### 8.4 Empty state

Zero live vessels with `DISPLAY_HISTORY=false` renders a black panel, as specified.

---

## 9. Storage

SQLite in WAL mode with `synchronous=NORMAL` — write volume is low and this is an
SD card. All writes go through `storage.py` on the event loop via short
transactions; no ORM.

### `ship_log` — one row per visit

A new row each time an MMSI enters the box, updated in place while the visit
lasts. Per-visit rather than per-ship granularity is what makes the log useful for
answering "what came through, when, and for how long".

```sql
CREATE TABLE ship_log (
  id              INTEGER PRIMARY KEY,
  mmsi            INTEGER NOT NULL,
  entered_at      TEXT    NOT NULL,   -- ISO8601 UTC
  last_seen       TEXT    NOT NULL,
  departed_at     TEXT,               -- NULL while still in the box
  depart_reason   TEXT,               -- 'timeout' | 'left_bbox' | 'shutdown'
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
  first_lat       REAL, first_lon REAL,
  last_lat        REAL, last_lon REAL,
  max_sog         REAL,
  last_cog        REAL,
  last_heading    INTEGER,
  nav_status      INTEGER,
  position_count  INTEGER NOT NULL DEFAULT 0,
  static_resolved INTEGER NOT NULL DEFAULT 0,
  displayed       INTEGER NOT NULL DEFAULT 0,  -- ever held a display slot
  raw_static      TEXT,   -- JSON, latest ShipStaticData
  raw_position    TEXT    -- JSON, latest PositionReport
);
CREATE INDEX idx_ship_log_entered ON ship_log(entered_at);
CREATE INDEX idx_ship_log_mmsi    ON ship_log(mmsi, entered_at);
CREATE INDEX idx_ship_log_cat     ON ship_log(category, entered_at);
```

**Known edge case:** an unresolved vessel is always eligible (Section 7.2) and can
briefly win a slot before its static data arrives; if that data later reveals a
vessel that should be filtered (e.g. `MIN_LENGTH_METERS`), it is correctly removed
from the panel from that point on, but `displayed` stays `True` — it records
"was ever rendered," not "matches the vessel's final resolved classification."
This is a rare edge case (it needs an otherwise-quiet box for a small vessel to
win a slot at all) and is consistent with the field's documented meaning below,
but it can slightly skew the traffic summary toward over-counting displayed
small craft. Revisit only if real tuning data shows this matters in practice.

`displayed` and `static_resolved` exist specifically to answer tuning questions:
what got shown, and how often static data never arrived.

### `event_log`

```sql
CREATE TABLE event_log (
  id       INTEGER PRIMARY KEY,
  ts       TEXT NOT NULL,     -- ISO8601 UTC
  level    TEXT NOT NULL,     -- DEBUG | INFO | WARN | ERROR
  category TEXT NOT NULL,     -- ws | registry | display | web | storage | config
  message  TEXT NOT NULL,
  detail   TEXT               -- JSON
);
CREATE INDEX idx_event_log_ts ON event_log(ts);
```

### Retention

An hourly task deletes `ship_log` rows older than `SHIP_LOG_DAYS` and `event_log`
rows older than `EVENT_LOG_HOURS`, then runs `PRAGMA incremental_vacuum`. The
pruner logs how many rows it removed.

---

## 10. Web Debug Interface

`aiohttp`, bound to `HTTP_HOST:HTTP_PORT`. Read-only; no authentication, intended
for a trusted LAN.

| Route | Purpose |
|---|---|
| `GET /` | Debug page |
| `GET /api/state` | Effective config (API key redacted), connection status, live vessels, departed deque, current slot assignment |
| `GET /api/ships` | `ship_log` query: `since`, `until`, `category`, `limit` |
| `GET /api/events` | `event_log` query: `since`, `level`, `category`, `limit` |
| `GET /api/traffic-summary` | Section 7.3 aggregates over `window` |
| `GET /healthz` | Liveness + last-message age |
| `WS /ws/frames` | Live push |

The websocket pushes two message kinds: `{"type":"frame","rgb":"<base64>"}` at
`WEB_FPS` (64x64x3 = 12 KB raw, ~120 KB/s at 10 fps — fine on a LAN), and
`{"type":"state", ...}` whenever registry state changes.

The page is a single dark-themed view with no build step and no external assets:

1. **Panel mirror** — the frame drawn to a `<canvas>` at 8x nearest-neighbour
   scale, pixel-accurate to the LED panel, so layout can be tuned without looking
   at the hardware.
2. **Live vessels** — every vessel in the box, including ones filtered out of the
   display, with category, priority, length, and slot assignment. Filtered rows
   are visibly marked so it is obvious *why* something is not on the panel.
3. **Traffic summary** — the Section 7.3 aggregates.
4. **Event log** — live tail with level filtering.
5. **Config** — effective values including the parsed bounding box corners.

---

## 11. Replay Harness

With `RECORD_RAW_PATH` set, every received envelope is appended verbatim as JSONL
with an arrival timestamp. `python -m ship_observer.replay --file <path> --speed N`
feeds a recorded session back through the identical registry, storage, and render
pipeline against the null driver.

This substitutes for the unavailable historical backfill: it gives backtesting from
first boot forward, and the same fixtures drive the integration tests.

---

## 12. Error Handling

| Failure | Behavior |
|---|---|
| Missing `AIS_STREAM_API_KEY`, malformed `BBOX` | Exit non-zero at startup with a specific message. No crash loop. |
| Websocket drop | Exponential backoff 1-60 s with jitter; event logged; display keeps last state. |
| No message for `STALE_SECONDS` | Frame dims to 50%. |
| Malformed AIS envelope | Dropped, counted, logged at DEBUG with the raw payload. One bad message never kills the consumer. |
| `rgbmatrix` import or init failure | Log ERROR, fall back to the null driver, keep serving the web UI so the failure is diagnosable remotely. |
| SQLite write error | Log ERROR; display and ingest continue. Logging is never allowed to take down the display. |
| Render exception | Caught per frame; logged once per unique traceback with a backoff to avoid log flooding. |

---

## 13. Testing

`pytest` with `pytest-asyncio`, entirely headless via the null driver. Nothing
outside `drivers/rgbmatrix.py` may require Pi hardware.

**Unit**
- `config.py` — valid and invalid `BBOX` including transposed axes and inverted
  min/max; defaults; required-variable errors.
- `shiptypes.py` — every code 0-99 maps to a category and tier; boundaries at
  29/30/31, 35/36/37, 49/50/51/52, 59/60, 69/70, 79/80, 89/90.
- `registry.py` — merge of position and static data; provisional name from
  `MetaData`; timeout prune; out-of-bbox immediate departure; re-entry creates a
  new visit; departed deque ordering and cap.
- Selection — priority selection vs. recency ordering; unknown vessels never hard
  gated; `MIN_LENGTH_METERS` and `EXCLUDE_CATEGORIES`; `PRIORITY_SELECTION=false`.
- `layout.py` — pixel assertions on block origins for all history configurations;
  divider placement; 3 blocks + divider is exactly 64 px.
- `scroll.py` — fields that fit never move; overflow scrolls and pauses; state
  resets on text change.
- `storage.py` — visit rows, in-place updates, retention boundaries, summary
  aggregates.
- Time parsing — Go-formatted `time_utc`, and fallback on garbage.

**Integration**
- A fake AISStream websocket server replays recorded envelopes; assert registry
  state, `ship_log` rows, and rendered frames end to end.
- Reconnect: server drops the connection mid-stream; assert backoff, resubscribe,
  and event rows.
- Web API: each route against a seeded database.

---

## 14. Deployment

**Runtime:** Python 3.11.13 via pyenv. `pyenv virtualenv 3.11.13 ship_observer`,
pinned by a `.python-version` file so the venv activates on entering the project.

**Dependencies:** `websockets`, `aiohttp`, `python-dotenv`; `pytest` and
`pytest-asyncio` for development. `rgbmatrix` is **not on PyPI** — `deploy/install.sh`
clones and builds Henner Zeller's `rpi-rgb-led-matrix` with Python bindings against
the venv interpreter.

**Pi setup**, handled or documented by `install.sh`:
- Blacklist `snd_bcm2835`; the matrix library needs the PWM hardware.
- Recommend `isolcpus=3` on the kernel command line for flicker-free output.
- Create `/var/lib/ship-observer` owned by the service user.
- The panel requires root for GPIO; the unit runs as root with
  `--led-drop-privs` handled by the library, or as a user in `gpio` with
  appropriate rules. `install.sh` documents the tradeoff.

**systemd** (`deploy/ship-observer.service`): `Restart=always`, `RestartSec=5`,
`After=network-online.target`, `EnvironmentFile=` pointing at the `.env`,
journal logging.

**`docs/raspberry-pi-setup.md`** is a first-class deliverable: a standalone,
follow-along runbook for provisioning the device from a blank SD card, written to
be usable months from now without reference to this spec. It must cover OS imaging
and headless SSH/Wi-Fi setup; the HUB75 wiring and power budget (a 64x64 panel can
draw ~4 A at full white, so panel power is separate from the Pi's supply); the
`snd_bcm2835` blacklist and `isolcpus=3` kernel argument with the reasoning for
each; installing pyenv and Python 3.11.13 on Pi OS including the build
dependencies; building `rpi-rgb-led-matrix` with Python bindings against the venv
interpreter; the root-versus-`gpio`-group tradeoff for GPIO access; creating
`/var/lib/ship-observer`; populating `.env`; installing and enabling the systemd
unit; and a verification section that proves each layer works in order (panel test
binary, then service status, then `/healthz`, then live ships on the panel). It
ends with a troubleshooting table for the failures that actually happen: flickering
output, a blank panel, colors swapped by a wrong `--led-hardware-mapping`,
websocket auth rejection, and a service that crash-loops on a bad `BBOX`.

**Secrets:** `.env` is gitignored and never logged; `/api/state` redacts the API
key.

---

## 15. Open Items Deferred by Design

- `MIN_LENGTH_METERS` and `EXCLUDE_CATEGORIES` ship at permissive defaults; the
  right values come from `/api/traffic-summary` after a week of real traffic.
- Widening the bounding box to include the Edmonds-Kingston or Mukilteo-Clinton
  ferry routes is a one-line config change once the current box has been observed.
