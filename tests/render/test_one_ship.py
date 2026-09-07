from datetime import datetime, timezone

from ship_observer.models import ShipCategory, Vessel
from ship_observer.render.canvas import Canvas
from ship_observer.render.font import FONT_H, LARGE_FONT_H, text_width
from ship_observer.render.layout import (CALLSIGN_COLOR, DEST_COLOR,
                                         DIVIDER_LABEL)
from ship_observer.render.one_ship import (DETAILS_Y, DOT_CAP, DOT_GAP,
                                           DOT_OFF, DOT_ON, DOT_SIZE, DOTS_Y,
                                           ICON_SIZE, LINE2_Y, NAME_Y,
                                           render_one_ship)
from ship_observer.render.scroll import Scroller
from ship_observer.selection import RotationView

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)


def vessel(mmsi=1, name="EVER GIVEN", call_sign="H3RC", destination="SEATTLE",
           category=ShipCategory.CARGO, length_m=400.0, max_sog=12.0):
    return Vessel(mmsi=mmsi, entered_at=T0, last_seen=T0, name=name,
                  call_sign=call_sign, destination=destination,
                  category=category, length_m=length_m, max_sog=max_sog,
                  static_resolved=True)


def view(*vessels, from_history=False, pages=1, page=0):
    return RotationView(vessels=list(vessels), from_history=from_history,
                        pages=pages, page=page)


def lit(canvas, y0, y1, x0=0, x1=64):
    return {(x, y) for y in range(y0, y1) for x in range(x0, x1)
            if canvas.get_pixel(x, y) != (0, 0, 0)}


def lit_rows(canvas):
    return {y for y in range(canvas.height) for x in range(canvas.width)
            if canvas.get_pixel(x, y) != (0, 0, 0)}


def dot_x(index, pages):
    shown = min(pages, DOT_CAP)
    width = shown * (DOT_SIZE + DOT_GAP) - DOT_GAP
    return (64 - width) // 2 + index * (DOT_SIZE + DOT_GAP)


def test_the_bands_stack_without_overlapping_or_leaving_the_panel():
    assert ICON_SIZE <= NAME_Y
    assert NAME_Y + LARGE_FONT_H <= LINE2_Y
    assert LINE2_Y + FONT_H <= DETAILS_Y
    assert DETAILS_Y + FONT_H <= DOTS_Y
    assert DOTS_Y + DOT_SIZE <= 64


def test_empty_view_renders_a_black_panel():
    c = Canvas(64, 64)
    render_one_ship(c, RotationView(), Scroller(), dt=0.1)
    assert c.to_bytes() == bytes(64 * 64 * 3)


def test_the_32px_icon_is_centred_at_the_top():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel()), Scroller(), dt=0.0)
    icon = lit(c, 0, ICON_SIZE)
    assert icon, "no icon pixels"
    left = (64 - ICON_SIZE) // 2
    assert min(x for x, _ in icon) >= left
    assert max(x for x, _ in icon) < left + ICON_SIZE


def test_the_name_is_drawn_below_the_icon_in_the_large_font():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel(name="ORCA")), Scroller(), dt=0.0)
    band = lit(c, NAME_Y, NAME_Y + LARGE_FONT_H)
    assert band
    assert max(y for _, y in band) >= NAME_Y + 6, "taller than the 4x6 face"


def test_line2_keeps_the_two_colour_callsign_destination_split():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel()), Scroller(), dt=0.0)
    colors = {c.get_pixel(x, y) for x in range(64)
              for y in range(LINE2_Y, LINE2_Y + FONT_H)}
    assert CALLSIGN_COLOR in colors and DEST_COLOR in colors


def test_the_details_row_fits_length_speed_and_category():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel(category=ShipCategory.TUG, length_m=31.0)),
                    Scroller(), dt=0.0)
    row = lit(c, DETAILS_Y, DETAILS_Y + FONT_H)
    assert row
    assert max(x for x, _ in row) < 64


def test_a_long_category_word_drops_a_detail_instead_of_spilling():
    """PASSENGER plus a length plus a speed is wider than the panel; the
    line must lose its least important part, never run off the edge."""
    c = Canvas(64, 64)
    v = vessel(category=ShipCategory.PASSENGER, length_m=400.0, max_sog=22.5)
    render_one_ship(c, view(v), Scroller(), dt=0.0)
    row = lit(c, DETAILS_Y, DETAILS_Y + FONT_H)
    assert max(x for x, _ in row) < 64


def test_one_dot_is_drawn_per_page_with_the_current_one_highlighted():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel(), pages=3, page=1), Scroller(), dt=0.0)
    colors = [c.get_pixel(dot_x(i, 3), DOTS_Y) for i in range(3)]
    assert colors == [DOT_OFF, DOT_ON, DOT_OFF]


def test_each_dot_is_two_by_two_pixels():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel(), pages=2, page=0), Scroller(), dt=0.0)
    x = dot_x(0, 2)
    assert all(c.get_pixel(x + dx, DOTS_Y + dy) == DOT_ON
               for dx in range(DOT_SIZE) for dy in range(DOT_SIZE))
    assert c.get_pixel(x + DOT_SIZE, DOTS_Y) != DOT_ON, "dots must not touch"


def test_the_dot_row_is_centred():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel(), pages=4, page=0), Scroller(), dt=0.0)
    cols = {x for x, _ in lit(c, DOTS_Y, DOTS_Y + DOT_SIZE)}
    assert abs(min(cols) - (63 - max(cols))) <= 1, "the row must sit centred"


def test_the_dot_row_is_capped_and_still_marks_the_current_page():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel(), pages=25, page=24), Scroller(), dt=0.0)
    on = {x for x, _ in lit(c, DOTS_Y, DOTS_Y + DOT_SIZE)
          if c.get_pixel(x, DOTS_Y) == DOT_ON}
    cols = {x for x, _ in lit(c, DOTS_Y, DOTS_Y + DOT_SIZE)}
    assert len(cols) == DOT_CAP * DOT_SIZE, "at most DOT_CAP dots"
    assert on, "the current page must still be marked"
    assert min(on) >= dot_x(DOT_CAP - 1, 25), "the last page marks the last dot"


def test_a_single_page_still_shows_its_dot():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel(), pages=1, page=0), Scroller(), dt=0.0)
    assert lit(c, DOTS_Y, DOTS_Y + DOT_SIZE)


def test_history_vessels_are_tagged_last_seen():
    c = Canvas(64, 64)
    bare = vessel(length_m=None, max_sog=None, category=ShipCategory.UNKNOWN)
    render_one_ship(c, view(bare, from_history=True), Scroller(), dt=0.0)
    tag_x0 = 64 - text_width(DIVIDER_LABEL)
    assert lit(c, DETAILS_Y, DETAILS_Y + FONT_H, tag_x0, 64)


def test_the_tag_never_collides_with_the_details():
    c = Canvas(64, 64)
    v = vessel(category=ShipCategory.PASSENGER, length_m=400.0, max_sog=22.5)
    render_one_ship(c, view(v, from_history=True), Scroller(), dt=0.0)
    tag_x0 = 64 - text_width(DIVIDER_LABEL)
    left = {x for (x, _) in lit(c, DETAILS_Y, DETAILS_Y + FONT_H)
            if x < tag_x0}
    assert max(left) < tag_x0 - 1, "a blank column must separate them"


def test_nothing_is_drawn_outside_the_panel():
    c = Canvas(64, 64)
    v = vessel(name="A" * 40, destination="B" * 40)
    render_one_ship(c, view(v, pages=25, page=3), Scroller(), dt=0.0)
    assert max(lit_rows(c)) < 64


def test_stale_dims_the_whole_frame():
    bright, dim = Canvas(64, 64), Canvas(64, 64)
    render_one_ship(bright, view(vessel()), Scroller(), dt=0.0, stale=False)
    render_one_ship(dim, view(vessel()), Scroller(), dt=0.0, stale=True)
    assert 0 < sum(dim.to_bytes()) < sum(bright.to_bytes())


def test_scroller_state_is_pruned_to_the_on_screen_vessel():
    scroller, c = Scroller(), Canvas(64, 64)
    render_one_ship(c, view(vessel(1, name="A" * 40)), scroller, dt=0.1)
    render_one_ship(c, view(vessel(2, name="B" * 40)), scroller, dt=0.1)
    assert all(key[0] == 2 for key in scroller._states)


def test_render_clears_the_canvas_between_frames():
    c = Canvas(64, 64)
    render_one_ship(c, view(vessel()), Scroller(), dt=0.1)
    render_one_ship(c, RotationView(), Scroller(), dt=0.1)
    assert c.to_bytes() == bytes(64 * 64 * 3)


def test_only_the_first_vessel_of_a_page_is_drawn():
    c = Canvas(64, 64)
    one = Canvas(64, 64)
    render_one_ship(c, view(vessel(1), vessel(2)), Scroller(), dt=0.0)
    render_one_ship(one, view(vessel(1)), Scroller(), dt=0.0)
    assert c.to_bytes() == one.to_bytes()
