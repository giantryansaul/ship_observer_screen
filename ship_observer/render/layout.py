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
