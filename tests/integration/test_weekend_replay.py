"""Replaying the first real weekend of traffic (2026-08-27 to 08-31).

tests/data/weekend-20260827.jsonl is rebuilt from that weekend's ships.db:

    python -m ship_observer.export_session --db ships.db \
        --out tests/data/weekend-20260827.jsonl

Replaying it proves the pipeline reproduces the recorded visits, and the
render walk proves every frame of four days of Puget Sound traffic stays
legible on the panel.
"""
import contextlib
import json
import sqlite3
from pathlib import Path

import pytest

from ship_observer.config import Settings
from ship_observer.registry import VesselRegistry
from ship_observer.render.canvas import Canvas
from ship_observer.render.icons import ICON_H, ICON_W
from ship_observer.render.layout import BLOCK_H, capacity, render_frame
from ship_observer.render.scroll import Scroller
from ship_observer.replay import read_session, replay
from ship_observer.selection import select_slots

DATA = Path(__file__).parent.parent / "data"
SESSION = DATA / "weekend-20260827.jsonl"
EXPECTED = DATA / "weekend-20260827.expected.json"

ENV = {
    "AIS_STREAM_API_KEY": "k",
    "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
    "DISPLAY_DRIVER": "null",
}

# Anything dimmer than this on the LED matrix reads as an unlit pixel - the
# weekend's UNKNOWN icons at (90, 90, 90) proved it.
MIN_VISIBLE = 140


async def test_weekend_replay_reproduces_the_recorded_visits(tmp_path):
    settings = Settings.from_env({**ENV, "DB_PATH": str(tmp_path / "replay.db")})

    result = await replay(SESSION, settings, speed=0.0)

    assert result == json.loads(EXPECTED.read_text())


async def test_weekend_replay_identifies_a_regular_on_a_visit_with_no_static_data(tmp_path):
    """VOYAGER OF THE SEAS sent static data on her first pass and none on her
    second. The vessel store is why the second is a passenger ship and not a
    question mark - and the visit log still shows that nothing arrived."""
    db = tmp_path / "replay.db"
    await replay(SESSION, Settings.from_env({**ENV, "DB_PATH": str(db)}),
                 speed=0.0)

    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        visits = conn.execute(
            "SELECT category, raw_static FROM ship_log "
            "WHERE mmsi = 311317000 ORDER BY entered_at").fetchall()
        remembered = conn.execute(
            "SELECT name, ship_type FROM vessel WHERE mmsi = 311317000"
        ).fetchone()

    assert [v["category"] for v in visits] == ["passenger", "passenger"]
    assert visits[0]["raw_static"] is not None   # heard over the air
    assert visits[1]["raw_static"] is None       # seeded from the store
    assert dict(remembered) == {"name": "VOYAGER OF THE SEAS", "ship_type": 60}


def _walk_frames(settings) -> list[bytes]:
    """Drive the render path with the weekend's messages, sampling frames."""
    registry = VesselRegistry(settings)
    scroller = Scroller()
    canvas = Canvas(settings.panel_width, settings.panel_height)
    slot_capacity = capacity(settings.panel_height)
    frames = []
    for index, message in enumerate(read_session(SESSION)):
        registry.prune(now=message.received_at)
        registry.apply(message)
        if index % 5:
            continue
        slots = select_slots(registry.live(), registry.departed(),
                             settings, slot_capacity)
        render_frame(canvas, slots, scroller, dt=0.0)
        for slot, _vessel in enumerate(slots.live):
            visible = sum(
                1
                for y in range(slot * BLOCK_H, slot * BLOCK_H + ICON_H)
                for x in range(ICON_W)
                if max(canvas.get_pixel(x, y)) >= MIN_VISIBLE
            )
            assert visible >= 5, (
                f"slot {slot} icon nearly invisible at message {index}")
        frames.append(canvas.to_bytes())
    return frames


def test_weekend_render_walk_keeps_every_icon_visible():
    settings = Settings.from_env(ENV)
    frames = _walk_frames(settings)
    assert len(frames) > 100
    assert any(frame != frames[0] for frame in frames)  # ships actually shown


def test_weekend_render_walk_is_deterministic():
    """Two replays of the same session must draw pixel-identical frames."""
    settings = Settings.from_env(ENV)
    assert _walk_frames(settings) == _walk_frames(settings)
