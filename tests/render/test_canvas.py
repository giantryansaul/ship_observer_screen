import pytest

from ship_observer.render.canvas import Bitmap, Canvas

RED = (255, 0, 0)
GREEN = (0, 255, 0)


def test_new_canvas_is_black():
    c = Canvas(4, 3)
    assert c.to_bytes() == bytes(4 * 3 * 3)
    assert c.get_pixel(0, 0) == (0, 0, 0)


def test_set_and_get_pixel():
    c = Canvas(4, 3)
    c.set_pixel(1, 2, RED)
    assert c.get_pixel(1, 2) == RED
    assert c.get_pixel(0, 0) == (0, 0, 0)


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (4, 0), (0, 3), (99, 99)])
def test_out_of_bounds_writes_are_silently_clipped(x, y):
    """Clipping is the norm here - scrolling text runs off both edges."""
    c = Canvas(4, 3)
    c.set_pixel(x, y, RED)
    assert c.to_bytes() == bytes(4 * 3 * 3)


def test_to_bytes_is_row_major_rgb():
    c = Canvas(2, 2)
    c.set_pixel(0, 0, RED)
    c.set_pixel(1, 1, GREEN)
    assert c.to_bytes() == bytes([255, 0, 0,  0, 0, 0,
                                  0, 0, 0,    0, 255, 0])


def test_clear_resets_every_pixel():
    c = Canvas(2, 2)
    c.set_pixel(0, 0, RED)
    c.clear()
    assert c.to_bytes() == bytes(2 * 2 * 3)


def test_hline_is_inclusive_and_clipped():
    c = Canvas(4, 2)
    c.hline(0, 1, 2, RED)
    assert [c.get_pixel(x, 0) for x in range(4)] == [(0, 0, 0), RED, RED, (0, 0, 0)]
    c.hline(1, -5, 99, GREEN)
    assert all(c.get_pixel(x, 1) == GREEN for x in range(4))


def test_dim_scales_every_channel():
    c = Canvas(1, 1)
    c.set_pixel(0, 0, (200, 100, 50))
    c.dim(0.5)
    assert c.get_pixel(0, 0) == (100, 50, 25)


def test_bitmap_from_art_maps_palette_characters():
    bitmap = Bitmap.from_art(["#.", ".o"], {"#": RED, "o": GREEN})
    assert bitmap.width == 2 and bitmap.height == 2
    assert bitmap.pixels == (RED, None, None, GREEN)


def test_bitmap_from_art_rejects_ragged_rows():
    with pytest.raises(ValueError, match="same width"):
        Bitmap.from_art(["##", "#"], {"#": RED})


def test_bitmap_from_art_rejects_unmapped_characters():
    with pytest.raises(ValueError, match="'x'"):
        Bitmap.from_art(["#x"], {"#": RED})


def test_blit_skips_transparent_pixels():
    c = Canvas(3, 2)
    c.set_pixel(1, 0, GREEN)
    c.blit(Bitmap.from_art(["#.", "##"], {"#": RED}), 0, 0)
    assert c.get_pixel(0, 0) == RED
    assert c.get_pixel(1, 0) == GREEN, "transparent pixel must not overwrite"
    assert c.get_pixel(0, 1) == RED and c.get_pixel(1, 1) == RED


def test_blit_clips_at_the_edges():
    c = Canvas(2, 2)
    c.blit(Bitmap.from_art(["##", "##"], {"#": RED}), 1, 1)
    assert c.get_pixel(1, 1) == RED
    assert c.get_pixel(0, 0) == (0, 0, 0)
