"""The serving process's watchdog (ADR-0017): a 30 s `faulthandler` timer re-armed by the accept
loop at most every 5 s. A failed arm leaves the process unprotected: no main-thread stderr writes,
a retry on every tick, and `os._exit(1)` without a write unless protection is back within 5 s.
Recovery is logged only after the new timer exists. The first arm happens before the banner and
before the accept loop; if it fails the process exits at once. Stopping always cancels the timer.

W1, W2 and U-START run everywhere with fakes. FC2-FC4 run the real timer in a child process
(POSIX only: FIFOs, process groups) and take about two minutes together.
"""

import faulthandler
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

import matcheyes.__main__ as cli
from matcheyes.api.server import BroadcastServer
from matcheyes.ingestion.io import write_observable_match
from tests.api.support import app, child_env, fake_watchdog
from tests.lifecycle.support import in_progress

MATCH = in_progress().info.match_id


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_w1_rearm_failure_policy_recovery_and_unconditional_cancel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock, failing = Clock(), [False]
    arms: list[float] = []
    cancels: list[float] = []
    exits: list[int] = []

    def arm(timeout: int, exit: bool = False) -> None:
        assert (timeout, exit) == (30, True)
        arms.append(clock.now)
        if failing[0]:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(faulthandler, "dump_traceback_later", arm)
    monkeypatch.setattr(faulthandler, "cancel_dump_traceback_later", lambda: cancels.append(1))
    monkeypatch.setattr(cli.os, "_exit", exits.append)
    dog = cli.Watchdog(clock)

    def at(now: float) -> bool:
        clock.now = now
        return dog.tick()

    assert dog.arm_initial() and dog.state == "protected" and arms == [100.0]
    assert all(at(t) for t in (100.5, 101.0, 104.9)) and arms == [100.0]
    assert at(105.0) and arms == [100.0, 105.0]
    assert at(109.9) and arms == [100.0, 105.0]

    failing[0] = True
    assert not at(110.0) and dog.state == "unprotected"
    assert not at(110.5) and arms[-2:] == [110.0, 110.5]
    assert capsys.readouterr().err == ""
    failing[0] = False
    assert at(111.0) and dog.state == "protected" and arms[-1] == 111.0
    assert capsys.readouterr().err == (
        "watchdog re-armed after 2 failed attempts (first error RuntimeError)\n"
    )
    assert at(115.9) and arms[-1] == 111.0

    failing[0] = True
    assert not at(120.0)
    assert not at(124.9) and exits == []
    assert not at(125.0) and exits == [1]
    assert capsys.readouterr().err == "" and dog.state == "unprotected"

    dog.stop()
    assert dog.state == "stopped" and cancels == [1]
    count = len(arms)
    assert at(200.0) and at(300.0) and len(arms) == count
    dog.stop()
    assert cancels == [1, 1]


def test_w2_the_server_ticks_only_through_its_hook(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    touched: list[object] = []
    monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda *a, **k: touched.append(a))
    plain = BroadcastServer(("127.0.0.1", 0), app())
    plain.service_actions()
    plain.server_close()
    assert touched == []

    allowed = [False]
    ticks: list[float] = []

    def tick() -> bool:
        ticks.append(time.monotonic())
        return allowed[0]

    server = BroadcastServer(("127.0.0.1", 0), app(), on_tick=tick)
    try:
        server.rejected, server._reported = 3, time.monotonic() - 60
        server.service_actions()
        assert len(ticks) == 1 and server.rejected == 3
        assert "rejected" not in capsys.readouterr().err
        allowed[0] = True
        server.service_actions()
        assert server.rejected == 0
        assert capsys.readouterr().err == "rejected 3 connections (cap 64)\n"
        loop = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5})
        before = len(ticks)
        loop.start()
        time.sleep(1.6)
        server.shutdown()
        loop.join(10)
        assert len(ticks) - before >= 3
    finally:
        server.server_close()


def test_u_start_a_failed_first_arm_exits_before_the_banner_and_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Exited(BaseException):
        pass

    def exit_(code: int) -> None:
        raise Exited(code)

    calls: list[str] = []
    served: list[object] = []
    fake_watchdog(monkeypatch, calls, arms=0)
    monkeypatch.setattr(cli.os, "_exit", exit_)
    monkeypatch.setattr(cli, "load_catalog", lambda _: {MATCH: in_progress()})
    monkeypatch.setattr(cli.BroadcastServer, "serve_forever", lambda self, *_: served.append(1))
    with pytest.raises(Exited) as exited:
        cli.main(["serve", "unused", "--port", "0", "--speed", "0", "--no-loop"])
    assert exited.value.args == (1,)
    assert served == [] and calls == ["arm", "cancel"]
    assert capsys.readouterr() == ("", "")


# --- FC2-FC4: the real timer in a child process (POSIX) ----------------------------------------

FC = pytest.mark.skipif(os.name != "posix", reason="needs FIFOs and process groups")

DRIVER = """
import faulthandler, os, sys, time
import matcheyes.__main__ as cli
from matcheyes.api.server import BroadcastServer

root, plan, marker, freeze = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4] == "freeze"
real_arm, real_cancel = faulthandler.dump_traceback_later, faulthandler.cancel_dump_traceback_later
state = {"calls": 0, "failed": False, "recovered": None}

def mark(text):
    os.write(marker, f"{text} {time.monotonic():.6f}\\n".encode())

def arm(timeout, exit=False):
    state["calls"] += 1
    if plan[min(state["calls"], len(plan)) - 1] == "f":
        mark("INJECT")
        real_cancel()
        state["failed"] = True
        raise RuntimeError("can't start new thread")
    real_arm(timeout, exit=exit)
    if state["failed"] and state["recovered"] is None:
        state["recovered"] = time.monotonic()

faulthandler.dump_traceback_later = arm
tick = BroadcastServer.service_actions

def service_actions(self):
    tick(self)
    recovered = state["recovered"]
    if freeze and recovered is not None and time.monotonic() - recovered >= 10:
        mark("FREEZE")
        while True:
            time.sleep(60)

BroadcastServer.service_actions = service_actions
sys.exit(cli.main(["serve", root, "--port", "0", "--speed", "0", "--no-loop"]))
"""


class Child:
    """The serving process under test, with markers on a non-blocking pipe (fd `marker`)."""

    def __init__(self, root: Path, plan: str, freeze: bool, stderr: int, log: Path) -> None:
        self.marker_r, marker_w = os.pipe()
        os.set_blocking(self.marker_r, False)
        self.pending = b""
        self.markers: list[tuple[str, float]] = []
        with log.open("wb") as out:
            self.process = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
                [
                    sys.executable,
                    "-c",
                    DRIVER,
                    str(root),
                    plan,
                    str(marker_w),
                    "freeze" if freeze else "-",
                ],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=stderr,
                pass_fds=(marker_w,),
                start_new_session=True,
                env=child_env(),
            )
        os.close(marker_w)
        self.killed = False

    def poll_markers(self) -> None:
        try:
            self.pending += os.read(self.marker_r, 65536)
        except BlockingIOError:
            return
        *lines, self.pending = self.pending.split(b"\n")
        for line in lines:
            name, at = line.decode().split()
            self.markers.append((name, float(at)))

    def first(self, name: str) -> float | None:
        return next((at for n, at in self.markers if n == name), None)

    def wait_marker(self, name: str, timeout: float) -> float | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.process.poll() is None:
            self.poll_markers()
            if (found := self.first(name)) is not None:
                return found
            time.sleep(0.01)
        self.poll_markers()
        return self.first(name)

    def wait_exit(self, timeout: float) -> tuple[int | None, float]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (code := self.process.poll()) is not None:
                return code, time.monotonic()
            time.sleep(0.01)
        return None, time.monotonic()

    def kill(self) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.killed = True
            self.process.wait(5)
        os.close(self.marker_r)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    match = in_progress()
    write_observable_match(match, tmp_path / "match" / match.info.match_id)
    return tmp_path / "match"


class FullPipe:
    """A FIFO whose buffer is full and whose reader stays open, so any write to it blocks."""

    PATTERN = b"prefill."

    def __init__(self) -> None:
        shm = "/dev/shm"  # noqa: S108 - the FIFO belongs on tmpfs; mkdtemp makes it private
        self.directory = tempfile.mkdtemp(dir=shm if os.path.isdir(shm) else None)
        self.path = os.path.join(self.directory, "stderr")
        os.mkfifo(self.path)
        self.read_fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        self.write_fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
        self.written = 0
        try:
            while True:
                self.written += os.write(self.write_fd, self.PATTERN)
        except BlockingIOError:
            pass
        os.set_blocking(self.write_fd, True)
        probe = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            with pytest.raises(BlockingIOError):
                os.write(probe, b"x")
        finally:
            os.close(probe)

    def handed_over(self) -> None:
        os.close(self.write_fd)

    def drain(self) -> bytes:
        data = b""
        while True:
            try:
                chunk = os.read(self.read_fd, 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            data += chunk
        return data

    def remove(self) -> None:
        os.close(self.read_fd)
        os.unlink(self.path)
        os.rmdir(self.directory)


@FC
@pytest.mark.slow
def test_fc2_a_recovered_watchdog_is_a_real_timer(root: Path, tmp_path: Path) -> None:
    stderr = tmp_path / "stderr.log"
    with stderr.open("wb") as err:
        child = Child(root, "offo", True, err.fileno(), tmp_path / "stdout.log")
    try:
        frozen = child.wait_marker("FREEZE", 90)
        assert frozen is not None, "no FREEZE within 90 s of launch"
        code, ended = child.wait_exit(60)
        assert code == 1, f"exit status {code}"
        assert 24.0 <= ended - frozen <= 31.0, ended - frozen
    finally:
        child.kill()
    assert not child.killed
    text = stderr.read_text("utf-8", "replace")
    assert "Timeout (0:00:30)!" in text and "Thread 0x" in text
    assert len(re.findall(r"watchdog re-armed after 2 failed attempts", text)) == 1


@FC
@pytest.mark.slow
def test_fc3_failing_closed_never_depends_on_stderr(root: Path, tmp_path: Path) -> None:
    pipe = FullPipe()
    try:
        child = Child(root, "of", False, pipe.write_fd, tmp_path / "stdout.log")
        pipe.handed_over()
        try:
            injected = child.wait_marker("INJECT", 30)
            assert injected is not None, "no INJECT within 30 s of launch"
            code, ended = child.wait_exit(20)
            assert code == 1, f"exit status {code}"
            assert 4.9 <= ended - injected <= 6.5, ended - injected
        finally:
            child.kill()
        assert not child.killed
        data = pipe.drain()
        assert data == (FullPipe.PATTERN * (len(data) // len(FullPipe.PATTERN)))
        assert len(data) == pipe.written
    finally:
        pipe.remove()


@FC
@pytest.mark.slow
def test_fc4_a_blocked_recovery_write_never_exits(root: Path, tmp_path: Path) -> None:
    """Control for FC3 and residual risk 1: the recovery line blocks on the full pipe."""
    pipe = FullPipe()
    try:
        child = Child(root, "ofo", False, pipe.write_fd, tmp_path / "stdout.log")
        pipe.handed_over()
        try:
            injected = child.wait_marker("INJECT", 30)
            assert injected is not None, "no INJECT within 30 s of launch"
            code, _ = child.wait_exit(45)
            assert code is None, f"the child exited by itself ({code}): the FIFO did not block"
        finally:
            child.kill()
        assert child.killed and child.process.returncode == -signal.SIGKILL
    finally:
        pipe.remove()


def test_the_watchdog_policy_constants_are_fixed() -> None:
    assert (cli.WATCHDOG_SECONDS, cli.WATCHDOG_REARM_SECONDS, cli.WATCHDOG_GRACE_SECONDS) == (
        30,
        5.0,
        5.0,
    )
