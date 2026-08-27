import re
from pathlib import Path

DOC = Path(__file__).parent.parent / "docs/raspberry-pi-setup.md"


def test_the_runbook_exists():
    assert DOC.is_file()


def test_runbook_covers_every_required_section():
    body = DOC.read_text().lower()
    for topic in [
        "raspberry pi imager", "ssh", "power", "wiring",
        "snd_bcm2835", "isolcpus", "pyenv", "rpi-rgb-led-matrix",
        "gpio", "/var/lib/ship-observer", ".env",
        "systemctl", "verification", "troubleshooting",
    ]:
        assert topic in body, f"the runbook never covers {topic}"


def test_runbook_warns_about_panel_power():
    """A 64x64 panel can pull ~4 A. Powering it from the Pi destroys the Pi."""
    body = DOC.read_text().lower()
    assert re.search(r"\b4\s*a\b|\bamp", body)
    assert "separate" in body


def test_runbook_documents_the_env_variables_that_must_be_set():
    body = DOC.read_text()
    assert "AIS_STREAM_API_KEY" in body
    assert "BBOX" in body


def test_runbook_verification_is_layered():
    body = DOC.read_text().lower()
    for check in ("demo", "systemctl status", "/healthz"):
        assert check in body, f"verification never uses {check}"


def test_troubleshooting_covers_the_real_failures():
    body = DOC.read_text().lower()
    for symptom in ("flicker", "blank", "hardware-mapping", "bbox"):
        assert symptom in body
