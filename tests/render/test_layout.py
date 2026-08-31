from datetime import datetime, timedelta, timezone

import pytest

from ship_observer.models import ShipCategory, Vessel
from ship_observer.render.canvas import Canvas
from ship_observer.render.font import FONT_H
from ship_observer.render.layout import (
    BLOCK_H,
    DIVIDER_H,
    LENGTH_COL_W,
    NAME_BOX_W,
    NAME_Y_OFFSET,
    TEXT_X,
    capacity,
    format_length,
    format_line2,
    render_frame,
)
from ship_observer.render.scroll import Scroller
from ship_observer.selection import Slots

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)


def vessel(mmsi=1, name="EVER GIVEN", call_sign="H3RC", destination="SEATTLE",
           category=ShipCategory.CARGO, length_m=None):
    return Vessel(mmsi=mmsi, entered_at=T0, last_seen=T0, name=name,
                  call_sign=call_sign, destination=destination,
                  category=category, length_m=length_m, static_resolved=True)


def lit_cols(canvas, y0, y1, x0, x1):
    return {x for y in range(y0, y1) for x in range(x0, x1)
            if canvas.get_pixel(x, y) != (0, 0, 0)}


def rows_with_content(canvas):
    return {y for y in range(canvas.height) for x in range(canvas.width)
            if canvas.get_pixel(x, y) != (0, 0, 0)}


def test_block_arithmetic_fills_the_panel_exactly():
    """3 * 19 + 7 == 64. This is the constraint the whole layout rests on."""
    assert BLOCK_H * 3 + DIVIDER_H == 64


@pytest.mark.parametrize("height,expected", [(64, 3), (32, 1), (128, 6), (18, 0)])
def test_capacity_is_derived_from_panel_height(height, expected):
    assert capacity(height) == expected


def test_empty_slots_render_a_black_panel():
    c = Canvas(64, 64)
    render_frame(c, Slots(), Scroller(), dt=0.1)
    assert c.to_bytes() == bytes(64 * 64 * 3)


def test_one_ship_occupies_only_the_first_block():
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel()]), Scroller(), dt=0.1)
    assert max(rows_with_content(c)) < BLOCK_H


def test_three_ships_stay_within_the_panel():
    c = Canvas(64, 64)
    slots = Slots(live=[vessel(1), vessel(2, name="WSF PUYALLUP"),
                        vessel(3, name="POLAR RESOLUTE")])
    render_frame(c, slots, Scroller(), dt=0.1)
    assert max(rows_with_content(c)) < BLOCK_H * 3


def test_blocks_are_stacked_at_multiples_of_block_height():
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel(1), vessel(2)]), Scroller(), dt=0.1)
    rows = rows_with_content(c)
    assert any(r < BLOCK_H for r in rows)
    assert any(BLOCK_H <= r < BLOCK_H * 2 for r in rows)
    assert not any(r >= BLOCK_H * 2 for r in rows)


def test_divider_appears_between_live_and_history():
    c = Canvas(64, 64)
    slots = Slots(live=[vessel(1), vessel(2)], history=[vessel(3)],
                  show_divider=True)
    render_frame(c, slots, Scroller(), dt=0.1)
    rows = rows_with_content(c)
    divider_top = BLOCK_H * 2
    assert divider_top in rows, "the divider rule must be drawn"
    assert any(r >= divider_top + DIVIDER_H for r in rows), "history block missing"
    assert max(rows) < 64


def test_two_live_plus_one_history_fills_exactly_64_rows():
    c = Canvas(64, 64)
    slots = Slots(live=[vessel(1), vessel(2)], history=[vessel(3)],
                  show_divider=True)
    render_frame(c, slots, Scroller(), dt=0.1)
    assert BLOCK_H * 2 + DIVIDER_H + BLOCK_H == 64


def test_history_only_starts_with_the_divider_at_the_top():
    c = Canvas(64, 64)
    slots = Slots(history=[vessel(1), vessel(2), vessel(3)], show_divider=True)
    render_frame(c, slots, Scroller(), dt=0.1)
    assert 0 in rows_with_content(c)


def test_no_divider_is_drawn_when_show_divider_is_false():
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel(1)], show_divider=False), Scroller(), dt=0.1)
    assert max(rows_with_content(c)) < BLOCK_H


def test_stale_dims_the_whole_frame():
    bright, dim = Canvas(64, 64), Canvas(64, 64)
    render_frame(bright, Slots(live=[vessel()]), Scroller(), dt=0.1, stale=False)
    render_frame(dim, Slots(live=[vessel()]), Scroller(), dt=0.1, stale=True)
    assert sum(dim.to_bytes()) < sum(bright.to_bytes())
    assert sum(dim.to_bytes()) > 0


@pytest.mark.parametrize("call_sign,destination,expected", [
    ("H3RC", "SEATTLE", "H3RC > SEATTLE"),
    (None, "SEATTLE", "> SEATTLE"),
    ("H3RC", None, "H3RC"),
    (None, None, ""),
])
def test_format_line2(call_sign, destination, expected):
    v = vessel(call_sign=call_sign, destination=destination)
    assert format_line2(v) == expected


@pytest.mark.parametrize("length_m,expected", [
    (400.0, "400m"),
    (45.0, "45m"),
    (44.6, "45m"),
    (None, ""),
])
def test_format_length(length_m, expected):
    v = vessel(length_m=length_m)
    assert format_length(v) == expected


def test_name_box_leaves_room_for_the_length_column():
    """NAME_BOX_W must end before the length column starts, or a long
    scrolling vessel name would pass directly under the length digits."""
    assert TEXT_X + NAME_BOX_W <= 64 - LENGTH_COL_W


def test_length_is_drawn_at_the_right_edge_of_the_name_row():
    c = Canvas(64, 64)
    v = vessel(name="EVER GIVEN", length_m=400.0)
    render_frame(c, Slots(live=[v]), Scroller(), dt=0.0)
    right_edge = lit_cols(c, NAME_Y_OFFSET, NAME_Y_OFFSET + FONT_H,
                          64 - LENGTH_COL_W, 64)
    assert right_edge, "expected length text in the reserved right column"


def test_no_length_text_when_length_is_unknown():
    c = Canvas(64, 64)
    v = vessel(name="EVER GIVEN", length_m=None)
    render_frame(c, Slots(live=[v]), Scroller(), dt=0.0)
    right_edge = lit_cols(c, NAME_Y_OFFSET, NAME_Y_OFFSET + FONT_H,
                          64 - LENGTH_COL_W, 64)
    assert not right_edge, "no length text should render when length is unknown"


def test_unnamed_vessel_falls_back_to_its_mmsi():
    c = Canvas(64, 64)
    v = vessel(name=None)
    render_frame(c, Slots(live=[v]), Scroller(), dt=0.1)
    assert rows_with_content(c), "an unnamed vessel must still render something"


def test_scroller_state_is_pruned_to_on_screen_fields():
    scroller = Scroller()
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel(1, name="A" * 40)]), scroller, dt=0.1)
    render_frame(c, Slots(live=[vessel(2, name="B" * 40)]), scroller, dt=0.1)
    assert all(key[0] == 2 for key in scroller._states)


def test_retain_keeps_history_only_scroll_state():
    """A vessel visible ONLY as history (not live) must still have its scroll
    state retained - otherwise its scrolling text visibly resets every frame.
    """
    scroller = Scroller()
    c = Canvas(64, 64)
    long_name_vessel = vessel(9, name="A" * 40)
    render_frame(c, Slots(history=[long_name_vessel], show_divider=True),
                scroller, dt=0.1)
    assert (9, "name") in scroller._states


def test_render_clears_the_canvas_between_frames():
    c = Canvas(64, 64)
    render_frame(c, Slots(live=[vessel(1), vessel(2), vessel(3)]), Scroller(), dt=0.1)
    render_frame(c, Slots(), Scroller(), dt=0.1)
    assert c.to_bytes() == bytes(64 * 64 * 3)


def test_short_canvas_stops_drawing_instead_of_crashing():
    """A panel height that doesn't fit a whole number of blocks (here 40px:
    exactly 2 blocks at 38px, a 3rd would need 38..57 which overflows) must
    silently stop, not raise or draw a clipped/partial block.
    """
    c = Canvas(64, 40)
    render_frame(c, Slots(live=[vessel(1), vessel(2), vessel(3)]), Scroller(), dt=0.1)
    lit_rows = {y for y in range(40) for x in range(64)
                if c.get_pixel(x, y) != (0, 0, 0)}
    assert max(lit_rows) < BLOCK_H * 2, "only the 2 blocks that fit should draw"
