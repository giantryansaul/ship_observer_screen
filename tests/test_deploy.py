import configparser
import stat
from pathlib import Path

DEPLOY = Path("deploy")


def test_service_unit_exists_and_parses():
    parser = configparser.ConfigParser()
    parser.read(DEPLOY / "ship-observer.service")
    assert parser.has_section("Unit")
    assert parser.has_section("Service")
    assert parser.has_section("Install")


def test_service_restarts_forever():
    parser = configparser.ConfigParser()
    parser.read(DEPLOY / "ship-observer.service")
    assert parser["Service"]["Restart"] == "always"
    assert int(parser["Service"]["RestartSec"]) >= 1


def test_service_waits_for_the_network():
    parser = configparser.ConfigParser()
    parser.read(DEPLOY / "ship-observer.service")
    assert "network-online.target" in parser["Unit"]["After"]
    assert "network-online.target" in parser["Unit"]["Wants"]


def test_service_loads_the_env_file_and_runs_the_module():
    parser = configparser.ConfigParser()
    parser.read(DEPLOY / "ship-observer.service")
    assert ".env" in parser["Service"]["EnvironmentFile"]
    assert "-m ship_observer" in parser["Service"]["ExecStart"]


def test_install_script_is_executable_and_strict():
    script = DEPLOY / "install.sh"
    assert script.is_file()
    assert script.stat().st_mode & stat.S_IXUSR, "install.sh must be executable"
    body = script.read_text()
    assert body.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in body


def test_install_script_covers_every_provisioning_step():
    body = (DEPLOY / "install.sh").read_text()
    for needle in ("snd_bcm2835", "isolcpus", "rpi-rgb-led-matrix",
                   "/var/lib/ship-observer", "pyenv", "systemctl enable"):
        assert needle in body, f"install.sh never mentions {needle}"


def test_install_script_does_not_hardcode_a_secret():
    body = (DEPLOY / "install.sh").read_text()
    assert "AIS_STREAM_API_KEY=" not in body.replace("AIS_STREAM_API_KEY=your", "")
