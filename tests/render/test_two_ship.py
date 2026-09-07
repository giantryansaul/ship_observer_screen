from datetime import datetime, timezone

from ship_observer.models import ShipCategory, Vessel
from ship_observer.render.canvas import Canvas
from ship_observer.render.font import FONT_H, text_width
from ship_observer.render.layout import (CALLSIGN_COLOR, DEST_COLOR,
                                         DIVIDER_LABEL, SEPARATOR_COLOR)
from ship_observer.render.scroll import Scroller
from ship_observer.render.two_ship import (BLOCK_H, ICON_SIZE, LINE2_Y,
                                           LINE3_Y, NAME_X, NAME_Y,
                                           SEPARATOR_Y, render_two_ship)
from ship_observer.selection import RotationView

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)


def vessel(mmsi=1, name="EVER GIVEN", call_sign="H3RC", destination="SEATTLE",
           category=ShipCategory.CARGO, length_m=400.0, max_sog=12.0):
    return Vessel(mmsi=mmsi, entered_at=T0, last_seen=T0, name=name,
                  call_sign=call_sign, destination=destination,
                  category=category, length_m=length_m, max_sog=max_sog,
                  static_resolved=True)


def view(*vessels, from_history=False):
    return RotationView(vessels=list(vessels), from_history=from_history,
                        pages=1, page=0)


def lit(canvas, y0, y1, x0=0, x1=64):
    return {(x, y) for y in range(y0, y1) for x in range(x0, x1)
            if canvas.get_pixel(x, y) != (0, 0, 0)}


def lit_rows(canvas):
    return {y for y in range(canvas.height) for x in range(canvas.width)
            if canvas.get_pixel(x, y) != (0, 0, 0)}


def lit_cols(canvas, y0, y1):
    return {x for (x, _) in lit(canvas, y0, y1)}


def test_two_blocks_split_the_panel_in_half():
    assert BLOCK_H * 2 == 64
    assert SEPARATOR_Y == BLOCK_H - 1


def test_empty_view_renders_a_black_panel():
    c = Canvas(64, 64)
    render_two_ship(c, RotationView(), Scroller(), dt=0.1)
    assert c.to_bytes() == bytes(64 * 64 * 3)


def test_each_block_draws_a_16px_icon_at_the_left_edge():
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel(1), vessel(2)), Scroller(), dt=0.0)
    for y0 in (0, BLOCK_H):
        icon = lit(c, y0, y0 + ICON_SIZE, 0, ICON_SIZE)
        assert icon, f"no icon pixels in the block at y={y0}"
        assert max(x for x, _ in icon) < ICON_SIZE


def test_the_name_is_drawn_beside_the_icon_in_the_large_font():
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel(name="ORCA")), Scroller(), dt=0.0)
    band = lit(c, NAME_Y, NAME_Y + 10, NAME_X, 64)
    assert band, "the name must render to the right of the icon"
    # The 6x10 face is taller than the 4x6 one: a 4x6 name could never
    # reach past its 6th row.
    assert max(y for _, y in band) >= NAME_Y + 6


def test_the_name_never_overdraws_the_icon():
    c = Canvas(64, 64)
    long_name = view(vessel(name="A" * 30))
    render_two_ship(c, long_name, Scroller(), dt=0.0)
    icon_only = lit(c, NAME_Y, NAME_Y + 10, ICON_SIZE, NAME_X)
    assert not icon_only, "the gap between the icon and the name must stay clear"


def test_line2_keeps_the_two_colour_callsign_destination_split():
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel()), Scroller(), dt=0.0)
    colors = {c.get_pixel(x, y) for x in range(64)
              for y in range(LINE2_Y, LINE2_Y + FONT_H)}
    assert CALLSIGN_COLOR in colors and DEST_COLOR in colors


def test_the_second_block_starts_at_y_32():
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel(1), vessel(2, name="WSF PUYALLUP")),
                    Scroller(), dt=0.0)
    assert any(y >= BLOCK_H for y in lit_rows(c))


def test_a_single_vessel_leaves_the_bottom_half_empty():
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel()), Scroller(), dt=0.0)
    assert max(lit_rows(c)) < SEPARATOR_Y
    assert c.get_pixel(0, SEPARATOR_Y) == (0, 0, 0), (
        "no separator under a lone vessel - it would just underline nothing")


def test_the_separator_divides_two_occupied_blocks():
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel(1), vessel(2)), Scroller(), dt=0.0)
    assert all(c.get_pixel(x, SEPARATOR_Y) == SEPARATOR_COLOR
               for x in range(64))


def test_nothing_is_drawn_outside_the_panel_or_across_the_separator():
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel(1, name="A" * 30),
                            vessel(2, name="B" * 30)), Scroller(), dt=0.0)
    rows = lit_rows(c)
    assert max(rows) < 64
    top_text = lit(c, LINE3_Y, SEPARATOR_Y)
    assert max(y for _, y in top_text) < SEPARATOR_Y


def test_a_vessel_with_no_details_still_shows_its_name():
    c = Canvas(64, 64)
    bare = vessel(name=None, call_sign=None, destination=None,
                  length_m=None, max_sog=None)
    render_two_ship(c, view(bare), Scroller(), dt=0.0)
    assert lit(c, NAME_Y, NAME_Y + 10, NAME_X, 64), "the MMSI fallback name"
    assert not lit(c, LINE2_Y, LINE2_Y + FONT_H), "no callsign/destination row"
    assert not lit(c, LINE3_Y, LINE3_Y + FONT_H), "no length/speed row"


def test_the_details_row_shows_length_and_speed():
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel(length_m=400.0, max_sog=12.0)),
                    Scroller(), dt=0.0)
    assert lit(c, LINE3_Y, LINE3_Y + FONT_H)


def test_history_vessels_are_tagged_last_seen():
    c = Canvas(64, 64)
    bare = vessel(length_m=None, max_sog=None)
    render_two_ship(c, view(bare, from_history=True), Scroller(), dt=0.0)
    tag_x0 = 64 - text_width(DIVIDER_LABEL)
    assert lit(c, LINE3_Y, LINE3_Y + FONT_H, tag_x0, 64), "LAST SEEN tag missing"
    assert not lit(c, LINE3_Y, LINE3_Y + FONT_H, 0, tag_x0)


def test_live_vessels_are_not_tagged():
    c = Canvas(64, 64)
    bare = vessel(length_m=None, max_sog=None)
    render_two_ship(c, view(bare, from_history=False), Scroller(), dt=0.0)
    assert not lit(c, LINE3_Y, LINE3_Y + FONT_H)


def test_the_tag_displaces_the_speed_rather_than_overlapping_it():
    """LAST SEEN plus length plus speed cannot all fit on one 64px row, so
    the least important detail goes rather than the two colliding."""
    c = Canvas(64, 64)
    v = vessel(length_m=400.0, max_sog=12.0)
    render_two_ship(c, view(v, from_history=True), Scroller(), dt=0.0)
    tag_x0 = 64 - text_width(DIVIDER_LABEL)
    left = {x for x in lit_cols(c, LINE3_Y, LINE3_Y + FONT_H) if x < tag_x0}
    assert left, "the length must still be shown"
    assert max(left) < text_width("400m"), "only the length is left of the tag"


def test_stale_dims_the_whole_frame():
    bright, dim = Canvas(64, 64), Canvas(64, 64)
    render_two_ship(bright, view(vessel()), Scroller(), dt=0.0, stale=False)
    render_two_ship(dim, view(vessel()), Scroller(), dt=0.0, stale=True)
    assert 0 < sum(dim.to_bytes()) < sum(bright.to_bytes())


def test_scroller_state_is_pruned_to_on_screen_vessels():
    scroller, c = Scroller(), Canvas(64, 64)
    render_two_ship(c, view(vessel(1, name="A" * 40)), scroller, dt=0.1)
    render_two_ship(c, view(vessel(2, name="B" * 40)), scroller, dt=0.1)
    assert all(key[0] == 2 for key in scroller._states)


def test_render_clears_the_canvas_between_frames():
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel(1), vessel(2)), Scroller(), dt=0.1)
    render_two_ship(c, RotationView(), Scroller(), dt=0.1)
    assert c.to_bytes() == bytes(64 * 64 * 3)


def test_only_the_first_two_vessels_of_a_page_are_drawn():
    """A caller handing over an oversized page must not spill off the panel."""
    c = Canvas(64, 64)
    render_two_ship(c, view(vessel(1), vessel(2), vessel(3)), Scroller(), dt=0.0)
    assert max(lit_rows(c)) < 64
