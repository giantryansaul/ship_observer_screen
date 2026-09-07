from __future__ import annotations

from typing import Hashable

from ..models import Vessel
from ..selection import Slots
from ..uscg_locations import resolve_destination
from .canvas import RGB, Canvas
from .font import Font, draw_text, text_width
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


def format_sog(vessel: Vessel) -> str:
    """The fastest speed seen this visit, or '' when none was reported.

    Trailing '.0' is dropped: '12kn' says as much as '12.0kn' in four fewer
    pixels, and slow movers still keep the tenth that distinguishes a tug
    working from a tug moored.
    """
    if vessel.max_sog is None:
        return ""
    return f"{vessel.max_sog:.1f}".rstrip('0').rstrip('.') + "kn"


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


def fit_parts(parts: list[str], box_width: int, tag: str = "",
              sep: str = " ", font: Font | None = None) -> str:
    """Join `parts`, dropping the rightmost until the line fits.

    Shared by the 2- and 1-ship detail rows, where a long category word or
    a `LAST SEEN` tag can push the line past the panel edge. Parts are
    ordered most- to least-important, so losing the tail is always better
    than clipping mid-word or squeezing the gaps to nothing. `tag` is drawn
    right-aligned on the same row, so its width plus one blank cell is
    reserved before anything is measured.
    """
    reserved = (text_width(tag, font) + (font or Font.default()).width
                if tag else 0)
    kept = [part for part in parts if part]
    while kept:
        text = sep.join(kept)
        if text_width(text, font) + reserved <= box_width:
            return text
        kept.pop()
    return ""


def text_x(text: str, box_width: int, offset: int,
           font: Font | None = None, center: bool = False) -> int:
    """Where a line of text starts: the scroller's offset while it
    overflows, otherwise flush left - or centred, for the stacked 1-ship
    layout whose whole composition is centred on its icon."""
    if center and text_width(text, font) <= box_width:
        return (box_width - text_width(text, font)) // 2
    return offset


def draw_line2(canvas: Canvas, vessel: Vessel, y: int, scroller: Scroller,
               dt: float, box_width: int = LINE2_BOX_W,
               center: bool = False) -> None:
    """`CALLSIGN > DESTINATION`, in every mode.

    Callsign and destination are drawn as one scrolling string so they
    travel together, then re-coloured by character position.
    """
    line2 = format_line2(vessel)
    if not line2:
        return
    offset = scroller.offset_for((vessel.mmsi, "line2"), line2, box_width, dt)
    x = text_x(line2, box_width, offset, center=center)
    split = len(vessel.call_sign or "")
    draw_text(canvas, line2[:split], x, y, CALLSIGN_COLOR,
              clip_x0=0, clip_x1=box_width - 1)
    draw_text(canvas, line2[split:], x + text_width(line2[:split]), y,
              DEST_COLOR, clip_x0=0, clip_x1=box_width - 1)


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

    draw_line2(canvas, vessel, y + LINE2_Y_OFFSET, scroller, dt)

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
