"""Parsing logic for US/GUID destination codes (USCG AIS Encoding Guide v.25).

These tests exercise resolve_destination() against a small fake table, so
they stay stable if the bundled dataset is refreshed. See
test_uscg_locations_data.py for a smoke test against the real bundled file.
"""
import pytest

from ship_observer import uscg_locations as ul
from ship_observer.uscg_locations import GuidPlace, resolve_destination

FAKE_TABLE = {
    "0TEM": GuidPlace("Guemes Channel WA", "Dakota Creek Industries East Pier."),
    "016S": GuidPlace("Seattle WA", "ELLIOTT BAY GENERAL ANCHORAGE"),
    "0USS": GuidPlace("Puget Sound Area WA Other Ports", ""),
}


@pytest.fixture(autouse=True)
def fake_table(monkeypatch):
    monkeypatch.setattr(ul, "_table", lambda: FAKE_TABLE)


def test_plain_text_destination_is_left_untouched():
    assert resolve_destination("SEATTLE") is None


def test_destination_with_no_us_guid_token_is_left_untouched():
    """'XX XXX' is AIS's own placeholder for an unset origin, and bare
    UN/LOCODEs (e.g. USSEA) already read fine as-is - only US^ tokens need
    resolving."""
    assert resolve_destination("XX XXX>?? ???") is None
    assert resolve_destination("USSEA") is None


def test_none_destination_is_left_untouched():
    assert resolve_destination(None) is None


def test_unresolved_guid_code_falls_back_to_none():
    """A syntactically valid US^ token whose code isn't in the table must
    not be reported as resolved - showing the raw text is more honest than
    guessing."""
    assert resolve_destination("US^ZZZZ") is None


def test_a_route_with_one_known_and_one_unknown_code_resolves_the_known_half():
    """US^0TJZ>0U5P-style real destinations mix legs; a route we can only
    partly resolve should still show what we know rather than nothing."""
    result = resolve_destination("US^ZZZZ>016S")
    assert result is not None
    assert result.panel_text == "US^ZZZZ>SEATTLE WA"


def test_a_bare_code_after_a_us_guid_token_is_also_resolved():
    """Only the first code in a route carries the 'US^' prefix; later legs
    are bare 4-character codes in the same string, e.g. US^0TEM>016S."""
    result = resolve_destination("US^0TEM>016S")
    assert result.panel_text == "GUEMES CHANNEL WA>SEATTLE WA"


def test_a_bare_four_character_token_before_any_us_guid_prefix_is_not_touched():
    """Without a preceding US^ token establishing GUID mode, a bare
    4-character string is just text, not a code to look up."""
    result = resolve_destination("016S>US^0TEM")
    assert result.panel_text == "016S>GUEMES CHANNEL WA"


def test_resolves_a_known_guid_pair_for_the_panel():
    result = resolve_destination("US^0TEM>016S")
    assert result is not None
    assert result.panel_text == "GUEMES CHANNEL WA>SEATTLE WA"


def test_panel_text_is_uppercase_for_consistency_with_the_rest_of_the_display():
    result = resolve_destination("US^016S")
    assert result.panel_text == "SEATTLE WA"


def test_resolves_a_known_guid_pair_for_the_debug_detail():
    result = resolve_destination("US^0TEM>016S")
    assert "Guemes Channel WA" in result.detail
    assert "Dakota Creek Industries East Pier" in result.detail
    assert "Seattle WA" in result.detail


def test_detail_omits_empty_parentheses_when_no_official_name_is_known():
    result = resolve_destination("US^0USS")
    assert "()" not in result.detail
    assert "Puget Sound Area WA Other Ports" in result.detail


def test_a_single_unpaired_guid_token_still_resolves():
    """US^0NVR<< (anchored/moored/on station) has only one code."""
    result = resolve_destination("US^016S<<")
    assert result is not None
    assert result.panel_text == "SEATTLE WA<<"
