from ship_observer.render.font import Font
from ship_observer.render.scroll import Scroller

KEY = (366123456, "name")


def test_text_that_fits_never_moves():
    s = Scroller()
    for _ in range(200):
        assert s.offset_for(KEY, "SHORT", box_width=64, dt=0.1) == 0


def test_overflowing_text_holds_then_scrolls_left():
    s = Scroller(speed_px_s=10.0, pause_s=1.0)
    text = "A" * 40   # 160 px in a 64 px box

    assert s.offset_for(KEY, text, 64, dt=0.5) == 0, "still in the opening hold"
    assert s.offset_for(KEY, text, 64, dt=0.6) == 0, "hold ends exactly at 1.0s"
    s.offset_for(KEY, text, 64, dt=1.0)
    assert s.offset_for(KEY, text, 64, dt=0.0) < 0, "must have moved left"


def test_scroll_stops_at_the_end_of_the_text():
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    text = "A" * 40
    for _ in range(50):
        offset = s.offset_for(KEY, text, 64, dt=0.1)
    assert offset == -(40 * 4 - 64), "must stop with the last character flush right"


def test_scroll_returns_to_the_start_after_the_trailing_hold():
    s = Scroller(speed_px_s=1000.0, pause_s=0.5)
    text = "A" * 40
    for _ in range(20):
        s.offset_for(KEY, text, 64, dt=0.1)   # reach the end and hold
    for _ in range(20):
        offset = s.offset_for(KEY, text, 64, dt=0.1)
    assert offset == 0, "cycle must return to the start"


def test_changing_the_text_resets_the_scroll():
    """A destination update must not leave the scroller mid-travel."""
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    for _ in range(10):
        s.offset_for(KEY, "A" * 40, 64, dt=0.1)
    assert s.offset_for(KEY, "B" * 40, 64, dt=0.0) == 0


def test_shrinking_the_box_starts_a_scroll():
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    assert s.offset_for(KEY, "A" * 12, box_width=64, dt=0.1) == 0
    for _ in range(5):
        offset = s.offset_for(KEY, "A" * 12, box_width=20, dt=0.1)
    assert offset < 0


def test_keys_are_independent():
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    a, b = (1, "name"), (2, "name")
    for _ in range(10):
        s.offset_for(a, "A" * 40, 64, dt=0.1)
    assert s.offset_for(b, "A" * 40, 64, dt=0.0) == 0


def test_text_that_only_overflows_in_the_large_font_scrolls_there():
    """12 characters is 48 px in the 4x6 font (fits a 64 px box) but 72 px in
    the 6x10 font - the scroller has to measure with the font being drawn."""
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    text = "A" * 12
    assert s.offset_for(KEY, text, 64, dt=0.1) == 0
    for _ in range(5):
        offset = s.offset_for(KEY, text, 64, dt=0.1, font=Font.large())
    assert offset < 0


def test_large_font_scroll_stops_at_the_end_of_the_text():
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    text = "A" * 20   # 120 px in the 6x10 font
    for _ in range(50):
        offset = s.offset_for(KEY, text, 64, dt=0.1, font=Font.large())
    assert offset == -(20 * 6 - 64), "must stop with the last character flush right"


def test_changing_the_font_resets_the_scroll():
    """Switching display mode re-measures the same text; a leftover offset
    from the other font would start the field mid-travel."""
    s = Scroller(speed_px_s=1000.0, pause_s=0.0)
    for _ in range(10):
        s.offset_for(KEY, "A" * 40, 64, dt=0.1)
    assert s.offset_for(KEY, "A" * 40, 64, dt=0.0, font=Font.large()) == 0


def test_retain_drops_state_for_departed_vessels():
    s = Scroller()
    s.offset_for((1, "name"), "A" * 40, 64, dt=0.1)
    s.offset_for((2, "name"), "A" * 40, 64, dt=0.1)
    s.retain({(1, "name")})
    assert set(s._states) == {(1, "name")}
