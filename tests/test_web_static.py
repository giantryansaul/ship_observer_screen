import re
from pathlib import Path

import pytest

STATIC = Path("ship_observer/web/static")


def test_all_static_files_exist():
    for name in ("index.html", "app.js", "style.css"):
        assert (STATIC / name).is_file(), f"{name} is missing"


def test_index_references_local_assets_only():
    """The Pi serves this over the LAN; it must work from a cold cache."""
    html = (STATIC / "index.html").read_text()
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert external == [], f"external assets are not allowed: {external}"


def test_index_mounts_every_panel_the_spec_requires():
    html = (STATIC / "index.html").read_text()
    for element_id in ("panel", "live-table", "traffic-summary",
                       "event-log", "config"):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_app_js_connects_to_the_frame_websocket():
    js = (STATIC / "app.js").read_text()
    assert "/ws/frames" in js
    assert "putImageData" in js, "the mirror must be pixel-accurate, not scaled art"


def test_app_js_polls_every_api_endpoint():
    js = (STATIC / "app.js").read_text()
    for endpoint in ("/api/traffic-summary", "/api/events"):
        assert endpoint in js


def test_app_js_never_interpolates_event_fields_into_innerhtml():
    """event.message can carry AIS-broadcast ship-name text. AIS is an open,
    unauthenticated protocol, so that string is attacker-controlled - it must
    be assigned via textContent, never interpolated into an innerHTML string,
    or a malicious ship name becomes a stored XSS payload on this page.
    """
    js = (STATIC / "app.js").read_text()
    assert "e.message" not in re.sub(r"\.textContent\s*=\s*e\.message", "", js), (
        "event.message must only ever be assigned via .textContent"
    )
    assert "msg.textContent = e.message" in js
