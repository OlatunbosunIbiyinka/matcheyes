"""The read-only HTTP API and its Server-Sent Events stream, over a real localhost server."""

import json
from collections.abc import Iterator

import pytest

from matcheyes.api.server import CSP, SECURITY_HEADERS
from matcheyes.broadcast.compiler import compile_timeline
from matcheyes.broadcast.contracts import BROADCAST_VERSION
from matcheyes.personalization.contracts import Audience, PersonalizationProfile
from tests.api.support import app, get_json, request, serving, sse, wait_done
from tests.lifecycle.support import in_progress, reference

MATCH = in_progress().info.match_id


def offline(audience: Audience, club: str | None = None) -> str:
    ref = reference()
    profile = PersonalizationProfile(audience=audience, favourite_club_id=club)
    tl = compile_timeline(in_progress().info, ref.log.events(), ref.state, profile)
    return tl.model_dump_json()


@pytest.fixture(scope="module")
def address() -> Iterator[tuple[str, int]]:
    broadcast = app()
    with serving(broadcast) as addr:
        broadcast.live[MATCH].start()
        wait_done(broadcast.live[MATCH])
        yield addr


def test_static_surface_is_served_with_strict_headers(address: tuple[str, int]) -> None:
    for path, ctype in (
        ("/", "text/html"),
        ("/static/app.js", "text/javascript"),
        ("/static/app.css", "text/css"),
    ):
        status, headers, body = request(address, path)
        assert status == 200 and headers["Content-Type"].startswith(ctype) and body
        for name, value in SECURITY_HEADERS.items():
            assert headers[name] == value
        assert headers["Cache-Control"] == "no-store"
        assert headers["Server"].startswith("MatchEyes") and "Python" not in headers["Server"]
    assert "'unsafe-inline'" not in CSP and "'unsafe-eval'" not in CSP


def test_health_and_the_match_allow_list(address: tuple[str, int]) -> None:
    assert get_json(address, "/health") == (
        200,
        {"status": "ok", "broadcast_version": BROADCAST_VERSION},
    )
    status, body = get_json(address, "/matches")
    assert status == 200 and [m["match_id"] for m in body["matches"]] == [MATCH]
    entry = body["matches"][0]
    assert set(entry) == {
        "match_id",
        "competition",
        "matchday",
        "venue",
        "synthetic",
        "home",
        "away",
        "reasoner",
    }
    assert entry["reasoner"]["kind"] == "reference"
    assert entry["synthetic"] is True
    assert set(entry["home"]) == {"team_id", "name", "short_name"}


@pytest.mark.parametrize(
    ("audience", "club"),
    [(Audience.FAN, None), (Audience.BROADCASTER, None), (Audience.FAN, "home")],
)
def test_the_live_timeline_equals_the_offline_compile(
    address: tuple[str, int], audience: Audience, club: str | None
) -> None:
    team = in_progress().info.home.team_id if club else None
    query = f"?audience={audience.value}" + (f"&club={team}" if team else "")
    status, body = get_json(address, f"/matches/{MATCH}/timeline{query}")
    assert status == 200 and body["complete"] is True and body["edition"] == 0
    assert body["timeline"] == json.loads(offline(audience, team))


def test_the_stream_replays_exactly_the_canonical_cues(address: tuple[str, int]) -> None:
    status, headers, body = request(address, f"/matches/{MATCH}/stream?audience=fan")
    assert status == 200 and headers["Content-Type"].startswith("text/event-stream")
    assert headers["Content-Security-Policy"] == CSP
    messages = sse(body)
    assert messages[0] == {"retry": "3000"}
    assert messages[1] == {"event": "edition", "data": '{"edition":0}'}
    assert messages[-1] == {"event": "end", "data": '{"edition":0}'}
    cues = [json.loads(m["data"]) for m in messages if m.get("event") == "cue"]
    assert cues == json.loads(offline(Audience.FAN))["cues"]
    ids = [m["id"] for m in messages if "id" in m]
    assert ids == [f"0:{i}" for i in range(len(ids))]
    ticks = [json.loads(m["data"]) for m in messages if m.get("event") == "clock"]
    assert len(ticks) == len(reference().state.snapshots)
    assert set(ticks[0]) == {
        "edition",
        "snapshot_id",
        "minute",
        "as_of",
        "period",
        "clock_ms",
        "watermark",
    }


def test_the_stream_resumes_after_the_last_event_id(address: tuple[str, int]) -> None:
    path = f"/matches/{MATCH}/stream?audience=fan"
    full = [m for m in sse(request(address, path)[2]) if "id" in m]
    resumed = [
        m for m in sse(request(address, path, headers={"Last-Event-ID": "0:5"})[2]) if "id" in m
    ]
    assert resumed == full[6:]
    ignored = sse(request(address, path, headers={"Last-Event-ID": "nonsense"})[2])
    assert [m for m in ignored if "id" in m] == full


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/matches/unknown/timeline", 404),
        ("/matches/../../etc/passwd", 404),
        ("/static/../server.py", 404),
        ("/debug", 404),
        (f"/matches/{MATCH}/truth", 404),
        (f"/matches/{MATCH}/timeline?audience=analyst", 400),
        (f"/matches/{MATCH}/timeline?audience=coach", 400),
        (f"/matches/{MATCH}/timeline?club=somebody", 400),
        (f"/matches/{MATCH}/timeline?audience=fan&audience=fan", 400),
        (f"/matches/{MATCH}/timeline?seed=1", 400),
        ("/static/app.js?v=1", 400),
        ("/health?x=1", 404),
        ("/" + "a" * 300, 414),
    ],
)
def test_invalid_requests_get_short_json_errors(
    address: tuple[str, int], path: str, status: int
) -> None:
    got, headers, body = request(address, path)
    assert got == status
    assert headers["Content-Type"] == "application/json"
    payload = json.loads(body)
    assert set(payload) == {"error"} and len(payload["error"]) < 40
    assert headers["Content-Security-Policy"] == CSP


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
def test_every_method_but_get_is_refused(address: tuple[str, int], method: str) -> None:
    status, _, body = request(address, f"/matches/{MATCH}/timeline", method=method)
    assert status == 405
    if method != "HEAD":
        assert json.loads(body) == {"error": "read-only API"}


def test_streams_are_bounded() -> None:
    broadcast = app(max_streams=1)
    assert broadcast.streams.acquire(blocking=False)
    with serving(broadcast) as addr:
        status, _, body = request(addr, f"/matches/{MATCH}/stream")
    assert status == 503 and json.loads(body) == {"error": "too many streams"}


def test_an_internal_error_reveals_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    broadcast = app()

    def broken() -> list[dict[str, object]]:
        raise RuntimeError("secret internals at /some/path")

    monkeypatch.setattr(broadcast, "matches", broken)
    with serving(broadcast) as addr:
        status, _, body = request(addr, "/matches")
    assert status == 500 and json.loads(body) == {"error": "internal error"}
