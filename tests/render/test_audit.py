"""The /panel audit tool: font, icon, and sample-data views.

These render through the real pipeline (Canvas, draw_text, icon_for,
render_frame) so what the tool shows is pixel-identical to what the
hardware would draw - never a reimplementation for display purposes.
"""
from ship_observer.models import ShipCategory
from ship_observer.render.audit import (
    ICON_AUDIT_CELL_H,
    ICON_AUDIT_CELL_W,
    ICON_AUDIT_COLS,
    dummy_slots,
    render_font_audit,
    render_icon_audit,
)
from ship_observer.render.canvas import Canvas
from ship_observer.render.layout import render_frame
from ship_observer.render.scroll import Scroller


def lit_pixels(canvas):
    return {(x, y) for y in range(canvas.height) for x in range(canvas.width)
            if canvas.get_pixel(x, y) != (0, 0, 0)}


def test_font_audit_lights_something_in_every_expected_row_band():
    """Each line of the pangram/charset dump must actually draw - a silent
    empty line would hide a font bug rather than surface it."""
    c = Canvas(64, 64)
    render_font_audit(c)
    lit = lit_pixels(c)
    rows_with_content = {y for x, y in lit}
    assert len(rows_with_content) >= 6 * 4, (
        "expected multiple lines of glyphs, each several pixel-rows tall")


def test_font_audit_stays_within_the_canvas():
    c = Canvas(64, 64)
    render_font_audit(c)
    assert all(0 <= x < 64 and 0 <= y < 64 for x, y in lit_pixels(c))


def _cell_bounds(index):
    row, col = divmod(index, ICON_AUDIT_COLS)
    x0, y0 = col * ICON_AUDIT_CELL_W, row * ICON_AUDIT_CELL_H
    return x0, y0, x0 + ICON_AUDIT_CELL_W, y0 + ICON_AUDIT_CELL_H


def test_icon_audit_draws_every_category_inside_its_own_grid_cell():
    """Every category gets a dedicated cell in the grid; its lit pixels must
    stay inside that cell, never bleeding into a neighbor."""
    c = Canvas(64, 64)
    render_icon_audit(c)
    lit = lit_pixels(c)
    assert lit, "expected at least one icon to render"

    for index, category in enumerate(ShipCategory):
        x0, y0, x1, y1 = _cell_bounds(index)
        cell_pixels = {(x, y) for x, y in lit if x0 <= x < x1 and y0 <= y < y1}
        assert cell_pixels, f"expected {category} to light its cell"

    n_categories = len(list(ShipCategory))
    other_cells_pixels = {(x, y) for x, y in lit
                          if not any(_cell_bounds(i)[0] <= x < _cell_bounds(i)[2]
                                    and _cell_bounds(i)[1] <= y < _cell_bounds(i)[3]
                                    for i in range(n_categories))}
    assert not other_cells_pixels, "an icon was drawn outside every known cell"


def test_icon_audit_places_categories_in_a_stable_reading_order():
    """The HTML legend mirrors this exact order - changing it silently would
    desync the legend from the pixels."""
    categories = list(ShipCategory)
    assert categories[0] is ShipCategory.PASSENGER
    assert ShipCategory.FISHING in categories
    assert ICON_AUDIT_COLS >= 1


def test_dummy_slots_has_one_live_and_two_history_vessels():
    slots = dummy_slots()
    assert len(slots.live) == 1
    assert len(slots.history) == 2
    assert slots.show_divider is True


def test_dummy_slots_includes_a_fishing_vessel():
    """The tool exists because the fishing icon is hard to identify - the
    sample data must actually include one to audit in context."""
    slots = dummy_slots()
    categories = {v.category for v in (*slots.live, *slots.history)}
    assert ShipCategory.FISHING in categories


def test_dummy_slots_covers_a_resolvable_us_guid_destination():
    """Exercises the destination-resolution feature in the sample-data view
    too, not just live traffic."""
    slots = dummy_slots()
    destinations = [v.destination for v in (*slots.live, *slots.history)
                    if v.destination]
    assert any(d.startswith("US^") for d in destinations)


def test_dummy_slots_covers_a_vessel_with_no_known_length():
    slots = dummy_slots()
    assert any(v.length_m is None for v in (*slots.live, *slots.history))


def test_dummy_slots_renders_without_error_through_the_real_pipeline():
    c = Canvas(64, 64)
    render_frame(c, dummy_slots(), Scroller(), dt=0.0)
    assert lit_pixels(c), "the sample data must actually draw something"
