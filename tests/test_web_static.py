import re
from pathlib import Path

import pytest

STATIC = Path("ship_observer/web/static")


def _rule_body(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"no CSS rule for {selector!r}"
    return match.group(1)


def test_all_static_files_exist():
    for name in ("index.html", "debug.html", "panel.html", "common.js",
                "panel.js", "debug.js", "panel-audit.js", "style.css"):
        assert (STATIC / name).is_file(), f"{name} is missing"


@pytest.mark.parametrize("name", ["index.html", "debug.html", "panel.html"])
def test_html_references_local_assets_only(name):
    """The Pi serves this over the LAN; it must work from a cold cache."""
    html = (STATIC / name).read_text()
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert external == [], f"external assets are not allowed: {external}"


def test_debug_mounts_every_panel_the_spec_requires():
    html = (STATIC / "debug.html").read_text()
    for element_id in ("panel", "live-table", "traffic-summary",
                       "event-log", "config"):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_index_is_the_panel_only_kiosk_page():
    html = (STATIC / "index.html").read_text()
    assert 'id="panel"' in html
    assert 'id="status"' in html
    for absent in ("live-table", "traffic-summary", "event-log", "config"):
        assert f'id="{absent}"' not in html, (
            f"index.html is meant to be panel-only; found #{absent}")


def test_debug_stacks_panel_vessels_and_config_in_a_fixed_column():
    """Panel Mirror, then Vessels in the box, then Configuration, all inside
    the non-shrinking rail; Traffic summary and Events live in the flexible
    area after it."""
    html = (STATIC / "debug.html").read_text()
    rail_start = html.index('class="rail"')
    panel_idx = html.index('id="panel"')
    live_idx = html.index('id="live-table"')
    config_idx = html.index('id="config"')
    fill_start = html.index('class="fill"')
    assert rail_start < panel_idx < live_idx < config_idx < fill_start
    assert html.index('id="traffic-summary"') > fill_start
    assert html.index('id="event-log"') > fill_start


def test_rail_column_never_shrinks():
    """A shrinkable column was the bug: at some viewport widths the grid
    track holding the Panel Mirror sized narrower than the canvas, and the
    card's own overflow-x:auto clipped it left and right. flex-shrink:0 on
    the rail stops that column from ever sizing below its content."""
    css = (STATIC / "style.css").read_text()
    body = _rule_body(css, ".rail")
    assert re.search(r"flex-shrink:\s*0", body), (
        ".rail must set flex-shrink: 0 so the Panel Mirror never compresses")


def test_fill_columns_stay_flexible():
    css = (STATIC / "style.css").read_text()
    body = _rule_body(css, ".fill")
    assert "1fr" in body, ".fill must size its tracks with fr units, not fixed px"


def test_common_js_provides_the_pixel_accurate_frame_renderer():
    js = (STATIC / "common.js").read_text()
    assert "putImageData" in js, "the mirror must be pixel-accurate, not scaled art"
    assert "/ws/frames" in js


@pytest.mark.parametrize("name", ["panel.js", "debug.js"])
def test_page_scripts_reuse_the_shared_frame_connection(name):
    js = (STATIC / name).read_text()
    assert "connectFrames" in js
    assert "drawFrame" in js


def test_debug_js_polls_every_api_endpoint():
    js = (STATIC / "debug.js").read_text()
    for endpoint in ("/api/traffic-summary", "/api/events"):
        assert endpoint in js


def test_debug_js_throttles_the_vessel_table_to_once_per_second():
    """The websocket pushes a state message at WEB_FPS (10/s by default), and
    renderLive()/renderConfig() rebuild their tables via innerHTML - at that
    rate any text selection is destroyed before a copy can complete. The
    rebuild must be gated to roughly once a second, not called straight from
    the websocket callback."""
    js = (STATIC / "debug.js").read_text()
    assert re.search(r"TABLE_REFRESH_MS\s*=\s*1000", js), (
        "expected a 1000ms throttle constant")
    on_state = re.search(r"onState:\s*\(state\)\s*=>\s*\{(.*?)\n\s*\},",
                         js, re.S)
    assert on_state, "debug.js must wire an onState handler"
    assert "renderLive(state)" not in on_state.group(1), (
        "renderLive must be called from behind the throttle, not directly "
        "from the websocket callback")


def test_debug_js_shows_resolved_destinations_as_a_tooltip():
    """A raw US^0TEM>016S destination is unreadable; the server resolves it
    to destination_resolved (a plain-English place name) - debug.js must
    surface that, not just the raw code."""
    js = (STATIC / "debug.js").read_text()
    assert "destination_resolved" in js


def test_panel_html_has_a_button_for_each_audit_view():
    html = (STATIC / "panel.html").read_text()
    for view in ("chars", "icons", "ships"):
        assert f'data-view="{view}"' in html, f"missing a button for {view!r}"
    assert 'id="panel"' in html


def test_panel_audit_js_fetches_the_audit_api_and_draws_with_the_shared_renderer():
    js = (STATIC / "panel-audit.js").read_text()
    assert "/api/panel-audit" in js
    assert "drawFrame" in js


def test_panel_html_has_a_font_toggle_that_starts_hidden():
    """The toggle only makes sense for the Characters view, so the markup
    ships hidden and the script reveals it."""
    html = (STATIC / "panel.html").read_text()
    toggle = re.search(r'<div[^>]*id="font-toggle"[^>]*>', html)
    assert toggle, "missing #font-toggle"
    assert "hidden" in toggle.group(0)
    for font in ("small", "large"):
        assert f'data-font="{font}"' in html, f"missing a button for {font!r}"
    assert "4x6" in html and "6x10" in html


def test_panel_audit_js_asks_for_the_selected_font_and_shows_the_toggle_for_chars():
    js = (STATIC / "panel-audit.js").read_text()
    assert "font=" in js, "the chars request must carry the selected font"
    assert "data-font" in js or "dataset.font" in js
    assert re.search(r'fontToggle\.hidden\s*=\s*view\s*!==\s*"chars"', js), (
        "the toggle must be shown only for the Characters view")


def test_debug_js_never_interpolates_event_fields_into_innerhtml():
    """event.message can carry AIS-broadcast ship-name text. AIS is an open,
    unauthenticated protocol, so that string is attacker-controlled - it must
    be assigned via textContent, never interpolated into an innerHTML string,
    or a malicious ship name becomes a stored XSS payload on this page.
    """
    js = (STATIC / "debug.js").read_text()
    assert "e.message" not in re.sub(r"\.textContent\s*=\s*e\.message", "", js), (
        "event.message must only ever be assigned via .textContent"
    )
    assert "msg.textContent = e.message" in js
