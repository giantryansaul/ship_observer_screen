"""The 1-ship layout: one vessel, flight-wall style.

The whole panel goes to a single ship - a 32px icon over its name in the
6x10 face, then the details in the small face, then a dot per page of the
rotation so the panel says how much traffic it is cycling through. Every
band is centred on the icon: this is meant to be read from across a room,
not scanned like a table.
"""
from __future__ import annotations

from ..models import Vessel
from ..selection import RotationView
from .canvas import RGB, Canvas
from .font import Font, draw_text, text_width
from .icons import icon_for
from .layout import (DIVIDER_LABEL, DIVIDER_TEXT_COLOR, LENGTH_COLOR,
                     NAME_COLOR, SEPARATOR_COLOR, STALE_DIM, draw_line2,
                     fit_parts, format_length, format_sog, text_x)
from .scroll import Scroller

ICON_SIZE = 32
ICON_Y = 0

NAME_BOX_W = 64             # the name gets the full panel width
NAME_Y = 32                 # directly under the icon band
LINE2_Y = 44
DETAILS_Y = 52

# One 2x2 dot per page along the bottom edge. Past ten pages the row would
# be wider than it is legible, so it caps and the current page is mapped
# proportionally onto the dots that are shown.
DOT_SIZE = 2
DOT_GAP = 1
DOT_CAP = 10
DOTS_Y = 62
DOT_ON: RGB = NAME_COLOR
DOT_OFF: RGB = SEPARATOR_COLOR


def _draw_details(canvas: Canvas, vessel: Vessel, from_history: bool) -> None:
    """Length, speed and the category word - the icon says the category in
    a glance, this says it in a word. A departed vessel trades the tail of
    that line for the `LAST SEEN` tag."""
    tag = DIVIDER_LABEL if from_history else ""
    details = fit_parts([format_length(vessel), format_sog(vessel),
                         vessel.category.value.upper()], NAME_BOX_W, tag=tag)
    if details:
        # Never centred against a tag: the two would drift into each other.
        x = text_x(details, NAME_BOX_W, 0, center=not tag)
        draw_text(canvas, details, x, DETAILS_Y, LENGTH_COLOR)
    if tag:
        draw_text(canvas, tag, canvas.width - text_width(tag), DETAILS_Y,
                  DIVIDER_TEXT_COLOR)


def _draw_page_dots(canvas: Canvas, pages: int, page: int) -> None:
    shown = min(pages, DOT_CAP)
    if shown <= 0:
        return
    width = shown * (DOT_SIZE + DOT_GAP) - DOT_GAP
    x0 = (canvas.width - width) // 2
    # With more pages than dots each dot stands for several pages, so the
    # lit one tracks how far through the rotation we are rather than which
    # page exactly - which is all a ten-dot row can honestly say.
    current = page if pages <= DOT_CAP else page * shown // pages
    for index in range(shown):
        color = DOT_ON if index == current else DOT_OFF
        x = x0 + index * (DOT_SIZE + DOT_GAP)
        for row in range(DOT_SIZE):
            canvas.hline(DOTS_Y + row, x, x + DOT_SIZE - 1, color)


def render_one_ship(canvas: Canvas, view: RotationView, scroller: Scroller,
                    dt: float, stale: bool = False) -> None:
    """Draw one complete frame. The canvas is cleared first."""
    canvas.clear()

    vessels = view.vessels[:1]
    for vessel in vessels:
        canvas.blit(icon_for(vessel.category, ICON_SIZE),
                    (canvas.width - ICON_SIZE) // 2, ICON_Y)

        name = vessel.display_name
        offset = scroller.offset_for((vessel.mmsi, "name"), name, NAME_BOX_W,
                                     dt, font=Font.large())
        draw_text(canvas, name,
                  text_x(name, NAME_BOX_W, offset, font=Font.large(),
                         center=True),
                  NAME_Y, NAME_COLOR, clip_x0=0, clip_x1=NAME_BOX_W - 1,
                  font=Font.large())

        draw_line2(canvas, vessel, LINE2_Y, scroller, dt, NAME_BOX_W,
                   center=True)
        _draw_details(canvas, vessel, view.from_history)

    _draw_page_dots(canvas, view.pages, view.page)

    # Bound the scroller's memory: only fields actually on screen survive.
    scroller.retain({(v.mmsi, field) for v in vessels
                     for field in ("name", "line2")})

    if stale:
        canvas.dim(STALE_DIM)
