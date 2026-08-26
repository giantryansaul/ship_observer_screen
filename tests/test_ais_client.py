import asyncio
import json
from datetime import datetime, timezone

import pytest

from ship_observer.ais_client import AisClient, build_subscription
from ship_observer.config import Settings

MINIMAL = {
    "AIS_STREAM_API_KEY": "test-key",
    "BBOX": "-122.527428,47.859476,-122.323322,47.910359",
}

POSITION_JSON = json.dumps({
    "MessageType": "PositionReport",
    "MetaData": {"MMSI": 366123456, "ShipName": "TEST SHIP",
                 "latitude": 47.88, "longitude": -122.41,
                 "time_utc": "2026-08-26 17:04:11.000000000 +0000 UTC"},
    "Message": {"PositionReport": {"UserID": 366123456, "Sog": 10.0}},
})


class FakeSocket:
    """Yields queued frames, then raises `error` (or ends the stream)."""

    def __init__(self, frames, error=None):
        self._frames = list(frames)
        self._error = error
        self.sent: list[str] = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False


def make_connect(sockets):
    """Returns a connect() that hands out each socket in turn."""
    queue = list(sockets)
    attempts = []

    def connect(url, **kwargs):
        attempts.append(url)
        return queue.pop(0) if queue else FakeSocket([])

    connect.attempts = attempts
    return connect


def test_build_subscription_shape():
    s = Settings.from_env(MINIMAL)
    sub = build_subscription(s)
    assert sub["APIKey"] == "test-key"
    assert sub["FilterMessageTypes"] == ["PositionReport", "ShipStaticData"]
    # AISStream requires latitude-first corner pairs.
    assert sub["BoundingBoxes"] == [[[47.859476, -122.527428],
                                     [47.910359, -122.323322]]]
    assert set(sub) == {"APIKey", "BoundingBoxes", "FilterMessageTypes"}


async def test_subscription_is_sent_immediately_on_connect():
    s = Settings.from_env(MINIMAL)
    sock = FakeSocket([POSITION_JSON])
    client = AisClient(s, connect=make_connect([sock]))

    messages = [m async for m in _take(client.stream(), 1)]

    assert len(sock.sent) == 1
    assert json.loads(sock.sent[0])["APIKey"] == "test-key"
    assert messages[0].mmsi == 366123456


async def test_malformed_frames_are_skipped_without_killing_the_stream():
    s = Settings.from_env(MINIMAL)
    sock = FakeSocket(["not json", json.dumps({"MessageType": "Nope"}), POSITION_JSON])
    client = AisClient(s, connect=make_connect([sock]))

    messages = [m async for m in _take(client.stream(), 1)]

    assert len(messages) == 1
    assert messages[0].mmsi == 366123456


async def test_reconnects_after_a_drop_and_resubscribes():
    s = Settings.from_env(MINIMAL)
    first = FakeSocket([POSITION_JSON], error=ConnectionError("dropped"))
    second = FakeSocket([POSITION_JSON])
    events = []
    client = AisClient(
        s,
        on_event=lambda *a: events.append(a),
        connect=make_connect([first, second]),
        backoff_base=0.0,
        backoff_max=0.0,
    )

    messages = [m async for m in _take(client.stream(), 2)]

    assert len(messages) == 2
    assert len(second.sent) == 1, "must resubscribe on the new connection"
    categories = {e[1] for e in events}
    assert categories == {"ws"}
    assert any("disconnect" in e[2].lower() for e in events)


async def test_connected_reflects_stream_lifecycle():
    s = Settings.from_env(MINIMAL)
    sock = FakeSocket([POSITION_JSON], error=ConnectionError("dropped"))
    # A nonzero backoff is required here, unlike the 0.0/0.0 used in
    # test_reconnects_after_a_drop_and_resubscribes above: with a falsy delay,
    # stream()'s `if delay: await asyncio.sleep(delay)` is skipped entirely,
    # so a failed connect loops straight into reconnecting against a further
    # empty FakeSocket with no `await` that actually suspends - a synchronous
    # busy loop with no checkpoint for `asyncio.sleep(0)` below to hand
    # control back after. A tiny positive backoff gives stream() a real
    # suspension point (the reconnect sleep) to land on.
    client = AisClient(s, connect=make_connect([sock]), backoff_base=0.01, backoff_max=0.01)

    assert client.connected is False

    agen = client.stream()
    await agen.__anext__()
    assert client.connected is True

    # The FakeSocket raises ConnectionError after its one queued frame. Pump
    # the generator once more so stream() reaches the except branch, sets
    # connected back to False, and suspends inside the reconnect sleep.
    task = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0)
    assert client.connected is False

    # Cancel and let the cancellation actually land before closing the
    # generator - closing while a prior asend() is still mid-flight raises
    # "asynchronous generator is already running".
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await agen.aclose()


async def test_backoff_grows_then_resets_after_a_successful_message():
    s = Settings.from_env(MINIMAL)
    client = AisClient(s, connect=make_connect([]), backoff_base=1.0, backoff_max=60.0)
    assert client._next_backoff() == pytest.approx(1.0, rel=0.5)
    assert client._next_backoff() == pytest.approx(2.0, rel=0.5)
    assert client._next_backoff() == pytest.approx(4.0, rel=0.5)
    client._reset_backoff()
    assert client._next_backoff() == pytest.approx(1.0, rel=0.5)


async def test_backoff_is_capped():
    s = Settings.from_env(MINIMAL)
    client = AisClient(s, connect=make_connect([]), backoff_base=1.0, backoff_max=5.0)
    for _ in range(20):
        delay = client._next_backoff()
    assert delay <= 5.0


async def test_last_message_at_tracks_the_newest_message():
    s = Settings.from_env(MINIMAL)
    client = AisClient(s, connect=make_connect([FakeSocket([POSITION_JSON])]))
    assert client.last_message_at is None
    async for _ in _take(client.stream(), 1):
        pass
    assert client.last_message_at == datetime(
        2026, 8, 26, 17, 4, 11, tzinfo=timezone.utc
    )


async def _take(agen, n):
    """Yield the first n items, then close the generator."""
    count = 0
    async for item in agen:
        yield item
        count += 1
        if count >= n:
            break
    await agen.aclose()
