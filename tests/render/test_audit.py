"""The /panel audit tool: font, icon, and sample-data views.

These render through the real pipeline (Canvas, draw_text, icon_for,
render_frame) so what the tool shows is pixel-identical to what the
hardware would draw - never a reimplementation for display purposes.
"""
import pytest

from ship_observer.models import ShipCategory
from ship_observer.render.audit import (
    ICON_AUDIT_CELL_H,
    ICON_AUDIT_CELL_W,
    ICON_AUDIT_COLS,
    ICON_AUDIT_GRIDS,
    dummy_slots,
    icon_audit_categories,
    icon_audit_pages,
    render_font_audit,
    render_icon_audit,
)
from ship_observer.render.canvas import Canvas
from ship_observer.render.font import Font
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


def test_font_audit_can_render_the_large_font():
    """The 6x10 view has to draw the same dump through the same pipeline -
    fewer, taller lines - without spilling off the panel."""
    c = Canvas(64, 64)
    render_font_audit(c, font=Font.large())
    lit = lit_pixels(c)
    assert lit, "expected the large font to draw something"
    assert all(0 <= x < 64 and 0 <= y < 64 for x, y in lit)

    small = Canvas(64, 64)
    render_font_audit(small)
    assert lit != lit_pixels(small), "the large view must differ from the 4x6 one"


def test_font_audit_wraps_to_the_large_font_line_length():
    """10 characters per 64 px line in the 6x10 font: a line drawn at the
    4x6 width would run off the right edge."""
    c = Canvas(64, 64)
    render_font_audit(c, font=Font.large())
    assert max(x for x, y in lit_pixels(c)) < 60


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


def test_icon_audit_pages_the_bigger_icons_across_frames():
    """11 categories: the 8px grid still fits on one frame, the 16px grid
    takes 9 at a time, the 32px grid 4."""
    assert icon_audit_pages(8) == 1
    assert icon_audit_pages(16) == 2
    assert icon_audit_pages(32) == 3


@pytest.mark.parametrize("size", [8, 16, 32])
def test_icon_audit_pages_cover_every_category_exactly_once(size):
    """Paging must partition the categories, in ShipCategory order - a
    dropped or repeated category would make the tool lie about coverage."""
    shown = [category
             for page in range(icon_audit_pages(size))
             for category in icon_audit_categories(size, page)]
    assert shown == list(ShipCategory)


@pytest.mark.parametrize("size", [8, 16, 32])
def test_icon_audit_grid_fits_the_panel(size):
    grid = ICON_AUDIT_GRIDS[size]
    assert grid.cols * grid.cell_w <= 64
    assert grid.rows * grid.cell_h <= 64
    assert grid.margin + size <= min(grid.cell_w, grid.cell_h)


@pytest.mark.parametrize("size", [8, 16, 32])
def test_icon_audit_draws_every_page_inside_the_canvas(size):
    for page in range(icon_audit_pages(size)):
        c = Canvas(64, 64)
        render_icon_audit(c, size=size, page=page)
        lit = lit_pixels(c)
        assert lit, f"expected {size}px page {page} to draw something"
        assert all(0 <= x < 64 and 0 <= y < 64 for x, y in lit)


@pytest.mark.parametrize("size", [16, 32])
def test_icon_audit_keeps_each_bigger_icon_in_its_own_cell(size):
    """Same guarantee as the 8px grid: position alone identifies an icon, so
    nothing may bleed into a neighbouring cell."""
    grid = ICON_AUDIT_GRIDS[size]
    for page in range(icon_audit_pages(size)):
        c = Canvas(64, 64)
        render_icon_audit(c, size=size, page=page)
        lit = lit_pixels(c)
        categories = icon_audit_categories(size, page)
        covered = set()
        for index, category in enumerate(categories):
            row, col = divmod(index, grid.cols)
            x0, y0 = col * grid.cell_w, row * grid.cell_h
            cell = {(x, y) for x, y in lit
                    if x0 <= x < x0 + grid.cell_w and y0 <= y < y0 + grid.cell_h}
            assert cell, f"expected {category.value} to light its cell"
            covered |= cell
        assert covered == lit, "an icon drew outside the cells of its page"


def test_icon_audit_defaults_to_the_original_single_page_8px_grid():
    default = Canvas(64, 64)
    render_icon_audit(default)
    explicit = Canvas(64, 64)
    render_icon_audit(explicit, size=8, page=0)
    assert default.to_bytes() == explicit.to_bytes()


def test_icon_audit_rejects_a_size_with_no_grid():
    with pytest.raises(ValueError):
        render_icon_audit(Canvas(64, 64), size=12)
    with pytest.raises(ValueError):
        icon_audit_pages(12)


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
