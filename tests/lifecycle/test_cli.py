"""`python -m matcheyes replay`: the lifecycle from the event log, canonical JSON, audience view."""

import json
from pathlib import Path

import pytest

import matcheyes.__main__ as cli
from matcheyes.lifecycle.contracts import LifecycleState
from matcheyes_eval.stage7 import canonical
from tests.lifecycle.support import in_progress, reference
from tests.support.builders import FIXTURE_DIR


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_observable_match", lambda _: in_progress())


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    assert cli.main(list(argv)) == 0
    return capsys.readouterr().out


@pytest.mark.usefixtures("live")
def test_replay_writes_the_canonical_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "lifecycle.json"
    out = _run(capsys, "replay", "unused", "--json", str(path))
    state = LifecycleState.model_validate(json.loads(path.read_text("utf-8")))
    assert canonical(state) == canonical(reference().state)
    assert out.startswith(f"Lifecycle - {state.match_id} (snapshot-anchored")
    assert "status current" in out and "Current:" in out and "Withdrawn:" in out
    assert f"{len(state.storylines)} storylines" in out


@pytest.mark.usefixtures("live")
def test_replay_is_deterministic(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(capsys, "replay", "unused") == _run(capsys, "replay", "unused")


@pytest.mark.usefixtures("live")
def test_replay_with_an_audience_adds_the_stage6_view(capsys: pytest.CaptureFixture[str]) -> None:
    out = _run(capsys, "replay", "unused", "--audience", "broadcaster")
    assert out.startswith("Lifecycle - ")
    assert "Audience: broadcaster" in out


def test_replay_of_a_match_without_candidates(capsys: pytest.CaptureFixture[str]) -> None:
    out = _run(capsys, "replay", str(FIXTURE_DIR), "--audience", "fan")
    assert "status no_candidate" in out
    assert "No verified insight available." in out


def test_replay_preferences_need_an_audience(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        cli.main(["replay", str(FIXTURE_DIR), "--club", "kestrel-bay"])
    assert exit_.value.code == 2
    assert "need --audience" in capsys.readouterr().err
