"""Smoke tests for the real bundled USCG GUID dataset (no fake table)."""
from ship_observer.uscg_locations import _table, resolve_destination


def test_the_bundled_dataset_resolves_the_encoding_guides_own_examples():
    """0Y0P and 0Q6L are the USCG AIS Encoding Guide v.25's own worked
    example for a scheduled route (the Staten Island Ferry) - if the bundled
    dataset can't resolve its source's own examples, the data or the loader
    is broken."""
    result = resolve_destination("US^0Y0P><0Q6L")
    assert result is not None
    assert "FERRY" in result.detail.upper()


def test_the_bundled_dataset_has_a_large_and_plausible_row_count():
    assert 30000 < len(_table()) < 40000


def test_every_row_has_a_four_character_guid():
    for guid in _table():
        assert len(guid) == 4, f"unexpected GUID length: {guid!r}"
