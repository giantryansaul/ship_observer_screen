from datetime import datetime, timezone

import pytest

from ship_observer.models import DisplayMode, ShipCategory, Vessel
from ship_observer.render.canvas import Canvas
from ship_observer.render.display import PAGE_SIZE, render_display
from ship_observer.render.layout import render_frame
from ship_observer.render.one_ship import render_one_ship
from ship_observer.render.scroll import Scroller
from ship_observer.render.two_ship import render_two_ship
from ship_observer.selection import RotationView, Slots

T0 = datetime(2026, 8, 26, 17, 0, 0, tzinfo=timezone.utc)


def vessel(mmsi=1, name="EVER GIVEN"):
    return Vessel(mmsi=mmsi, entered_at=T0, last_seen=T0, name=name,
                  call_sign="H3RC", destination="SEATTLE",
                  category=ShipCategory.CARGO, length_m=400.0, max_sog=12.0,
                  static_resolved=True)


def frame(render, *args, **kwargs):
    """One frame from a canvas-first layout renderer."""
    canvas = Canvas(64, 64)
    render(canvas, *args, **kwargs)
    return canvas.to_bytes()


def displayed(mode, slots, dt=0.0, **kwargs):
    """One frame through the dispatcher, in `mode`."""
    canvas = Canvas(64, 64)
    render_display(mode, canvas, slots, Scroller(), dt, **kwargs)
    return canvas.to_bytes()


def test_three_ship_routes_to_the_existing_layout_unchanged():
    slots = Slots(live=[vessel(1), vessel(2)], history=[vessel(3)],
                  show_divider=True)
    assert (displayed(DisplayMode.THREE_SHIP, slots, 0.1)
            == frame(render_frame, slots, Scroller(), 0.1))


def test_two_ship_routes_to_the_two_ship_layout():
    slots = Slots(live=[vessel(1), vessel(2), vessel(3)])
    view = RotationView.from_slots(slots, PAGE_SIZE[DisplayMode.TWO_SHIP])
    assert (displayed(DisplayMode.TWO_SHIP, slots, 0.0)
            == frame(render_two_ship, view, Scroller(), 0.0))


def test_one_ship_routes_to_the_one_ship_layout():
    slots = Slots(live=[vessel(1), vessel(2)])
    view = RotationView.from_slots(slots, PAGE_SIZE[DisplayMode.ONE_SHIP])
    assert (displayed(DisplayMode.ONE_SHIP, slots, 0.0)
            == frame(render_one_ship, view, Scroller(), 0.0))


def test_an_explicit_rotation_view_wins_over_the_slots():
    """The service hands the layout the page its rotation clock is on; the
    slots are only the fallback for still renders like the audit tool."""
    slots = Slots(live=[vessel(1, name="FROM SLOTS")])
    view = RotationView(vessels=[vessel(2, name="FROM VIEW")], pages=4, page=2)
    assert (displayed(DisplayMode.ONE_SHIP, slots, 0.0, view=view)
            == frame(render_one_ship, view, Scroller(), 0.0))


def test_history_only_slots_render_as_a_last_seen_page():
    slots = Slots(history=[vessel(1)], show_divider=True)
    view = RotationView.from_slots(slots, PAGE_SIZE[DisplayMode.ONE_SHIP])
    assert view.from_history is True
    assert (displayed(DisplayMode.ONE_SHIP, slots, 0.0)
            == frame(render_one_ship, view, Scroller(), 0.0))


@pytest.mark.parametrize("mode", list(DisplayMode))
def test_every_mode_renders_an_empty_box_as_a_black_panel(mode):
    assert displayed(mode, Slots(), 0.1) == bytes(64 * 64 * 3)


@pytest.mark.parametrize("mode", list(DisplayMode))
def test_every_mode_honours_the_stale_flag(mode):
    slots = Slots(live=[vessel(1), vessel(2)])
    bright = displayed(mode, slots, 0.0, stale=False)
    dim = displayed(mode, slots, 0.0, stale=True)
    assert 0 < sum(dim) < sum(bright)


@pytest.mark.parametrize("mode", list(DisplayMode))
def test_no_mode_draws_outside_the_panel(mode):
    canvas = Canvas(64, 64)
    slots = Slots(live=[vessel(i, name="A" * 30) for i in range(1, 4)])
    render_display(mode, canvas, slots, Scroller(), 0.0)
    assert len(canvas.to_bytes()) == 64 * 64 * 3
    assert any(canvas.to_bytes()), "something must actually be drawn"
