"""`python -m matcheyes cues` (offline cue timeline) and `serve` (the live surface)."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

import matcheyes.__main__ as cli
from matcheyes.broadcast.contracts import CueTimeline
from tests.broadcast.support import timeline
from tests.lifecycle.support import in_progress, reference
from tests.support.builders import FIXTURE_DIR


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_observable_match", lambda _: in_progress())
    monkeypatch.setattr(cli, "replay", lambda *_: reference())


@pytest.mark.usefixtures("live")
def test_cues_writes_the_canonical_timeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "cues.json"
    assert cli.main(["cues", "unused", "--json", str(path)]) == 0
    tl = CueTimeline.model_validate(json.loads(path.read_text("utf-8")))
    assert tl == timeline()
    out = capsys.readouterr().out
    assert out.startswith(f"Cue timeline {tl.timeline_id} - {tl.match_id} (fan;")
    assert len(out.splitlines()) == 1 + len(tl.cues)


@pytest.mark.usefixtures("live")
def test_cues_validates_the_club(capsys: pytest.CaptureFixture[str]) -> None:
    home = in_progress().info.home.team_id
    assert cli.main(["cues", "unused", "--audience", "broadcaster", "--club", home]) == 0
    assert "(broadcaster;" in capsys.readouterr().out
    for club in ("someone-else", "x" * 200):
        with pytest.raises(SystemExit) as exit_:
            cli.main(["cues", "unused", "--club", club])
        assert exit_.value.code == 2


def test_cues_of_a_match_without_candidates(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["cues", str(FIXTURE_DIR)]) == 0
    out = capsys.readouterr().out
    assert "No verified insight available." in out and "Full time" in out
    assert not re.search(r" (insight|revision|retraction) +p\d", out)


def test_serve_starts_on_localhost_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def interrupted(self: cli.BroadcastServer, *_: object) -> None:
        seen["address"] = self.server_address
        seen["apps"] = self.app
        raise KeyboardInterrupt

    match = in_progress()
    monkeypatch.setattr(cli, "load_catalog", lambda _: {match.info.match_id: match})
    monkeypatch.setattr(cli.BroadcastServer, "serve_forever", interrupted)
    assert cli.main(["serve", "unused", "--port", "0", "--speed", "0", "--no-loop"]) == 0
    assert seen["address"][0] == "127.0.0.1"
    assert list(seen["apps"].live) == [match.info.match_id]
    assert "on http://127.0.0.1:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["serve", str(FIXTURE_DIR), "--speed", "-1"],
        ["serve", str(FIXTURE_DIR), "--speed", "1000"],
        ["serve", str(FIXTURE_DIR), "--port", "70000"],
        ["serve", "does-not-exist"],
    ],
)
def test_serve_rejects_bad_options(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        cli.main(argv)
    assert exit_.value.code == 2
    assert "error" in capsys.readouterr().err
