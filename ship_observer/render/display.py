"""Mode dispatch: one entry point for every display mode.

The panel, the web mirror and the /panel audit tool all draw through
here, so a mode can never be shown in one place and not another.
"""
from __future__ import annotations

from ..models import DisplayMode
from ..selection import RotationView, Slots
from .canvas import Canvas
from .layout import render_frame
from .one_ship import render_one_ship
from .scroll import Scroller
from .two_ship import render_two_ship

# How many vessels a rotation mode shows at once - the page size its
# rotation clock steps through. THREE_SHIP is absent on purpose: its slot
# count is derived from the panel height (layout.capacity), not fixed.
PAGE_SIZE: dict[DisplayMode, int] = {
    DisplayMode.TWO_SHIP: 2,
    DisplayMode.ONE_SHIP: 1,
}

_RENDERERS = {
    DisplayMode.TWO_SHIP: render_two_ship,
    DisplayMode.ONE_SHIP: render_one_ship,
}


def render_display(mode: DisplayMode, canvas: Canvas, slots: Slots,
                   scroller: Scroller, dt: float, stale: bool = False,
                   *, view: RotationView | None = None) -> None:
    """Draw one complete frame in `mode`. The canvas is cleared first.

    `view` is the page the caller's rotation clock is currently on. Still
    renders have no such clock, so leaving it out falls back to a single
    static page of `slots` - which is also what keeps the 3-ship signature
    working unchanged for every mode.
    """
    if mode is DisplayMode.THREE_SHIP:
        render_frame(canvas, slots, scroller, dt, stale=stale)
        return
    if view is None:
        view = RotationView.from_slots(slots, PAGE_SIZE[mode])
    _RENDERERS[mode](canvas, view, scroller, dt, stale=stale)
