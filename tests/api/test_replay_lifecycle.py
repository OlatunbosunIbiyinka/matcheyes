"""Replay lifecycle (ADR-0017): failures are sticky and make the surface unready, never restart it.

Every replay failure (an exception, an unexpected thread exit, a stall) moves the match to FAILED
once, logs one line on stderr and nothing over HTTP, stops publication and turns readiness to 503;
an unusable recording is RECORDING_UNAVAILABLE from the start. Normal states stay ready.
"""

import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from matcheyes.api import live as live_module
from matcheyes.api.live import (
    FAILED,
    FINISHED,
    PAUSED,
    READY,
    RECORDING_UNAVAILABLE,
    REPLAYING,
    STOPPED,
    Backend,
    LiveMatch,
)
from matcheyes.lifecycle.engine import LifecycleEngine
from tests.api.support import app, get_json, request, serving, sse, wait_done
from tests.lifecycle.support import evaluator, in_progress

MATCH = in_progress().info.match_id
STREAM = f"/matches/{MATCH}/stream?audience=fan"


def _until(predicate: Callable[[], bool], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not reached")
        time.sleep(0.01)


class Gate:
    """Patches `LifecycleEngine.ingest`: event number `at` (1-based) waits for `release()`, then
    raises `error` if one is given."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, at: int, error: BaseException | None = None
    ) -> None:
        self.at, self.error = at, error
        self.reached, self.go = threading.Event(), threading.Event()
        self.calls = 0
        original = LifecycleEngine.ingest

        def ingest(engine: LifecycleEngine, event: Any) -> Any:
            self.calls += 1
            if self.calls == self.at:
                self.reached.set()
                self.go.wait(60)
                if self.error is not None:
                    raise self.error
            return original(engine, event)

        monkeypatch.setattr(LifecycleEngine, "ingest", ingest)

    def release(self) -> None:
        self.go.set()


@pytest.fixture
def stopped() -> Iterator[list[LiveMatch]]:
    made: list[LiveMatch] = []
    yield made
    for live in made:
        live.request_stop()


def _live(made: list[LiveMatch], **options: Any) -> LiveMatch:
    settings: dict[str, Any] = {"speed": 0, "loop": False, "evaluator": evaluator(), **options}
    live = LiveMatch(in_progress(), **settings)
    made.append(live)
    return live


def test_each_match_has_its_own_128_bit_run(stopped: list[LiveMatch]) -> None:
    runs = {_live(stopped).run for _ in range(3)}
    assert len(runs) == 3 and all(re.fullmatch(r"[0-9a-f]{32}", r) for r in runs)


def test_normal_states_are_ready_and_a_finished_replay_stays_ready(
    stopped: list[LiveMatch], capsys: pytest.CaptureFixture[str]
) -> None:
    live = _live(stopped)
    assert live.state == READY and live.health(time.monotonic() + 1) is None
    live.start()
    wait_done(live)
    _until(lambda: live.state == FINISHED)
    assert live.health(time.monotonic() + 1) is None and live.serving
    assert not live.check_progress()
    err = capsys.readouterr().err
    assert re.search(rf"replay edition: {MATCH} edition 0 events {len(live.events)} ", err)
    assert "max_progress_gap" in err and "replay failed" not in err
    assert live.stop() and live.state == STOPPED
    assert live.health(time.monotonic() + 1) == "stopping"


def test_a_paused_loop_is_ready_and_resumes(stopped: list[LiveMatch]) -> None:
    live = _live(stopped, loop=True, pause=0.6, stall_seconds=0.3)
    live.start()
    _until(lambda: live.state == PAUSED)
    for _ in range(4):
        assert not live.check_progress() and live.health(time.monotonic() + 1) is None
        time.sleep(0.1)
    _until(lambda: live.edition.number == 1)
    assert live.state in (REPLAYING, PAUSED)
    assert live.stop() and live.state == STOPPED


def test_an_overdue_pause_is_a_stall(stopped: list[LiveMatch]) -> None:
    live = _live(stopped, loop=True, pause=60, stall_seconds=0.2)
    live.start()
    _until(lambda: live.state == PAUSED)
    assert not live.check_progress()
    with live.condition:
        live.pause_until = time.monotonic() - 1
    assert live.check_progress() and (live.state, live.failure) == (FAILED, "stalled")


def test_a_stalled_ingest_fails_once_and_publishes_nothing_after(
    stopped: list[LiveMatch], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = Gate(monkeypatch, at=40)
    live = _live(stopped, stall_seconds=0.2)
    live.start()
    assert gate.reached.wait(60)
    assert not live.check_progress()
    time.sleep(0.35)
    assert live.health(time.monotonic() + 1) == "replay"
    assert (live.state, live.failure) == (FAILED, "stalled")
    counts = {k: len(s.messages) for k, s in live.edition.surfaces.items()}
    gate.release()
    assert live.join(30)
    assert {k: len(s.messages) for k, s in live.edition.surfaces.items()} == counts
    assert not live.edition.done and live.state == FAILED
    err = capsys.readouterr().err
    assert err.count("replay failed:") == 1
    assert f"replay failed: {MATCH} edition 0 event 39/{len(live.events)} category=stalled" in err
    assert "in ingest" in err


def test_a_slow_but_progressing_backlog_is_not_a_stall(
    stopped: list[LiveMatch], monkeypatch: pytest.MonkeyPatch
) -> None:
    warm = _live(stopped)
    warm.start()
    wait_done(warm)
    original = LifecycleEngine.ingest

    def slow(engine: LifecycleEngine, event: Any) -> Any:
        time.sleep(0.02)
        return original(engine, event)

    monkeypatch.setattr(LifecycleEngine, "ingest", slow)
    live = _live(stopped, speed=1_000_000, stall_seconds=0.5)
    live.start()
    lag = 0.0
    while lag <= 1.5:
        assert not live.check_progress() and live.state == REPLAYING
        lag = live.last_progress - live.due_at
        time.sleep(0.05)
    assert live.health(time.monotonic() + 1) is None


def test_an_exception_fails_the_match_and_never_reaches_a_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = Gate(monkeypatch, at=60, error=RuntimeError("secret detail at /some/path"))
    broadcast = app()
    live = broadcast.live[MATCH]
    with serving(broadcast) as address:
        result: dict[str, Any] = {}
        reader = threading.Thread(target=lambda: result.update(body=request(address, STREAM)[2]))
        reader.start()
        assert gate.reached.wait(60)
        _until(lambda: broadcast.streams.active == 1)
        gate.release()
        reader.join(30)
        assert not reader.is_alive()
        assert (live.state, live.failure) == (FAILED, "replay_error")
        messages = sse(result["body"])
        assert messages[-1] == {"event": "unavailable", "data": "{}"}
        assert sum(m.get("event") == "unavailable" for m in messages) == 1
        status, body = get_json(address, "/health")
        assert (status, body) == (
            503,
            {"status": "unavailable", "reason": "replay", "broadcast_version": "0.1.0"},
        )
        for path in (STREAM, f"/matches/{MATCH}/timeline"):
            status, _, raw = request(address, path)
            assert (status, json.loads(raw)) == (503, {"error": "replay unavailable"})
        assert broadcast.streams.active == 0
        assert b"secret" not in result["body"]
    err = capsys.readouterr().err
    assert err.count("replay failed:") == 1
    assert "category=replay_error RuntimeError" in err and "secret detail" not in err


class Abort(BaseException):
    pass


def test_a_thread_ended_by_a_base_exception_is_a_failure(
    stopped: list[LiveMatch], monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = Gate(monkeypatch, at=5, error=Abort())
    monkeypatch.setattr(threading, "excepthook", lambda _: None)
    live = _live(stopped)
    live.start()
    gate.release()
    assert live.join(30)
    assert (live.state, live.failure) == (FAILED, "thread_exited")
    assert live.health(time.monotonic() + 1) == "replay"


def test_a_dead_thread_in_a_live_state_is_seen_by_the_passive_check(
    stopped: list[LiveMatch],
) -> None:
    live = _live(stopped)
    with live.condition:
        live.state = REPLAYING
        live._thread = threading.Thread(target=lambda: None)
        live._thread.start()
        live._thread.join()
    assert live.check_progress() and live.failure == "thread_exited"


def test_concurrent_failures_transition_and_log_once(
    stopped: list[LiveMatch], capsys: pytest.CaptureFixture[str]
) -> None:
    live = _live(stopped)
    results: list[bool] = []
    start = threading.Barrier(8)

    def fail() -> None:
        start.wait()
        results.append(live.fail("replay_error", ValueError("x")))

    threads = [threading.Thread(target=fail) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False] * 7 + [True]
    assert not live.fail("stalled") and live.failure == "replay_error"
    assert capsys.readouterr().err.count("replay failed:") == 1
    live.start()
    assert live._thread is None


def test_an_unusable_recording_is_never_replayed_and_never_ready() -> None:
    broadcast = app(backends={MATCH: Backend(evaluator(), available=False)})
    live = broadcast.live[MATCH]
    assert live.state == RECORDING_UNAVAILABLE and not live.serving
    with serving(broadcast) as address:
        assert get_json(address, "/health") == (
            503,
            {"status": "unavailable", "reason": "recording", "broadcast_version": "0.1.0"},
        )
        for path in (
            STREAM,
            f"/matches/{MATCH}/timeline",
            f"/matches/{MATCH}/storylines/sl-x/revisions/1",
        ):
            status, _, raw = request(address, path)
            assert (status, json.loads(raw)) == (503, {"error": "replay unavailable"})
        status, body = get_json(address, "/matches")
        assert status == 200 and body["matches"][0]["match_id"] == MATCH
    assert live._thread is None and live.state == RECORDING_UNAVAILABLE


def test_a_wedged_replay_lock_makes_readiness_fail_within_its_deadline() -> None:
    broadcast = app()
    live = broadcast.live[MATCH]
    held, done = threading.Event(), threading.Event()

    def wedge() -> None:
        with live.condition:
            held.set()
            done.wait(30)

    holder = threading.Thread(target=wedge)
    holder.start()
    try:
        assert held.wait(10)
        began = time.monotonic()
        assert broadcast.readiness() == "replay"
        assert 0.9 <= time.monotonic() - began < 2.0
    finally:
        done.set()
        holder.join()
    assert broadcast.readiness() is None
    assert live.state == READY


def test_stopping_makes_readiness_stopping_and_refuses_replays() -> None:
    broadcast = app()
    with serving(broadcast) as address:
        broadcast.begin_stop()
        assert get_json(address, "/health")[1]["reason"] == "stopping"
        status, _, raw = request(address, f"/matches/{MATCH}/timeline")
        assert (status, json.loads(raw)) == (503, {"error": "replay unavailable"})


def test_the_stall_threshold_is_provisional_and_validated() -> None:
    assert live_module.STALL_SECONDS == 120.0
    with pytest.raises(ValueError, match="stall_seconds"):
        LiveMatch(in_progress(), speed=0, stall_seconds=0)
