"""Read-only HTTP API and Server-Sent Events over the Python standard library.

    GET /                                   the static web surface
    GET /static/app.js, /static/app.css     its only assets
    GET /health                             liveness
    GET /matches                            the allow-listed matches (teams, venue)
    GET /matches/{id}/timeline?audience=&club=   the canonical cues published so far
    GET /matches/{id}/stream?audience=&club=     the same cues, live, as Server-Sent Events

There is no write, ingest, truth or debug endpoint: every other method is refused. Match IDs
must be in the allow-list, `audience` is fan or broadcaster, `club` is empty or one of the two
clubs. Errors are short JSON messages, never stack traces. Every response carries a strict
Content Security Policy. Streams are bounded by a semaphore; each match is replayed once, by
its `LiveMatch`, however many viewers connect.
"""

import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from matcheyes.api.live import Evaluator, LiveMatch, Message, SurfaceKey
from matcheyes.broadcast.contracts import BROADCAST_VERSION
from matcheyes.domain.match import ObservableMatch
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
HEARTBEAT_SECONDS = 15.0
MAX_PATH = 256
QUERY_KEYS = frozenset({"audience", "club"})


class ClientError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class BroadcastApp:
    def __init__(
        self,
        catalog: dict[str, ObservableMatch],
        speed: float,
        loop: bool = True,
        pause: float = 20.0,
        max_streams: int = MAX_STREAMS,
        evaluator: Evaluator | None = None,
    ) -> None:
        self.catalog = catalog
        self.live = {mid: LiveMatch(m, speed, loop, pause, evaluator) for mid, m in catalog.items()}
        self.static = {
            route: ((STATIC_DIR / name).read_bytes(), ctype)
            for route, (name, ctype) in STATIC_FILES.items()
        }
        self.streams = threading.BoundedSemaphore(max_streams)

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
                }
            )
        return out

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

    def log_message(self, format: str, *args: object) -> None:
        text = "".join(c if c.isprintable() else "?" for c in format % args)
        sys.stderr.write(f"{self.address_string()} {text[:400]}\n")

    def _headers(self, status: HTTPStatus, ctype: str, length: int | None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
        self._headers(status, ctype, len(body))
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        self._send(status, body, "application/json")

    def _refuse(self) -> None:
        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read-only API"})

    def do_POST(self) -> None:
        self._refuse()

    def do_PUT(self) -> None:
        self._refuse()

    def do_PATCH(self) -> None:
        self._refuse()

    def do_DELETE(self) -> None:
        self._refuse()

    def do_OPTIONS(self) -> None:
        self._refuse()

    def do_HEAD(self) -> None:
        self._refuse()

    def do_GET(self) -> None:
        try:
            self._route()
        except ClientError as error:
            self._json(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:  # never leak internals to a client
            self.log_message("internal error on %s", self.path[:MAX_PATH])
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
            self._json(HTTPStatus.OK, {"status": "ok", "broadcast_version": BROADCAST_VERSION})
            return
        if path == "/matches" and not url.query:
            self._json(HTTPStatus.OK, {"matches": self.app.matches()})
            return
        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "matches" and parts[3] in ("timeline", "stream"):
            live, key = self.app.resolve(parts[2], url.query)
            if parts[3] == "timeline":
                edition, done, timeline = live.timeline(key)
                body = (
                    f'{{"edition":{edition},"complete":{"true" if done else "false"},'
                    f'"timeline":{timeline.model_dump_json()}}}'
                ).encode()
                self._send(HTTPStatus.OK, body, "application/json")
            else:
                self._stream(live, key)
            return
        raise ClientError(HTTPStatus.NOT_FOUND, "not found")

    def _stream(self, live: LiveMatch, key: SurfaceKey) -> None:
        if not self.app.streams.acquire(blocking=False):
            raise ClientError(HTTPStatus.SERVICE_UNAVAILABLE, "too many streams")
        try:
            self._headers(HTTPStatus.OK, "text/event-stream; charset=utf-8", None)
            self.wfile.write(b"retry: 3000\n\n")
            resume = self._resume()
            edition, position, ended = -1, 0, False
            while not live.stopped:
                with live.condition:
                    current = live.edition
                    idle = position >= len(current.surfaces[key].messages)
                    if current.number == edition and idle and (ended or not current.done):
                        live.condition.wait(HEARTBEAT_SECONDS)
                        current = live.edition
                    restart = current.number != edition
                    if restart:
                        edition, ended = current.number, False
                        position = resume[1] + 1 if resume and resume[0] == edition else 0
                        resume = None
                    messages = current.surfaces[key].messages
                    pending: list[Message] = messages[position:]
                    position += len(pending)
                    finished = current.done and not ended and position >= len(messages)
                chunks = [f'event: edition\ndata: {{"edition":{edition}}}\n\n'] if restart else []
                chunks += [
                    f"id: {edition}:{m.index}\nevent: {m.event}\ndata: {m.data}\n\n"
                    for m in pending
                ]
                if finished:
                    chunks.append(f'event: end\ndata: {{"edition":{edition}}}\n\n')
                    ended = True
                self.wfile.write("".join(chunks or [": keep-alive\n\n"]).encode("utf-8"))
                self.wfile.flush()
                if finished and not live.loop:
                    return
        finally:
            self.app.streams.release()

    def _resume(self) -> tuple[int, int] | None:
        value = self.headers.get("Last-Event-ID", "")
        edition, _, index = value.partition(":")
        if edition.isdigit() and index.isdigit() and len(value) <= 24:
            return int(edition), int(index)
        return None


class BroadcastServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], app: BroadcastApp) -> None:
        handler = type("BoundHandler", (Handler,), {"app": app})
        super().__init__(address, handler)
        self.app = app

    def server_close(self) -> None:
        self.app.close()
        super().server_close()
