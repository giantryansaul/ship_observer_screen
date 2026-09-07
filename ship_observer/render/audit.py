"""Static content for the /panel audit tool.

Renders through the real pipeline - Canvas, draw_text, icon_for,
render_frame - so what the tool shows is pixel-identical to what the
hardware would draw, never a reimplementation kept only for display.
"""
from __future__ import annotations

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


def render_icon_audit(canvas: Canvas) -> None:
    """Every category's icon, laid out in ShipCategory's own definition
    order - the HTML legend mirrors this exact grid so position alone
    identifies each icon."""
    canvas.clear()
    for index, category in enumerate(ShipCategory):
        row, col = divmod(index, ICON_AUDIT_COLS)
        x = col * ICON_AUDIT_CELL_W + ICON_AUDIT_MARGIN
        y = row * ICON_AUDIT_CELL_H + ICON_AUDIT_MARGIN
        canvas.blit(icon_for(category), x, y)


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
