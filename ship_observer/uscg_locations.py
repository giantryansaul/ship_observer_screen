"""Resolve US/GUID destination codes to place names.

Per the USCG AIS Encoding Guide v.25, a vessel's destination field is meant
to hold `Origination>Destination` using either a 5-character UN/LOCODE or a
4-character U.S. Geographic Unique ID (US/GUID) for waterway locations that
have no UN/LOCODE, written as `US^XXXX`. Symbols vary the relationship:
`>` one-way, `><` a scheduled round route, `<<` anchored/moored/on-station,
`<>` operating solely within one area - but only the `US^XXXX` tokens
themselves need a lookup to become readable; the symbols are already plain
text and bare UN/LOCODEs (e.g. USSEA) already read fine as-is.

data/us_guid_codes.csv.gz is a trimmed, deduplicated copy of NAVCEN's public
GUID export (fetched 2026-08-31 from
navcen.uscg.gov/sites/default/files/doc/GUID-Sorted-By-Latitude-Longitude-Type-Name.csv),
keeping only the GUID, Port Name and Official Name columns.
"""
from __future__ import annotations

import csv
import functools
import gzip
import re
from pathlib import Path
from typing import NamedTuple

DATA_PATH = Path(__file__).parent / "data" / "us_guid_codes.csv.gz"

# Splits a destination string into tokens and the operators between them,
# keeping the operators in the result. Longer operators must be tried first
# so '><' isn't split into two lone '>' matches.
_SPLIT = re.compile(r"(><|<<|<>|>)")
_GUID_CODE = re.compile(r"[0-9A-Za-z]{4}")


class GuidPlace(NamedTuple):
    port_name: str
    official_name: str


class ResolvedDestination(NamedTuple):
    panel_text: str    # short form for the LED panel: Port Name only
    detail: str         # fuller form for the debug page


@functools.lru_cache(maxsize=1)
def _table() -> dict[str, GuidPlace]:
    table: dict[str, GuidPlace] = {}
    with gzip.open(DATA_PATH, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            table[row["guid"]] = GuidPlace(row["port_name"], row["official_name"])
    return table


def lookup_guid(code: str) -> GuidPlace | None:
    return _table().get(code.upper())


def _detail_for(place: GuidPlace) -> str:
    if place.official_name:
        return f"{place.port_name} ({place.official_name})"
    return place.port_name


def resolve_destination(raw: str | None) -> ResolvedDestination | None:
    """Replace US/GUID codes in an AIS destination string with the place
    they resolve to. Only the first code in a route carries the 'US^'
    prefix (US^0Y0P><0Q6L is one route, not two) - once one has been seen,
    later bare 4-character tokens are treated as codes too. Returns None
    when nothing in the string resolves, so callers fall back to the raw
    text; a partially-resolvable route still resolves the parts it can.
    """
    if not raw or "US^" not in raw:
        return None

    in_guid_route = False
    resolved_any = False
    panel_parts: list[str] = []
    detail_parts: list[str] = []

    for part in _SPLIT.split(raw.strip()):
        if _SPLIT.fullmatch(part):
            panel_parts.append(part)
            detail_parts.append(part)
            continue

        code = None
        if part.startswith("US^") and _GUID_CODE.fullmatch(part[3:]):
            code = part[3:]
            in_guid_route = True
        elif in_guid_route and _GUID_CODE.fullmatch(part):
            code = part

        place = lookup_guid(code) if code else None
        if place is not None:
            resolved_any = True
            panel_parts.append(place.port_name.upper())
            detail_parts.append(_detail_for(place))
        else:
            panel_parts.append(part)
            detail_parts.append(part)

    if not resolved_any:
        return None
    return ResolvedDestination(panel_text="".join(panel_parts),
                               detail="".join(detail_parts))
