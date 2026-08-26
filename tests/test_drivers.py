import sys

import pytest

from ship_observer.config import Settings
from ship_observer.drivers import create_driver
from ship_observer.drivers.null import NullDriver

MINIMAL = {"AIS_STREAM_API_KEY": "k",
           "BBOX": "-122.527428,47.859476,-122.323322,47.910359"}


def test_null_driver_records_frames():
    d = NullDriver(64, 64)
    frame = bytes(64 * 64 * 3)
    d.show(frame)
    assert d.frames == [frame]
    assert (d.width, d.height) == (64, 64)
    d.close()


def test_explicit_null_driver_is_honoured():
    s = Settings.from_env({**MINIMAL, "DISPLAY_DRIVER": "null"})
    assert isinstance(create_driver(s), NullDriver)


def test_auto_falls_back_to_null_off_a_pi():
    """Development happens on WSL2; rgbmatrix will not import there."""
    s = Settings.from_env({**MINIMAL, "DISPLAY_DRIVER": "auto"})
    driver = create_driver(s)
    assert isinstance(driver, NullDriver)
    assert (driver.width, driver.height) == (64, 64)


def test_explicit_rgbmatrix_falls_back_and_logs_an_error(monkeypatch):
    """A missing library must not stop the web UI from coming up - that is
    how the failure gets diagnosed remotely.
    """
    monkeypatch.setitem(sys.modules, "rgbmatrix", None)
    s = Settings.from_env({**MINIMAL, "DISPLAY_DRIVER": "rgbmatrix"})
    events = []
    driver = create_driver(s, on_event=lambda *a: events.append(a))
    assert isinstance(driver, NullDriver)
    assert any(e[0] == "ERROR" and e[1] == "display" for e in events)


def test_driver_dimensions_follow_chain_and_parallel():
    s = Settings.from_env({**MINIMAL, "DISPLAY_DRIVER": "null",
                           "MATRIX_CHAIN": "2", "MATRIX_PARALLEL": "1"})
    driver = create_driver(s)
    assert (driver.width, driver.height) == (128, 64)


def test_null_driver_rejects_wrong_sized_frames():
    d = NullDriver(8, 8)
    with pytest.raises(ValueError, match="expected"):
        d.show(bytes(10))
