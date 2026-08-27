import logging

from ship_observer.config import Settings


def test_websockets_logger_is_never_below_info(monkeypatch, capsys, tmp_path):
    """Root-level DEBUG must never let the websockets library log a frame
    payload - that payload contains the raw API key. This test exercises
    the same logging.getLogger("websockets").setLevel(...) call __main__.py
    makes, and confirms a DEBUG-level websockets log call is suppressed
    after it runs.
    """
    settings = Settings.from_env({
        "AIS_STREAM_API_KEY": "TEST-MAIN-CANARY-1234",
        "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
        "LOG_LEVEL": "DEBUG",
    })
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("websockets").setLevel(logging.INFO)

    logging.getLogger("websockets.client").debug(
        "> TEXT '{\"APIKey\": \"%s\"}'", settings.ais_stream_api_key
    )

    captured = capsys.readouterr()
    assert "TEST-MAIN-CANARY-1234" not in captured.out
    assert "TEST-MAIN-CANARY-1234" not in captured.err
