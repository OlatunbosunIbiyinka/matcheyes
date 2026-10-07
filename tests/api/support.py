"""A real broadcast server on an ephemeral localhost port, over the lifecycle reference match."""

import http.client
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from matcheyes.api.live import LiveMatch
from matcheyes.api.server import BroadcastApp, BroadcastServer
from tests.lifecycle.support import evaluator, in_progress


def app(**options: Any) -> BroadcastApp:
    match = in_progress()
    settings: dict[str, Any] = {"speed": 0, "loop": False, "evaluator": evaluator(), **options}
    return BroadcastApp({match.info.match_id: match}, **settings)


@contextmanager
def serving(broadcast: BroadcastApp) -> Iterator[tuple[str, int]]:
    server = BroadcastServer(("127.0.0.1", 0), broadcast)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def request(
    address: tuple[str, int],
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(*address, timeout=60)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = b"" if method == "HEAD" else response.read()
        return response.status, dict(response.getheaders()), body
    finally:
        connection.close()


def get_json(address: tuple[str, int], path: str) -> tuple[int, Any]:
    status, _, body = request(address, path)
    return status, json.loads(body)


def wait_done(live: LiveMatch, edition: int = 0) -> None:
    with live.condition:
        live.condition.wait_for(
            lambda: live.edition.number > edition or (live.edition.done), timeout=300
        )


def sse(body: bytes) -> list[dict[str, str]]:
    """Parse a Server-Sent Events body into its messages (comments dropped)."""
    messages = []
    for block in body.decode("utf-8").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(": ")
            fields[name] = value
        if fields:
            messages.append(fields)
    return messages
