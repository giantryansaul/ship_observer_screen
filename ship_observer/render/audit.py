"""Static content for the /panel audit tool.

Renders through the real pipeline - Canvas, draw_text, icon_for,
render_frame - so what the tool shows is pixel-identical to what the
hardware would draw, never a reimplementation kept only for display.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from ..models import ShipCategory, Vessel
from ..selection import Slots
from .canvas import RGB, Canvas
from .font import Font, draw_text, max_chars
from .icons import icon_for

TEXT_COLOR: RGB = (255, 255, 255)
LINE_GAP = 1

PANGRAM = "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG"
# Every character an AIS text field can carry (space through underscore).
CHARSET = " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_"

ICON_AUDIT_COLS = 3
ICON_AUDIT_CELL_W = 21
ICON_AUDIT_CELL_H = 16
ICON_AUDIT_MARGIN = 2


@dataclass(frozen=True)
class IconGrid:
    """How many icons of one size fit on a 64x64 frame, and where."""

    cols: int
    rows: int
    cell_w: int
    cell_h: int
    margin: int

    @property
    def per_page(self) -> int:
        return self.cols * self.rows


# The 8px grid is the original layout, kept exactly as it was; the bigger
# icons need whole frames of their own, so they page instead. Two 32px cells
# fill the panel edge to edge - that art carries its own blank border.
ICON_AUDIT_GRIDS: dict[int, IconGrid] = {
    8: IconGrid(cols=ICON_AUDIT_COLS, rows=4, cell_w=ICON_AUDIT_CELL_W,
                cell_h=ICON_AUDIT_CELL_H, margin=ICON_AUDIT_MARGIN),
    16: IconGrid(cols=3, rows=3, cell_w=21, cell_h=21, margin=2),
    32: IconGrid(cols=2, rows=2, cell_w=32, cell_h=32, margin=0),
}


def _wrap(text: str, width: int) -> list[str]:
    return [text[i:i + width] for i in range(0, len(text), width)] or [""]


def render_font_audit(canvas: Canvas, font: Font | None = None) -> None:
    """The pangram (every letter, in a readable word) followed by the full
    AIS character set (every glyph, in order) - a systematic legibility
    check for the whole font, not just the letters that happen to appear
    in ship names today. Lines that no longer fit are simply dropped, so a
    taller font shows fewer of them rather than spilling off the panel."""
    canvas.clear()
    font = font or Font.default()
    width = max_chars(canvas.width, font)
    lines = [*_wrap(PANGRAM, width), "", *_wrap(CHARSET, width)]
    y = 0
    for line in lines:
        if y + font.height > canvas.height:
            break
        if line:
            draw_text(canvas, line, 0, y, TEXT_COLOR, font=font)
        y += font.height + LINE_GAP


def _icon_grid(size: int) -> IconGrid:
    try:
        return ICON_AUDIT_GRIDS[size]
    except KeyError:
        raise ValueError(
            f"icon size must be one of {tuple(ICON_AUDIT_GRIDS)}; "
            f"got {size!r}") from None


def icon_audit_pages(size: int = 8) -> int:
    """How many 64x64 frames it takes to show every category at this size."""
    return math.ceil(len(ShipCategory) / _icon_grid(size).per_page)


def icon_audit_categories(size: int = 8, page: int = 0) -> list[ShipCategory]:
    """The categories on one page, in the order the grid draws them."""
    per_page = _icon_grid(size).per_page
    return list(ShipCategory)[page * per_page:(page + 1) * per_page]


def render_icon_audit(canvas: Canvas, size: int = 8, page: int = 0) -> None:
    """One page of category icons, laid out in ShipCategory's own definition
    order - the HTML legend mirrors this exact grid so position alone
    identifies each icon. The 8px set fits on a single page; the bigger
    icons need several."""
    grid = _icon_grid(size)
    canvas.clear()
    for index, category in enumerate(icon_audit_categories(size, page)):
        row, col = divmod(index, grid.cols)
        x = col * grid.cell_w + grid.margin
        y = row * grid.cell_h + grid.margin
        canvas.blit(icon_for(category, size), x, y)


def dummy_slots() -> Slots:
    """One live vessel and two departed ones, covering: a name long enough
    to need scrolling, a resolvable US/GUID destination, an unresolvable
    destination, a vessel with no known length, and - the reason this tool
    exists - a fishing vessel, shown in the same context real traffic
    would use it."""
    now = datetime.now(timezone.utc)
    live = Vessel(
        mmsi=900000001, entered_at=now, last_seen=now,
        name="EVER GIVEN", call_sign="H3RC", destination="SEATTLE",
        category=ShipCategory.CARGO, priority=30, length_m=400.0,
        static_resolved=True,
    )
    tug = Vessel(
        mmsi=900000002, entered_at=now, last_seen=now, departed_at=now,
        depart_reason="timeout",
        name="HERCULES", call_sign="WDE2097", destination="US^0TEM>016S",
        category=ShipCategory.TUG, priority=20, length_m=31.0,
        static_resolved=True,
    )
    fishing = Vessel(
        mmsi=900000003, entered_at=now, last_seen=now, departed_at=now,
        depart_reason="left_bbox",
        name="NORTHERN DAWN", call_sign="WDX4471", destination=None,
        category=ShipCategory.FISHING, priority=10, length_m=None,
        static_resolved=True,
    )
    return Slots(live=[live], history=[tug, fishing], show_divider=True)
