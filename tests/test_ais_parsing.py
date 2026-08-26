from datetime import datetime, timezone

import pytest

from ship_observer.ais_client import (
    POSITION_REPORT,
    SHIP_STATIC_DATA,
    AisMessage,
    parse_envelope,
    parse_time_utc,
)

NOW = datetime(2026, 8, 26, 17, 5, 0, tzinfo=timezone.utc)

POSITION_ENVELOPE = {
    "MessageType": "PositionReport",
    "MetaData": {
        "MMSI": 366123456,
        "ShipName": "POLAR RESOLUTE       ",
        "latitude": 47.8801,
        "longitude": -122.4102,
        "time_utc": "2026-08-26 17:04:11.123456789 +0000 UTC",
    },
    "Message": {
        "PositionReport": {
            "UserID": 366123456,
            "Latitude": 47.8801,
            "Longitude": -122.4102,
            "Sog": 12.4,
            "Cog": 176.2,
            "TrueHeading": 175,
            "NavigationalStatus": 0,
        }
    },
}

STATIC_ENVELOPE = {
    "MessageType": "ShipStaticData",
    "MetaData": {
        "MMSI": 366123456,
        "ShipName": "POLAR RESOLUTE       ",
        "latitude": 47.8801,
        "longitude": -122.4102,
        "time_utc": "2026-08-26 17:04:20.000000000 +0000 UTC",
    },
    "Message": {
        "ShipStaticData": {
            "UserID": 366123456,
            "Name": "POLAR RESOLUTE       ",
            "CallSign": "WCX8834  ",
            "Destination": "CHERRY PT            ",
            "Type": 80,
            "ImoNumber": 9312345,
            "Dimension": {"A": 180, "B": 60, "C": 16, "D": 16},
            "MaximumStaticDraught": 12.5,
            "Eta": {"Month": 8, "Day": 27, "Hour": 6, "Minute": 30},
        }
    },
}


def test_parse_time_utc_handles_go_format_with_nanoseconds():
    ts = parse_time_utc("2026-08-26 17:04:11.123456789 +0000 UTC")
    assert ts == datetime(2026, 8, 26, 17, 4, 11, 123456, tzinfo=timezone.utc)
    assert ts.tzinfo is not None


def test_parse_time_utc_handles_missing_fractional_part():
    ts = parse_time_utc("2026-08-26 17:04:11 +0000 UTC")
    assert ts == datetime(2026, 8, 26, 17, 4, 11, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw", ["", "not a time", "2026-13-45 99:99:99 +0000 UTC", None])
def test_parse_time_utc_returns_none_on_garbage(raw):
    assert parse_time_utc(raw) is None


def test_parse_position_report():
    msg = parse_envelope(POSITION_ENVELOPE, NOW)
    assert isinstance(msg, AisMessage)
    assert msg.message_type == POSITION_REPORT
    assert msg.mmsi == 366123456
    assert msg.lat == pytest.approx(47.8801)
    assert msg.lon == pytest.approx(-122.4102)
    # AIS pads strings to fixed width; trailing space must be stripped.
    assert msg.meta_name == "POLAR RESOLUTE"
    assert msg.received_at == datetime(2026, 8, 26, 17, 4, 11, 123456, tzinfo=timezone.utc)
    assert msg.payload["Sog"] == pytest.approx(12.4)


def test_parse_static_data():
    msg = parse_envelope(STATIC_ENVELOPE, NOW)
    assert msg.message_type == SHIP_STATIC_DATA
    assert msg.payload["Type"] == 80
    assert msg.payload["CallSign"] == "WCX8834  "  # payload is verbatim


def test_received_at_falls_back_to_arrival_when_timestamp_is_garbage():
    envelope = {
        **POSITION_ENVELOPE,
        "MetaData": {**POSITION_ENVELOPE["MetaData"], "time_utc": "wat"},
    }
    assert parse_envelope(envelope, NOW).received_at == NOW


@pytest.mark.parametrize("envelope", [
    {},
    {"MessageType": "PositionReport"},                       # no MetaData
    {"MessageType": "PositionReport", "MetaData": {}},       # no MMSI
    {"MessageType": "UnknownThing", "MetaData": {"MMSI": 1}},
    {"MessageType": "PositionReport", "MetaData": {"MMSI": 1}, "Message": {}},
    {"MessageType": "PositionReport", "MetaData": {"MMSI": "abc"}, "Message": {"PositionReport": {}}},
])
def test_malformed_envelopes_return_none_and_never_raise(envelope):
    assert parse_envelope(envelope, NOW) is None


@pytest.mark.parametrize("mmsi", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_mmsi_returns_none_and_never_raises(mmsi):
    """json.loads accepts bare Infinity/NaN tokens, so these reach us from the wire."""
    envelope = {
        **POSITION_ENVELOPE,
        "MetaData": {**POSITION_ENVELOPE["MetaData"], "MMSI": mmsi},
    }
    assert parse_envelope(envelope, NOW) is None


def test_blank_ship_name_becomes_none():
    envelope = {
        **POSITION_ENVELOPE,
        "MetaData": {**POSITION_ENVELOPE["MetaData"], "ShipName": "          "},
    }
    assert parse_envelope(envelope, NOW).meta_name is None
