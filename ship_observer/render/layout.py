from __future__ import annotations

from typing import Hashable

from ..models import Vessel
from ..selection import Slots
from ..uscg_locations import resolve_destination
from .canvas import RGB, Canvas
from .font import draw_text, text_width
from .icons import icon_for
from .scroll import Scroller

# Section 8.1 of the spec: 3 * BLOCK_H + DIVIDER_H == 64 exactly.
BLOCK_H = 19
DIVIDER_H = 7

ICON_X = 0
TEXT_X = 10
LENGTH_COL_W = 16           # 4 chars -> up to "999m", right-aligned at x=64
NAME_BOX_W = 64 - TEXT_X - LENGTH_COL_W    # 38 px -> 9 characters, scrolling
LINE2_BOX_W = 64            # 16 characters

NAME_Y_OFFSET = 1           # centres the 6 px text in the 8 px icon band
LINE2_Y_OFFSET = 9
SEPARATOR_Y_OFFSET = 16

NAME_COLOR: RGB = (255, 255, 255)
LENGTH_COLOR: RGB = (170, 170, 170)
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


def format_length(vessel: Vessel) -> str:
    """Rounded length in meters, or '' when unknown. Never scrolls - it sits
    right-aligned at the panel edge, outside the scrolling name box."""
    if vessel.length_m is None:
        return ""
    return f"{round(vessel.length_m)}m"


def format_line2(vessel: Vessel) -> str:
    """`CALLSIGN > DESTINATION`, degrading gracefully when either is missing.

    A raw US^XXXX destination code (USCG AIS Encoding Guide v.25) is
    unreadable on the panel; resolve it to a place name where we can.
    """
    call_sign = vessel.call_sign or ""
    dest_text = vessel.destination
    if dest_text:
        resolved = resolve_destination(dest_text)
        if resolved is not None:
            dest_text = resolved.panel_text
    destination = f"> {dest_text}" if dest_text else ""
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

    length_text = format_length(vessel)
    if length_text:
        length_x = canvas.width - text_width(length_text)
        draw_text(canvas, length_text, length_x, y + NAME_Y_OFFSET, LENGTH_COLOR)

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
