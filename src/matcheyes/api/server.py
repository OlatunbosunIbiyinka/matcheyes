"""Read-only HTTP API and Server-Sent Events over the Python standard library.

    GET /                                   the static web surface
    GET /static/app.js, /static/app.css     its only assets
    GET /health                             readiness: 503 while any match cannot be served
    GET /matches                            the allow-listed matches (teams, venue)
    GET /matches/{id}/timeline?audience=&club=   the canonical cues published so far
    GET /matches/{id}/stream?audience=&club=     the same cues, live, as Server-Sent Events
    GET /matches/{id}/storylines/{sid}/revisions/{n}   the work behind one published revision

There is no write, ingest, truth or debug endpoint: every other method is refused (405). Match
IDs must be in the allow-list, `audience` is fan or broadcaster, `club` is empty or one of the
two clubs. Errors are short JSON messages, never stack traces. Every response carries a strict
Content Security Policy. Each match is replayed once, by its `LiveMatch`, however many viewers
connect.

Bounds (ADR-0017): at most 64 connections, each reading one request head of at most 16 KiB
(request line 8 KiB, 50 header lines, no body) within 10 s of being accepted, then answered once
and closed; a connection over the cap gets a fixed 503 and is closed without blocking the accept
loop. At most 32 of them stream. Stream message IDs are `{run}:{edition}:{index}`; a
`Last-Event-ID` from another run, or one that cannot be genuine, gets 204 so the client rebuilds.
"""

import json
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from matcheyes.api.live import (
    FAILED,
    STALL_SECONDS,
    Backend,
    Evaluator,
    LiveMatch,
    Message,
    SurfaceKey,
)
from matcheyes.broadcast.contracts import BROADCAST_VERSION
from matcheyes.domain.match import ObservableMatch
from matcheyes.lifecycle.explain import work_rows
from matcheyes.personalization.contracts import Audience

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
MAX_STREAMS = 32
MAX_CONNECTIONS = 64
HEARTBEAT_SECONDS = 15.0
SLICE_SECONDS = 1.0
"""A stream re-checks failure, shutdown, stalls and its peer at least this often."""
HEAD_SECONDS = 10.0
MAX_HEAD = 16384
MAX_REQUEST_LINE = 8192
"""Including its line terminator."""
MAX_HEADERS = 50
READINESS_SECONDS = 1.0
SHUTDOWN_SECONDS = 10.0
REJECT_REPORT_SECONDS = 10.0
SHUT_RDWR = 2
MAX_PATH = 256
MAX_RESUME = 64
QUERY_KEYS = frozenset({"audience", "club"})
STORYLINE_ID = re.compile(r"sl-[A-Za-z0-9_.-]{1,157}")
REVISION = re.compile(r"[1-9][0-9]{0,3}")
RESUME = re.compile(r"([0-9a-f]{32}):(0|[1-9][0-9]{0,8}):(start|0|[1-9][0-9]{0,8})")
TOKEN = rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
REQUEST_LINE = re.compile(TOKEN + rb" ([\x21-\x7e]+) HTTP/([0-9])\.([0-9])\r?\n")
HEADER_LINE = re.compile(TOKEN + rb":[\t\x20-\x7e\x80-\xff]*\r?\n")
BODY_HEADERS = (b"content-length", b"transfer-encoding")
ERRORS = {
    HTTPStatus.BAD_REQUEST: "bad request",
    HTTPStatus.METHOD_NOT_ALLOWED: "read-only API",
    HTTPStatus.REQUEST_URI_TOO_LONG: "request line too long",
    HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE: "request head too large",
    HTTPStatus.HTTP_VERSION_NOT_SUPPORTED: "HTTP version not supported",
}
REJECT_BODY = b'{"error":"server busy"}'
REJECT_503 = (
    "HTTP/1.0 503 Service Unavailable\r\n"
    "Content-Type: application/json\r\n"
    "Cache-Control: no-store\r\n"
    + "".join(f"{name}: {value}\r\n" for name, value in SECURITY_HEADERS.items())
    + f"Content-Length: {len(REJECT_BODY)}\r\nConnection: close\r\n\r\n"
).encode("ascii") + REJECT_BODY


class ClientError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ShutdownRequested(BaseException):
    """Raised at the accept loop's safe point once a shutdown signal has been recorded."""


class Slots:
    """A non-blocking counting limit that can be waited on until nothing holds a slot."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._held = 0
        self._condition = threading.Condition()

    def try_acquire(self) -> bool:
        with self._condition:
            if self._held >= self.limit:
                return False
            self._held += 1
            return True

    def release(self) -> None:
        with self._condition:
            if self._held <= 0:
                raise ValueError("slot released more often than acquired")
            self._held -= 1
            self._condition.notify_all()

    @property
    def active(self) -> int:
        with self._condition:
            return self._held

    def wait_idle(self, deadline: float) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: self._held == 0, timeout=max(0.0, deadline - time.monotonic())
            )


class BroadcastApp:
    def __init__(
        self,
        catalog: dict[str, ObservableMatch],
        speed: float,
        loop: bool = True,
        pause: float = 20.0,
        max_streams: int = MAX_STREAMS,
        evaluator: Evaluator | None = None,
        backends: Mapping[str, Backend] | None = None,
        stall_seconds: float = STALL_SECONDS,
    ) -> None:
        """`backends` overrides, per match, the shared `evaluator` and adds reasoner identity and
        an explainer; a match without one is reasoned by the reference reasoner."""
        self.catalog = catalog
        backends = backends or {}
        self.live = {}
        for mid, m in catalog.items():
            b = backends.get(mid, Backend(evaluator))
            self.live[mid] = LiveMatch(
                m,
                speed,
                loop,
                pause,
                b.evaluator or evaluator,
                b.reasoner,
                b.explainer,
                stall_seconds=stall_seconds,
                available=b.available,
            )
        self.static = {
            route: ((STATIC_DIR / name).read_bytes(), ctype)
            for route, (name, ctype) in STATIC_FILES.items()
        }
        self.streams = Slots(max_streams)
        self.stopping = False

    def begin_stop(self) -> None:
        """From now on readiness is `stopping` and replays are no longer served."""
        self.stopping = True

    def readiness(self) -> str | None:
        """Why the surface is not ready, or None. Never starts a replay; all matches share one
        deadline, so a wedged match cannot hold the probe."""
        if self.stopping:
            return "stopping"
        deadline = time.monotonic() + READINESS_SECONDS
        for live in self.live.values():
            reason = live.health(deadline)
            if reason is not None:
                return reason
        return None

    def available(self, live: LiveMatch) -> None:
        if self.stopping or not live.serving:
            raise ClientError(HTTPStatus.SERVICE_UNAVAILABLE, "replay unavailable")

    def matches(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for mid, match in self.catalog.items():
            info = match.info
            out.append(
                {
                    "match_id": mid,
                    "competition": info.competition,
                    "matchday": info.matchday,
                    "venue": info.venue,
                    "synthetic": info.synthetic,
                    "home": _team(
                        info.home.team_id, info.home.club.name, info.home.club.short_name
                    ),
                    "away": _team(
                        info.away.team_id, info.away.club.name, info.away.club.short_name
                    ),
                    "reasoner": self.live[mid].reasoner.model_dump(mode="json"),
                }
            )
        return out

    def explain(self, match_id: str, storyline_id: str, number: str) -> bytes:
        live = self.live.get(match_id)
        if live is None:
            raise ClientError(HTTPStatus.NOT_FOUND, "unknown match")
        if not STORYLINE_ID.fullmatch(storyline_id) or not REVISION.fullmatch(number):
            raise ClientError(HTTPStatus.BAD_REQUEST, "invalid storyline or revision")
        live.start()
        self.available(live)
        found = live.explain(storyline_id, int(number))
        if found is None:
            raise ClientError(HTTPStatus.NOT_FOUND, "revision not published")
        edition, explanation = found
        rows = ",".join(row.model_dump_json() for row in work_rows(explanation))
        return (
            f'{{"edition":{edition},"run":"{live.run}",'
            f'"explanation":{explanation.model_dump_json()},"rows":[{rows}]}}'
        ).encode()

    def resolve(self, match_id: str, query: str) -> tuple[LiveMatch, SurfaceKey]:
        live = self.live.get(match_id)
        if live is None:
            raise ClientError(HTTPStatus.NOT_FOUND, "unknown match")
        params = parse_qs(query, keep_blank_values=True, strict_parsing=False, max_num_fields=4)
        if not set(params) <= QUERY_KEYS or any(len(v) != 1 for v in params.values()):
            raise ClientError(HTTPStatus.BAD_REQUEST, "unsupported query")
        try:
            audience = Audience(params.get("audience", ["fan"])[0])
        except ValueError:
            raise ClientError(HTTPStatus.BAD_REQUEST, "invalid audience") from None
        club = params.get("club", [""])[0] or None
        try:
            key = live.surface(audience, club)
        except KeyError:
            raise ClientError(HTTPStatus.BAD_REQUEST, "invalid audience or club") from None
        live.start()
        return live, key

    def close(self) -> None:
        for live in self.live.values():
            live.stop()


def _team(team_id: str, name: str, short: str) -> dict[str, str]:
    return {"team_id": team_id, "name": name, "short_name": short}


class Handler(BaseHTTPRequestHandler):
    server_version = "MatchEyes"
    sys_version = ""
    timeout = 30
    app: BroadcastApp

    def setup(self) -> None:
        super().setup()
        self.rfile.close()
        self.requestline, self.request_version, self.command = "", "HTTP/1.0", ""
        self.streaming = False

    def handle(self) -> None:
        self.close_connection = True
        self.handle_one_request()

    def handle_one_request(self) -> None:
        """Read exactly one bounded request head, parse it with the standard parser, answer it
        once. An incomplete head (EOF, reset, deadline) is closed without a response."""
        head = self._read_head()
        if head is None:
            return
        self.raw_requestline, headers = head
        self.rfile = BytesIO(headers)
        if not self.parse_request():
            return
        if self.rfile.read():
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        try:
            if self.command == "GET":
                self.do_GET()
            else:
                self._refuse()
            self.wfile.flush()
        except (ConnectionError, TimeoutError):
            return

    def _read_head(self) -> tuple[bytes, bytes] | None:
        """The request line and the header lines plus terminator, or None once an error has
        been sent or the connection is to be closed silently."""
        connection = self.connection
        server = self.server
        if isinstance(server, BroadcastServer):
            deadline = server.accepted_at(connection) + server.head_seconds
        else:
            deadline = time.monotonic() + HEAD_SECONDS
        buf = b""
        start = 0
        line_end = 0
        headers = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                connection.settimeout(remaining)
                chunk = connection.recv(min(4096, MAX_HEAD + 1 - len(buf)))
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
            status = None
            while status is None and (newline := buf.find(b"\n", start)) >= 0:
                line = buf[start : newline + 1]
                start = newline + 1
                if not line_end:
                    line_end = start
                    status = self._check_request_line(line)
                elif line in (b"\r\n", b"\n"):
                    if start > MAX_HEAD:
                        status = HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE
                    elif buf[start:]:
                        status = HTTPStatus.BAD_REQUEST
                    else:
                        return buf[:line_end], buf[line_end:start]
                else:
                    headers += 1
                    status = _check_header(line, headers)
            if status is None and not line_end and len(buf) >= MAX_REQUEST_LINE:
                status = HTTPStatus.REQUEST_URI_TOO_LONG
            if status is None and len(buf) > MAX_HEAD:
                status = HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE
            if status is not None:
                self._head_error(status)
                return None

    def _check_request_line(self, line: bytes) -> HTTPStatus | None:
        self.requestline = line.decode("latin-1").rstrip("\r\n")
        if len(line) > MAX_REQUEST_LINE:
            return HTTPStatus.REQUEST_URI_TOO_LONG
        found = REQUEST_LINE.fullmatch(line)
        if len(line.split()) == 2 or found is None:
            return HTTPStatus.BAD_REQUEST
        if (found[2], found[3]) not in ((b"1", b"0"), (b"1", b"1")):
            return HTTPStatus.HTTP_VERSION_NOT_SUPPORTED
        if not found[1].startswith(b"/"):
            return HTTPStatus.BAD_REQUEST
        return None

    def _head_error(self, status: HTTPStatus) -> None:
        try:
            self.connection.settimeout(self.timeout)
            self.send_error(status)
        except OSError:
            pass

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """A fixed short JSON error with the security headers; never echoes the request."""
        if self.request_version not in ("HTTP/1.0", "HTTP/1.1"):
            self.request_version = "HTTP/1.0"
        self.close_connection = True
        status = HTTPStatus(code)
        self._json(status, {"error": ERRORS.get(status, status.phrase.lower())}, close=True)

    def log_message(self, format: str, *args: object) -> None:
        text = "".join(c if c.isprintable() else "?" for c in format % args)
        sys.stderr.write(f"{self.address_string()} {text[:400]}\n")

    def _headers(
        self, status: HTTPStatus, ctype: str | None, length: int | None, close: bool = False
    ) -> None:
        self.send_response(status)
        if ctype is not None:
            self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        if length is not None:
            self.send_header("Content-Length", str(length))
        if close:
            self.send_header("Connection", "close")
        self.end_headers()

    def _send(self, status: HTTPStatus, body: bytes, ctype: str, close: bool = False) -> None:
        self._headers(status, ctype, len(body), close)
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: object, close: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        self._send(status, body, "application/json", close)

    def _refuse(self) -> None:
        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read-only API"})

    def do_GET(self) -> None:
        try:
            self._route()
        except ClientError as error:
            if not self.streaming:
                self._json(error.status, {"error": error.message})
        except (ConnectionError, TimeoutError):
            return
        except Exception:  # never leak internals to a client
            self.log_message("internal error on %s", self.path[:MAX_PATH])
            if not self.streaming:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})

    def _route(self) -> None:
        if len(self.path) > MAX_PATH:
            raise ClientError(HTTPStatus.REQUEST_URI_TOO_LONG, "path too long")
        url = urlsplit(self.path)
        path = url.path
        if path in self.app.static:
            if url.query:
                raise ClientError(HTTPStatus.BAD_REQUEST, "unsupported query")
            body, ctype = self.app.static[path]
            self._send(HTTPStatus.OK, body, ctype)
            return
        if path == "/health" and not url.query:
            reason = self.app.readiness()
            if reason is None:
                self._json(HTTPStatus.OK, {"status": "ok", "broadcast_version": BROADCAST_VERSION})
            else:
                unavailable = {"status": "unavailable", "reason": reason}
                unavailable["broadcast_version"] = BROADCAST_VERSION
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, unavailable)
            return
        if path == "/matches" and not url.query:
            self._json(HTTPStatus.OK, {"matches": self.app.matches()})
            return
        parts = path.split("/")
        if len(parts) == 7 and parts[1::2] == ["matches", "storylines", "revisions"]:
            if url.query:
                raise ClientError(HTTPStatus.BAD_REQUEST, "unsupported query")
            body = self.app.explain(parts[2], parts[4], parts[6])
            self._send(HTTPStatus.OK, body, "application/json")
            return
        if len(parts) == 4 and parts[1] == "matches" and parts[3] in ("timeline", "stream"):
            live, key = self.app.resolve(parts[2], url.query)
            if parts[3] == "timeline":
                self.app.available(live)
                edition, done, timeline = live.timeline(key)
                body = (
                    f'{{"edition":{edition},"run":"{live.run}",'
                    f'"complete":{"true" if done else "false"},'
                    f'"timeline":{timeline.model_dump_json()}}}'
                ).encode()
                self._send(HTTPStatus.OK, body, "application/json")
            else:
                self._stream(live, key)
            return
        raise ClientError(HTTPStatus.NOT_FOUND, "not found")

    def _stream(self, live: LiveMatch, key: SurfaceKey) -> None:
        with live.condition:
            start = self._resume(live, key)
        if start is None:
            self._headers(HTTPStatus.NO_CONTENT, None, None, close=True)
            return
        self.app.available(live)
        if not self.app.streams.try_acquire():
            raise ClientError(HTTPStatus.SERVICE_UNAVAILABLE, "too many streams")
        try:
            self._headers(HTTPStatus.OK, "text/event-stream; charset=utf-8", None)
            self.streaming = True
            self.wfile.write(b"retry: 3000\n\n")
            self._serve_stream(live, key, start)
        finally:
            self.app.streams.release()

    def _serve_stream(self, live: LiveMatch, key: SurfaceKey, start: tuple[int, int, bool]) -> None:
        run = live.run
        edition, position, marker = start
        ended = False
        written = time.monotonic()
        while True:
            with live.condition:
                current = live.edition
                idle = position >= len(current.surfaces[key].messages)
                halted = live.state == FAILED or live.stopped or self.app.stopping
                waiting = not marker and idle and (ended or not current.done)
                if current.number == edition and waiting and not halted:
                    heartbeat = written + HEARTBEAT_SECONDS - time.monotonic()
                    live.condition.wait(max(0.0, min(SLICE_SECONDS, heartbeat)))
                    current = live.edition
                failed = live.state == FAILED
                if not failed and current.number != edition:
                    edition, position, ended, marker = current.number, 0, False, True
                messages = current.surfaces[key].messages
                pending: list[Message] = [] if failed else messages[position:]
                position += len(pending)
                finished = not failed and current.done and not ended and position >= len(messages)
            if failed:
                self.wfile.write(b"event: unavailable\ndata: {}\n\n")
                return
            if live.stopped or self.app.stopping:
                return
            chunks = []
            if marker:
                chunks.append(
                    f"id: {run}:{edition}:start\nevent: edition\n"
                    f'data: {{"edition":{edition},"run":"{run}"}}\n\n'
                )
                marker = False
            chunks += [
                f"id: {run}:{edition}:{m.index}\nevent: {m.event}\ndata: {m.data}\n\n"
                for m in pending
            ]
            if finished:
                chunks.append(f'event: end\ndata: {{"edition":{edition}}}\n\n')
                ended = True
            now = time.monotonic()
            if chunks or now - written >= HEARTBEAT_SECONDS:
                self.wfile.write("".join(chunks or [": keep-alive\n\n"]).encode("utf-8"))
                written = now
                if finished and not live.loop:
                    return
            elif not live.check_progress() and not self._peer_open():
                return

    def _resume(self, live: LiveMatch, key: SurfaceKey) -> tuple[int, int, bool] | None:
        """Under `live.condition`: where a stream starts, as (edition, position, marker), or
        None (204) for a `Last-Event-ID` from another run or one no genuine client could hold."""
        value = self.headers.get("Last-Event-ID", "")
        current = live.edition
        if not value:
            return current.number, 0, True
        found = RESUME.fullmatch(value) if len(value) <= MAX_RESUME else None
        if found is None or found[1] != live.run:
            return None
        edition = int(found[2])
        if edition < current.number:
            return current.number, 0, True
        if edition > current.number:
            return None
        if found[3] == "start":
            return edition, 0, False
        index = int(found[3])
        if index >= len(current.surfaces[key].messages):
            return None
        return edition, index + 1, False

    def _peer_open(self) -> bool:
        """Whether the stream's peer is still there: a closed, reset or talking peer is not."""
        connection = self.connection
        try:
            connection.settimeout(0)
            try:
                connection.recv(1)
            except BlockingIOError:
                return True
            finally:
                connection.settimeout(self.timeout)
        except OSError:
            return False
        return False


def _check_header(line: bytes, count: int) -> HTTPStatus | None:
    if count > MAX_HEADERS:
        return HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE
    if HEADER_LINE.fullmatch(line) is None:
        return HTTPStatus.BAD_REQUEST
    if line.split(b":", 1)[0].lower() in BODY_HEADERS:
        return HTTPStatus.BAD_REQUEST
    return None


class BroadcastServer(ThreadingHTTPServer):
    """Owns the listening socket and, until dispatch, each accepted one. The accept loop never
    blocks on a client; `service_actions` is its safe point for ticks and shutdown."""

    daemon_threads = True
    request_queue_size = 64

    def __init__(
        self,
        address: tuple[str, int],
        app: BroadcastApp,
        on_tick: Callable[[], bool] | None = None,
        should_stop: Callable[[], bool] | None = None,
        max_connections: int = MAX_CONNECTIONS,
        head_seconds: float = HEAD_SECONDS,
    ) -> None:
        """`on_tick` runs once per accept-loop iteration and returns whether the main thread
        may write to stderr; `should_stop` makes the loop raise `ShutdownRequested`."""
        self.app = app
        self.on_tick = on_tick
        self.should_stop = should_stop
        self.connections = Slots(max_connections)
        self.head_seconds = head_seconds
        self.rejected = 0
        self._reported = time.monotonic()
        self._conn_lock = threading.Lock()
        self._accepted: dict[object, float] = {}
        self._closing = threading.Lock()
        self._complete: bool | None = None
        handler = type("BoundHandler", (Handler,), {"app": app})
        super().__init__(address, handler)

    def accepted_at(self, request: object) -> float:
        with self._conn_lock:
            return self._accepted.get(request, time.monotonic())

    def process_request(self, request: object, client_address: object) -> None:
        if not isinstance(request, type(self.socket)):
            raise TypeError("expected a stream socket")
        if not self.connections.try_acquire():
            try:
                request.setblocking(False)
                try:  # noqa: SIM105 - contextlib is outside the API's module allow-list
                    request.send(REJECT_503)
                except OSError:
                    pass
            finally:
                request.close()
                self.rejected += 1
            return
        with self._conn_lock:
            self._accepted[request] = time.monotonic()
        try:
            super().process_request(request, client_address)
        except Exception:  # e.g. no thread can be started; the main thread writes nothing
            with self._conn_lock:
                self._accepted.pop(request, None)
            self.connections.release()
            self.rejected += 1
            self.shutdown_request(request)

    def process_request_thread(self, request: object, client_address: object) -> None:
        try:
            if isinstance(request, type(self.socket)):
                super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def close_request(self, request: object) -> None:
        with self._conn_lock:
            self._accepted.pop(request, None)
        if isinstance(request, type(self.socket)):
            super().close_request(request)

    def service_actions(self) -> None:
        may_log = self.on_tick() if self.on_tick is not None else True
        if self.should_stop is not None and self.should_stop():
            raise ShutdownRequested
        now = time.monotonic()
        if may_log and self.rejected and now - self._reported >= REJECT_REPORT_SECONDS:
            sys.stderr.write(
                f"rejected {self.rejected} connections (cap {self.connections.limit})\n"
            )
            self.rejected, self._reported = 0, now

    def close_gracefully(self, deadline: float) -> bool:
        """Stop serving, drain streams and connections, stop the replays, all by `deadline`.
        Idempotent: a second call returns the first result. True if everything finished."""
        with self._closing:
            if self._complete is not None:
                return self._complete
            self.app.begin_stop()
            super().server_close()
            for live in self.app.live.values():
                live.request_stop()
            self.app.streams.wait_idle(deadline - 3)
            kind = type(self.socket)
            with self._conn_lock:
                for request in self._accepted:
                    if isinstance(request, kind):
                        try:  # noqa: SIM105 - contextlib is outside the API's module allow-list
                            request.shutdown(SHUT_RDWR)
                        except OSError:
                            pass
            streams = self.app.streams.wait_idle(deadline - 1)
            replays = [live.join(deadline - time.monotonic()) for live in self.app.live.values()]
            connections = self.connections.wait_idle(deadline)
            self._complete = streams and all(replays) and connections
            if not self._complete:
                sys.stderr.write(
                    f"shutdown deadline exceeded: streams {self.app.streams.active}, "
                    f"connections {self.connections.active}, "
                    f"replays {replays.count(False)} still running\n"
                )
            return self._complete

    def server_close(self) -> None:
        self.close_gracefully(time.monotonic() + SHUTDOWN_SECONDS)
