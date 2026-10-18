"""Connection bounds and the request-head reader (ADR-0017), over raw sockets.

At most `max_connections` connections are served; one more gets a fixed 503 and is closed at
once. Each reads one head of at most 16 KiB (request line 8 KiB, 50 header lines, no body) within
`head_seconds` of being accepted; anything else gets a short JSON error with the security headers
and a status line, or, for an incomplete head, a silent close.
"""

import json
import socket
import socketserver
import struct
import time
from collections.abc import Callable
from typing import Any

import pytest

from matcheyes.api.server import CSP, MAX_HEAD, MAX_REQUEST_LINE, REJECT_503, BroadcastServer
from tests.api.support import app, running

HEALTH = b"GET /health HTTP/1.0\r\n"
RESET = struct.pack("ii", 1, 0)
"""SO_LINGER on with a zero timeout: closing sends RST."""
Address = tuple[str, int]


def _connect(address: Address) -> socket.socket:
    return socket.create_connection(address, timeout=15)


def _read_all(sock: Any) -> bytes:
    chunks = []
    while True:
        try:
            chunk = sock.recv(65536)
        except (ConnectionResetError, ConnectionAbortedError):
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def exchange(address: Address, *parts: bytes, pause: float = 0.0) -> bytes:
    with _connect(address) as sock:
        for part in parts:
            sock.sendall(part)
            if pause:
                time.sleep(pause)
        return _read_all(sock)


def parsed(raw: bytes) -> tuple[str, int, dict[str, str], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    version, status = lines[0].split(" ")[:2]
    headers = dict(line.split(": ", 1) for line in lines[1:])
    return version, int(status), headers, body


def _until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.01)


def _error(raw: bytes, status: int, message: str) -> None:
    version, got, headers, body = parsed(raw)
    assert (version, got) == ("HTTP/1.0", status), raw[:200]
    assert json.loads(body) == {"error": message}
    assert headers["Content-Type"] == "application/json"
    assert headers["Content-Security-Policy"] == CSP and headers["Cache-Control"] == "no-store"
    assert headers["Connection"] == "close"


@pytest.fixture(scope="module")
def served() -> Any:
    with running(app()) as (server, address):
        yield server, address


@pytest.fixture
def address(served: tuple[BroadcastServer, Address]) -> Any:
    server, addr = served
    yield addr
    _until(lambda: server.connections.active == 0)


@pytest.mark.parametrize(
    ("eol", "blank"),
    [(b"\r\n", b"\r\n"), (b"\n", b"\n"), (b"\r\n", b"\n"), (b"\n", b"\r\n")],
    ids=["crlf-crlf", "lf-lf", "crlf-lf", "lf-crlf"],
)
def test_every_standard_head_terminator_is_accepted(
    address: Address, eol: bytes, blank: bytes
) -> None:
    raw = exchange(address, b"GET /health HTTP/1.1" + eol + b"Host: x" + eol + blank)
    assert parsed(raw)[1] == 200
    assert parsed(exchange(address, b"GET /health HTTP/1.0" + eol + blank))[1] == 200


def test_a_head_split_into_single_bytes_parses_the_same(address: Address) -> None:
    head = HEALTH + b"Host: x\r\nAccept: */*\r\n\r\n"
    raw = exchange(address, *(head[i : i + 1] for i in range(len(head))), pause=0.002)
    assert parsed(raw)[1] == 200


@pytest.mark.parametrize(
    "line",
    [
        b"Host x\r\n",
        b"Host: x\r\n continued\r\n",
        b"Host : x\r\n",
        b"Host: a\rb\r\n",
        b"X: \x00\r\n",
    ],
    ids=["no-colon", "obs-fold", "space-before-colon", "bare-cr", "nul"],
)
def test_malformed_header_lines_are_refused(address: Address, line: bytes) -> None:
    _error(exchange(address, HEALTH + line + b"\r\n"), 400, "bad request")


@pytest.mark.parametrize(
    "extra",
    [
        b"Content-Length: 0\r\n\r\n",
        b"content-length: 5\r\n\r\nhello",
        b"Transfer-Encoding: chunked\r\n\r\n",
    ],
)
def test_any_body_framing_is_refused(address: Address, extra: bytes) -> None:
    _error(exchange(address, HEALTH + extra), 400, "bad request")


def test_bytes_after_the_head_are_refused(address: Address) -> None:
    _error(exchange(address, HEALTH + b"\r\nGET / HTTP/1.0\r\n\r\n"), 400, "bad request")


@pytest.mark.parametrize(
    ("line", "status", "message"),
    [
        (b"GET /\r\n", 400, "bad request"),
        (b"GET /health HTTP/1.2\r\n", 505, "HTTP version not supported"),
        (b"GET /health HTTP/2.0\r\n", 505, "HTTP version not supported"),
        (b"GET /health HTTP/x\r\n", 400, "bad request"),
        (b"GET http://example.invalid/health HTTP/1.1\r\n", 400, "bad request"),
        (b"\x16\x03\x01\x02\x00\x01\r\n", 400, "bad request"),
        (b"TRACE /health HTTP/1.1\r\n", 405, "read-only API"),
        (b"FOO /health HTTP/1.0\r\n", 405, "read-only API"),
    ],
    ids=["http-0.9", "1.2", "2.0", "bad-version", "absolute-form", "garbage", "trace", "foo"],
)
def test_bad_request_lines_get_a_status_line_and_json(
    address: Address, line: bytes, status: int, message: str
) -> None:
    raw = exchange(address, line + b"\r\n")
    version, got, headers, body = parsed(raw)
    assert (version, got, json.loads(body)) == ("HTTP/1.0", status, {"error": message})
    assert headers["Content-Security-Policy"] == CSP


def test_the_request_line_limit_is_8_kib_including_its_terminator(address: Address) -> None:
    def line(length: int) -> bytes:
        prefix, suffix = b"GET /", b" HTTP/1.0\r\n"
        return prefix + b"a" * (length - len(prefix) - len(suffix)) + suffix

    assert len(line(MAX_REQUEST_LINE)) == MAX_REQUEST_LINE
    raw = exchange(address, line(MAX_REQUEST_LINE) + b"\r\n")
    assert json.loads(parsed(raw)[3]) == {"error": "path too long"}
    longer = line(MAX_REQUEST_LINE + 1)[:MAX_REQUEST_LINE]
    assert b"\n" not in longer
    _error(exchange(address, longer), 414, "request line too long")


def test_fifty_header_lines_are_accepted_and_fifty_one_are_not(address: Address) -> None:
    headers = b"".join(b"X-H%d: v\r\n" % i for i in range(51))
    fifty = headers[: headers.index(b"X-H50")]
    assert parsed(exchange(address, HEALTH + fifty + b"\r\n"))[1] == 200
    _error(exchange(address, HEALTH + headers), 431, "request head too large")


def test_the_head_limit_is_16_kib_including_its_terminator(address: Address) -> None:
    def head(total: int) -> bytes:
        start, end = HEALTH + b"X-Pad: ", b"\r\n\r\n"
        return start + b"p" * (total - len(start) - len(end)) + end

    assert len(head(MAX_HEAD)) == MAX_HEAD
    assert parsed(exchange(address, head(MAX_HEAD)))[1] == 200
    _error(exchange(address, head(MAX_HEAD + 1)[: MAX_HEAD + 1]), 431, "request head too large")
    unterminated = head(MAX_HEAD + 4)[: MAX_HEAD + 1]
    assert b"\r\n\r\n" not in unterminated
    _error(exchange(address, unterminated), 431, "request head too large")


def test_an_incomplete_head_is_closed_silently(
    served: tuple[BroadcastServer, Address], address: Address
) -> None:
    server = served[0]
    with _connect(address) as sock:
        sock.sendall(b"GET /health HTTP/1.0\r\nHost:")
        sock.shutdown(socket.SHUT_WR)
        assert _read_all(sock) == b""
    with _connect(address) as sock:
        sock.sendall(b"GET /health")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET)
    _until(lambda: server.connections.active == 0)


def test_a_slow_head_is_closed_at_its_deadline_however_it_trickles() -> None:
    with running(app(), head_seconds=1.0) as (server, address):
        for trickle in (None, 0.2):
            with _connect(address) as sock:
                began = time.monotonic()
                sock.settimeout(0.25 if trickle else 5)
                closed = None
                while closed is None and time.monotonic() - began < 5:
                    try:
                        closed = sock.recv(1024)
                    except TimeoutError:
                        try:
                            sock.sendall(b"G")
                        except OSError:
                            closed = b""
                    except OSError:
                        closed = b""
                elapsed = time.monotonic() - began
            assert closed == b"", trickle
            assert 0.8 <= elapsed <= 1.8, (trickle, elapsed)
        _until(lambda: server.connections.active == 0)


def test_a_connection_over_the_cap_gets_a_fixed_503_without_blocking_others(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with running(app(), max_connections=3, head_seconds=5.0) as (server, address):
        idle = [_connect(address) for _ in range(3)]
        try:
            _until(lambda: server.connections.active == 3)
            began = time.monotonic()
            with _connect(address) as extra:
                assert _read_all(extra) == REJECT_503
            assert time.monotonic() - began < 1.0
            assert server.connections.active == 3
            idle.pop().close()
            _until(lambda: server.connections.active == 2)
            assert parsed(exchange(address, HEALTH + b"\r\n"))[1] == 200
        finally:
            for sock in idle:
                sock.close()
        _until(lambda: server.connections.active == 0)
        assert server.rejected == 1
        server._reported = time.monotonic() - 60
        _until(lambda: server.rejected == 0)
    assert "rejected 1 connections (cap 3)" in capsys.readouterr().err
    _, status, headers, body = parsed(REJECT_503)
    assert (status, json.loads(body)) == (503, {"error": "server busy"})
    assert headers["Content-Security-Policy"] == CSP and headers["Connection"] == "close"


def test_a_thread_that_cannot_start_releases_its_slot_and_closes_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def cannot_start(self: Any, request: Any, client_address: Any) -> None:
        raise RuntimeError("can't start new thread")

    broadcast = app()
    server = BroadcastServer(("127.0.0.1", 0), broadcast)
    try:
        client = _connect(server.server_address[:2])
        request, peer = server.get_request()
        monkeypatch.setattr(socketserver.ThreadingMixIn, "process_request", cannot_start)
        server.process_request(request, peer)
        assert request.fileno() == -1
        assert (server.connections.active, server.rejected, server._accepted) == (0, 1, {})
        assert _read_all(client) == b""
        client.close()
    finally:
        monkeypatch.undo()
        server.server_close()
    assert capsys.readouterr().err == ""


def test_slots_return_to_zero_after_a_thousand_mixed_connections() -> None:
    kinds = [
        HEALTH + b"\r\n",
        b"POST /health HTTP/1.0\r\n\r\n",
        b"GET /\r\n\r\n",
        b"GET /health HTTP/1.0\r\nHost x\r\n\r\n",
        b"",
        b"GET /hea",
    ]
    with running(app(), head_seconds=2.0) as (server, address):
        for i in range(1000):
            with _connect(address) as sock:
                if kinds[i % 6]:
                    sock.sendall(kinds[i % 6])
                if i % 6 == 5:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET)
                elif i % 6 != 4:
                    _read_all(sock)
        assert server.connections.wait_idle(time.monotonic() + 10)
        assert server.app.streams.active == 0 and server._accepted == {}
