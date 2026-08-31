import pytest

from ship_observer.render.canvas import Canvas
from ship_observer.render.font import FONT_H, FONT_W, Font, draw_text, max_chars, text_width

WHITE = (255, 255, 255)


def test_font_cell_dimensions_match_the_spec():
    assert (FONT_W, FONT_H) == (4, 6)


def test_max_chars_for_a_64px_panel():
    """16 characters across is the constraint the whole layout is built on."""
    assert max_chars(64) == 16
    assert max_chars(54) == 13   # arbitrary example width, not tied to layout.py
    assert max_chars(3) == 0


def test_text_width_is_cell_width_times_length():
    assert text_width("ABC") == 12
    assert text_width("") == 0


def test_font_covers_every_character_ais_can_emit():
    font = Font.default()
    for code in range(32, 127):
        assert font.glyph(chr(code)) is not None, f"missing glyph for {chr(code)!r}"


def test_unmappable_characters_fall_back_rather_than_raising():
    font = Font.default()
    assert font.glyph("é") is not None   # é -> fallback box


def test_draw_text_marks_pixels_inside_its_advance_box():
    c = Canvas(64, 8)
    draw_text(c, "A", 0, 0, WHITE)
    lit = [(x, y) for y in range(8) for x in range(64)
           if c.get_pixel(x, y) != (0, 0, 0)]
    assert lit, "drawing 'A' must light at least one pixel"
    assert all(0 <= x < FONT_W and 0 <= y < FONT_H for x, y in lit)


def test_draw_text_advances_one_cell_per_character():
    c = Canvas(64, 8)
    draw_text(c, "AA", 0, 0, WHITE)
    first = {(x, y) for y in range(8) for x in range(FONT_W)
             if c.get_pixel(x, y) != (0, 0, 0)}
    second = {(x - FONT_W, y) for y in range(8) for x in range(FONT_W, FONT_W * 2)
              if c.get_pixel(x, y) != (0, 0, 0)}
    assert first == second


def test_space_draws_nothing():
    c = Canvas(64, 8)
    draw_text(c, " ", 0, 0, WHITE)
    assert c.to_bytes() == bytes(64 * 8 * 3)


def test_draw_text_uses_the_requested_colour():
    c = Canvas(64, 8)
    draw_text(c, "A", 0, 0, (12, 34, 56))
    lit = {c.get_pixel(x, y) for y in range(8) for x in range(64)
           if c.get_pixel(x, y) != (0, 0, 0)}
    assert lit == {(12, 34, 56)}


def test_negative_x_clips_instead_of_wrapping():
    """Scrolling text is drawn at negative offsets every frame."""
    c = Canvas(64, 8)
    draw_text(c, "AAAA", -FONT_W, 0, WHITE)
    assert all(c.get_pixel(x, y) == (0, 0, 0)
               for y in range(8) for x in range(60, 64))


def test_clip_window_confines_drawing():
    c = Canvas(64, 8)
    draw_text(c, "AAAAAAAA", 0, 0, WHITE, clip_x0=10, clip_x1=20)
    lit_x = [x for y in range(8) for x in range(64) if c.get_pixel(x, y) != (0, 0, 0)]
    assert lit_x, "something must be drawn inside the window"
    assert all(10 <= x <= 20 for x in lit_x)


def test_n_uses_the_gate_glyph_not_the_stock_diagonal():
    """Misc-Fixed draws N as two corner pixels standing in for a diagonal,
    which reads as a smudge at LED scale. The user picked the "gate" form -
    full-height verticals with a bar across the top-left - from the type
    specimen review (2026-08-31)."""
    assert Font.default().glyph("N") == (0b1100, 0b1010, 0b1010,
                                         0b1010, 0b1010, 0b0000)
