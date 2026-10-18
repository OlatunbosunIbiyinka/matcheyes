"""Bounded shutdown (ADR-0017): the first SIGTERM or SIGINT is recorded and shutdown starts at the
accept loop's safe point; streams, connections and replays drain within 10 s; a second signal
exits at once. The process-level tests need POSIX signals and are skipped elsewhere."""

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import matcheyes.__main__ as cli
from matcheyes.api.server import BroadcastServer, ShutdownRequested
from matcheyes.ingestion.io import write_observable_match
from matcheyes.lifecycle.engine import LifecycleEngine
from tests.api.support import app, child_env, fake_watchdog, running, wait_done
from tests.lifecycle.support import in_progress

MATCH = in_progress().info.match_id
POSIX = pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals and process groups")


def test_close_gracefully_is_idempotent() -> None:
    broadcast = app()
    server = BroadcastServer(("127.0.0.1", 0), broadcast)
    assert server.close_gracefully(time.monotonic() + 10)
    assert broadcast.stopping and broadcast.readiness() == "stopping"
    assert server.close_gracefully(time.monotonic() - 100)
    server.server_close()


@pytest.mark.skipif(os.name != "posix", reason="Windows does not wake a blocked recv on shutdown")
def test_a_pending_head_does_not_hold_up_shutdown() -> None:
    with running(app()) as (server, address):
        idle = socket.create_connection(address, timeout=15)
        deadline = time.monotonic() + 5
        while server.connections.active == 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        server.shutdown()
        began = time.monotonic()
        assert server.close_gracefully(time.monotonic() + 10)
        assert time.monotonic() - began < 3.0
        assert idle.recv(1024) == b""
        idle.close()


def test_a_stuck_replay_makes_shutdown_incomplete_by_its_deadline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reached, go = threading.Event(), threading.Event()
    original = LifecycleEngine.ingest

    def stuck(engine: LifecycleEngine, event: Any) -> Any:
        reached.set()
        go.wait(60)
        return original(engine, event)

    monkeypatch.setattr(LifecycleEngine, "ingest", stuck)
    broadcast = app()
    server = BroadcastServer(("127.0.0.1", 0), broadcast)
    try:
        broadcast.live[MATCH].start()
        assert reached.wait(30)
        began = time.monotonic()
        assert not server.close_gracefully(time.monotonic() + 2)
        assert 1.5 <= time.monotonic() - began < 3.0
        assert not server.close_gracefully(time.monotonic() + 10)
    finally:
        go.set()
    assert broadcast.live[MATCH].join(30)
    err = capsys.readouterr().err
    assert err.count("shutdown deadline exceeded:") == 1
    assert "replays 1 still running" in err


def test_a_recorded_signal_stops_the_accept_loop_at_its_safe_point() -> None:
    stop = threading.Event()
    ticks: list[float] = []

    def tick() -> bool:
        ticks.append(time.monotonic())
        return True

    server = BroadcastServer(("127.0.0.1", 0), app(), on_tick=tick, should_stop=stop.is_set)
    threading.Timer(0.8, stop.set).start()
    began = time.monotonic()
    with pytest.raises(ShutdownRequested):
        server.serve_forever(poll_interval=0.5)
    assert time.monotonic() - began < 2.0 and len(ticks) >= 2
    assert server.close_gracefully(time.monotonic() + 10)


def test_the_first_signal_is_recorded_and_a_second_exits_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exits: list[int] = []
    monkeypatch.setattr(cli.os, "_exit", exits.append)
    signals = cli.Signals()
    signals.handle(signal.SIGTERM, None)
    assert signals.first is not None and signals.first[0] == signal.SIGTERM and exits == []
    signals.handle(signal.SIGINT, None)
    assert exits == [128 + signal.SIGINT]
    assert signals.first[0] == signal.SIGTERM


# --- the serving process (POSIX) --------------------------------------------------------------

DRIVER = """
import sys, time
import matcheyes.__main__ as cli
from matcheyes.lifecycle.engine import LifecycleEngine
if sys.argv[2] == "stuck":
    LifecycleEngine.ingest = lambda engine, event: time.sleep(3600)
sys.exit(cli.main(["serve", sys.argv[1], "--port", "0", "--speed", "0", "--no-loop"]))
"""


class Served:
    def __init__(self, root: Path, mode: str) -> None:
        self.process = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
            [sys.executable, "-c", DRIVER, str(root), mode],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=child_env(),
        )
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        found = re.search(r"on http://127\.0\.0\.1:(\d+)", line)
        assert found, line
        self.address = ("127.0.0.1", int(found.group(1)))

    def get(self, path: str) -> None:
        with socket.create_connection(self.address, timeout=15) as sock:
            sock.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
            sock.recv(65536)

    def stop(self, *signals: signal.Signals, gap: float = 0.0) -> tuple[int, float]:
        began = time.monotonic()
        for i, signum in enumerate(signals):
            if i:
                time.sleep(gap)
            self.process.send_signal(signum)
        code = self.process.wait(timeout=30)
        return code, time.monotonic() - began

    def kill(self) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(5)


@pytest.fixture
def served(tmp_path: Path) -> Any:
    match = in_progress()
    write_observable_match(match, tmp_path / match.info.match_id)
    made: list[Served] = []

    def serve(mode: str = "normal") -> Served:
        made.append(Served(tmp_path, mode))
        return made[-1]

    yield serve
    for s in made:
        s.kill()


@POSIX
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_an_idle_server_exits_cleanly_on_its_first_signal(served: Any, signum: int) -> None:
    code, elapsed = served().stop(signum)
    assert code == 0 and elapsed <= 2.0


@POSIX
def test_a_stuck_replay_exits_1_by_the_deadline(served: Any) -> None:
    server = served("stuck")
    server.get(f"/matches/{MATCH}/timeline")
    code, elapsed = server.stop(signal.SIGTERM)
    assert code == 1 and elapsed <= 10.5
    assert server.process.stderr is not None
    assert "shutdown deadline exceeded" in server.process.stderr.read()


@POSIX
@pytest.mark.parametrize(("signum", "code"), [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_a_second_signal_during_cleanup_exits_at_once(
    served: Any, signum: signal.Signals, code: int
) -> None:
    server = served("stuck")
    server.get(f"/matches/{MATCH}/timeline")
    got, elapsed = server.stop(signum, signum, gap=0.5)
    assert got == code and elapsed <= 1.5


def test_the_handlers_are_restored_after_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

    def interrupted(self: BroadcastServer, *_: object) -> None:
        assert signal.getsignal(signal.SIGTERM) == signal.getsignal(signal.SIGINT)
        assert signal.getsignal(signal.SIGTERM) not in before.values()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "load_catalog", lambda _: {MATCH: in_progress()})
    monkeypatch.setattr(cli.BroadcastServer, "serve_forever", interrupted)
    fake_watchdog(monkeypatch, [])
    assert cli.main(["serve", "unused", "--port", "0", "--speed", "0", "--no-loop"]) == 0
    assert {s: signal.getsignal(s) for s in before} == before


def test_a_live_replay_drains_and_shutdown_completes() -> None:
    broadcast = app(loop=True, pause=60)
    with running(broadcast) as (server, _):
        broadcast.live[MATCH].start()
        wait_done(broadcast.live[MATCH])
        server.shutdown()
        assert server.close_gracefully(time.monotonic() + 10)
        assert broadcast.live[MATCH].state == "stopped"
