"""The 2-ship layout: two half-panel blocks, one vessel each.

Half the panel per vessel buys a 16px icon and the 6x10 name face, so a
name is readable from across the room where the 3-ship layout's 4x6 is
not. The cost is that only two vessels are on screen at a time - the
service's rotation clock pages the rest through (see selection.py).
"""
from __future__ import annotations

from ..models import Vessel
from ..selection import RotationView
from .canvas import Canvas
from .font import Font, draw_text, text_width
from .icons import icon_for
from .layout import (DIVIDER_LABEL, DIVIDER_TEXT_COLOR, LENGTH_COLOR,
                     NAME_COLOR, SEPARATOR_COLOR, STALE_DIM, draw_line2,
                     fit_parts, format_length, format_sog)
from .scroll import Scroller

# Two blocks fill the 64px panel exactly; the separator is the bottom row
# of the upper block rather than a row of its own, so both blocks get the
# same 31 usable rows.
BLOCKS = 2
BLOCK_H = 32
SEPARATOR_Y = BLOCK_H - 1

ICON_SIZE = 16
ICON_X = 0
NAME_X = ICON_SIZE + 1      # a 1px gutter keeps the name off the icon
NAME_BOX_W = 64 - NAME_X    # 47 px -> 7 characters of the 6x10 face
LINE_BOX_W = 64

# Block-relative rows. The name is centred in the 16px icon band; the two
# text rows sit under it with a blank row between each, leaving row 30
# blank above the separator.
NAME_Y = 3
LINE2_Y = 17
LINE3_Y = 24

# Two spaces between length and speed: at 4px per character one space
# reads as a typo, two as a column.
DETAIL_SEP = "  "


def _draw_details(canvas: Canvas, vessel: Vessel, y: int,
                  from_history: bool) -> None:
    """`<length>  <sog>kn`, with the `LAST SEEN` tag right-aligned on the
    same row for a departed vessel. All three never fit across 64px, so
    fit_parts drops the speed - the least useful number about a ship that
    has already left."""
    tag = DIVIDER_LABEL if from_history else ""
    details = fit_parts([format_length(vessel), format_sog(vessel)],
                        LINE_BOX_W, tag=tag, sep=DETAIL_SEP)
    if details:
        draw_text(canvas, details, 0, y, LENGTH_COLOR)
    if tag:
        draw_text(canvas, tag, canvas.width - text_width(tag), y,
                  DIVIDER_TEXT_COLOR)


def _draw_block(canvas: Canvas, vessel: Vessel, y: int, scroller: Scroller,
                dt: float, from_history: bool) -> None:
    canvas.blit(icon_for(vessel.category, ICON_SIZE), ICON_X, y)

    name = vessel.display_name
    offset = scroller.offset_for((vessel.mmsi, "name"), name, NAME_BOX_W, dt,
                                 font=Font.large())
    draw_text(canvas, name, NAME_X + offset, y + NAME_Y, NAME_COLOR,
              clip_x0=NAME_X, clip_x1=NAME_X + NAME_BOX_W - 1,
              font=Font.large())

    draw_line2(canvas, vessel, y + LINE2_Y, scroller, dt, LINE_BOX_W)
    _draw_details(canvas, vessel, y + LINE3_Y, from_history)


def render_two_ship(canvas: Canvas, view: RotationView, scroller: Scroller,
                    dt: float, stale: bool = False) -> None:
    """Draw one complete frame. The canvas is cleared first."""
    canvas.clear()

    vessels = view.vessels[:BLOCKS]
    for index, vessel in enumerate(vessels):
        y = index * BLOCK_H
        if y + BLOCK_H > canvas.height:
            break
        _draw_block(canvas, vessel, y, scroller, dt, view.from_history)

    # Only worth a rule when there is actually something on both sides of
    # it; under a lone vessel it would just underline an empty half.
    if len(vessels) > 1:
        canvas.hline(SEPARATOR_Y, 0, canvas.width - 1, SEPARATOR_COLOR)

    # Bound the scroller's memory: only fields actually on screen survive.
    scroller.retain({(v.mmsi, field) for v in vessels
                     for field in ("name", "line2")})

    if stale:
        canvas.dim(STALE_DIM)
