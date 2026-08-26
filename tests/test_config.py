import pytest
from ship_observer.config import ConfigError, Settings
from ship_observer.models import ShipCategory

MINIMAL = {
    "AIS_STREAM_API_KEY": "secret-key-value",
    "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
}


def test_defaults_match_spec():
    s = Settings.from_env(MINIMAL)
    assert s.ship_log_days == 7
    assert s.event_log_hours == 48
    assert s.display_history is False
    assert s.ship_timeout_seconds == 900
    assert s.max_ships == 3
    assert s.min_length_meters == 0.0
    assert s.exclude_categories == frozenset()
    assert s.priority_selection is True
    assert s.stale_seconds == 120
    assert s.render_fps == 15
    assert s.web_fps == 10
    assert s.display_driver == "auto"
    assert s.matrix_rows == 64 and s.matrix_cols == 64
    assert s.http_port == 8080
    assert s.record_raw_path is None


@pytest.mark.parametrize("missing", ["AIS_STREAM_API_KEY", "BBOX"])
def test_required_variables(missing):
    env = {k: v for k, v in MINIMAL.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        Settings.from_env(env)


def test_bad_bbox_raises_config_error_naming_the_variable():
    with pytest.raises(ConfigError, match="BBOX"):
        Settings.from_env({**MINIMAL, "BBOX": "1,2,3"})


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("no", False), ("off", False),
])
def test_boolean_parsing(raw, expected):
    s = Settings.from_env({**MINIMAL, "DISPLAY_HISTORY": raw})
    assert s.display_history is expected


@pytest.mark.parametrize("var,attr,default", [
    ("DISPLAY_HISTORY", "display_history", False),
    ("PRIORITY_SELECTION", "priority_selection", True),
])
def test_blank_boolean_falls_back_to_the_default(var, attr, default):
    """Blank means unset, as it does for the numeric parsers.

    Treating it as False would silently invert PRIORITY_SELECTION, whose
    documented default is True.
    """
    assert getattr(Settings.from_env({**MINIMAL, var: ""}), attr) is default
    assert getattr(Settings.from_env({**MINIMAL, var: "   "}), attr) is default


def test_unparseable_boolean_is_rejected():
    with pytest.raises(ConfigError, match="DISPLAY_HISTORY"):
        Settings.from_env({**MINIMAL, "DISPLAY_HISTORY": "maybe"})


def test_invalid_log_level_is_rejected():
    """A typo must fail at startup, not silently degrade to INFO."""
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        Settings.from_env({**MINIMAL, "LOG_LEVEL": "INF0"})


def test_exclude_categories_parsed():
    s = Settings.from_env({**MINIMAL, "EXCLUDE_CATEGORIES": "fishing,sailing"})
    assert s.exclude_categories == frozenset(
        {ShipCategory.FISHING, ShipCategory.SAILING}
    )


def test_bad_exclude_category_raises_config_error():
    with pytest.raises(ConfigError, match="EXCLUDE_CATEGORIES"):
        Settings.from_env({**MINIMAL, "EXCLUDE_CATEGORIES": "kayak"})


@pytest.mark.parametrize("var,value", [
    ("SHIP_LOG_DAYS", "0"),
    ("EVENT_LOG_HOURS", "-1"),
    ("MAX_SHIPS", "0"),
    ("RENDER_FPS", "0"),
    ("MATRIX_BRIGHTNESS", "101"),
    ("HTTP_PORT", "70000"),
    ("SHIP_LOG_DAYS", "seven"),
    ("MIN_LENGTH_METERS", "-5"),
    ("DISPLAY_DRIVER", "oled"),
])
def test_invalid_values_rejected(var, value):
    with pytest.raises(ConfigError, match=var):
        Settings.from_env({**MINIMAL, var: value})


def test_redacted_never_exposes_the_api_key():
    s = Settings.from_env(MINIMAL)
    blob = s.redacted()
    assert "secret-key-value" not in repr(blob)
    assert blob["ais_stream_api_key"] == "***redacted***"
    # The parsed corners are surfaced so a transposed BBOX paste is visible.
    assert blob["bbox"] == {
        "lon_min": -122.527428, "lat_min": 47.859476,
        "lon_max": -122.323322, "lat_max": 47.910359,
    }


def test_repr_does_not_leak_the_key():
    s = Settings.from_env(MINIMAL)
    assert "secret-key-value" not in repr(s)
