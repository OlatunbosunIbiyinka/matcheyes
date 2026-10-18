"""Stream slots (ADR-0017): at most 32 streams; a slot is held only by its own handler and freed
promptly when the peer goes away, the replay fails or the server stops."""

import json
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from matcheyes.api.live import LiveMatch
from matcheyes.api.server import MAX_STREAMS, Slots
from tests.api.support import app, request, running, wait_done
from tests.lifecycle.support import in_progress

MATCH = in_progress().info.match_id
STREAM = f"GET /matches/{MATCH}/stream?audience=fan HTTP/1.0\r\n\r\n".encode()
RESET = struct.pack("ii", 1, 0)


def _until(predicate: Callable[[], bool], timeout: float = 10.0) -> float:
    began = time.monotonic()
    while not predicate():
        assert time.monotonic() - began < timeout, "condition not reached"
        time.sleep(0.01)
    return time.monotonic() - began


def _open_stream(address: tuple[str, int]) -> socket.socket:
    sock = socket.create_connection(address, timeout=15)
    sock.sendall(STREAM)
    seen = b""
    while b"retry: 3000" not in seen:
        chunk = sock.recv(65536)
        assert chunk, seen
        seen += chunk
    assert seen.startswith(b"HTTP/1.0 200 ")
    return sock


@pytest.fixture
def paused() -> Any:
    """A served match whose first edition has finished and whose loop now pauses for a minute,
    so streams stay open and idle."""
    broadcast = app(loop=True, pause=60)
    with running(broadcast) as (server, address):
        broadcast.live[MATCH].start()
        wait_done(broadcast.live[MATCH])
        yield broadcast, server, address


def test_slots_count_refuse_over_the_limit_and_never_over_release() -> None:
    slots = Slots(2)
    assert slots.try_acquire() and slots.try_acquire() and not slots.try_acquire()
    assert slots.active == 2 and not slots.wait_idle(time.monotonic() + 0.05)
    slots.release()
    slots.release()
    assert slots.active == 0 and slots.wait_idle(time.monotonic())
    with pytest.raises(ValueError, match="released more often"):
        slots.release()


def test_thirty_two_streams_are_served_and_the_thirty_third_is_refused(paused: Any) -> None:
    broadcast, _, address = paused
    assert MAX_STREAMS == 32 and broadcast.streams.limit == 32
    streams = [_open_stream(address) for _ in range(32)]
    try:
        assert broadcast.streams.active == 32
        status, headers, body = request(address, f"/matches/{MATCH}/stream")
        assert (status, json.loads(body)) == (503, {"error": "too many streams"})
        assert headers["Content-Type"] == "application/json"
    finally:
        for sock in streams:
            sock.close()
    assert _until(lambda: broadcast.streams.active == 0) <= 2.5


@pytest.mark.parametrize("how", ["fin", "rst"])
def test_a_departed_peer_frees_its_slot_within_two_seconds(paused: Any, how: str) -> None:
    broadcast, _, address = paused
    sock = _open_stream(address)
    assert broadcast.streams.active == 1
    if how == "rst":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET)
    else:
        sock.shutdown(socket.SHUT_WR)
    sock.close()
    assert _until(lambda: broadcast.streams.active == 0) <= 2.0


def test_simultaneous_disconnects_and_a_reconnect_burst_balance_the_slots(
    paused: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    broadcast, server, address = paused
    streams = [_open_stream(address) for _ in range(32)]
    for sock in streams:
        sock.close()
    assert _until(lambda: broadcast.streams.active == 0) <= 2.0
    for _ in range(2):
        burst = [_open_stream(address) for _ in range(32)]
        assert broadcast.streams.active == 32
        for sock in burst:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET)
            sock.close()
        _until(lambda: broadcast.streams.active == 0)
    _until(lambda: server.connections.active == 0)
    assert "internal error" not in capsys.readouterr().err


def test_bytes_sent_after_the_stream_started_close_it(paused: Any) -> None:
    broadcast, _, address = paused
    sock = _open_stream(address)
    sock.sendall(b"unexpected")
    assert _until(lambda: broadcast.streams.active == 0) <= 2.0
    sock.close()


def test_an_error_mid_stream_writes_nothing_into_the_stream(
    paused: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broadcast, _, address = paused
    called = threading.Event()

    def broken(self: LiveMatch) -> bool:
        called.set()
        raise RuntimeError("secret internals")

    monkeypatch.setattr(LiveMatch, "check_progress", broken)
    sock = _open_stream(address)
    sock.settimeout(15)
    rest = b""
    while chunk := sock.recv(65536):
        rest += chunk
    sock.close()
    assert called.is_set()
    assert b"error" not in rest and b"secret" not in rest and b"HTTP/" not in rest
    assert broadcast.streams.active == 0
    assert "internal error on /matches/" in capsys.readouterr().err


def test_shutdown_ends_open_streams_and_frees_their_slots() -> None:
    broadcast = app(loop=True, pause=60)
    with running(broadcast) as (server, address):
        broadcast.live[MATCH].start()
        wait_done(broadcast.live[MATCH])
        streams = [_open_stream(address) for _ in range(4)]
        server.shutdown()
        began = time.monotonic()
        assert server.close_gracefully(time.monotonic() + 10)
        assert time.monotonic() - began < 3.0
        for sock in streams:
            sock.settimeout(5)
            while sock.recv(65536):
                pass
            sock.close()
        assert broadcast.streams.active == 0 and server.connections.active == 0
