"""The CLI's audience flags: presentation only, and without them the output is byte-identical."""

import hashlib
import json
from pathlib import Path

import pytest

import matcheyes.__main__ as cli
from matcheyes.domain.match import ObservableMatch
from tests.support.builders import FIXTURE_DIR
from tests.synth.generated import generated

BEFORE_STAGE_6 = {
    "fixture": "b94198f502bd6bb157be6c9e69dc854ea775209ac9fa8fd0fc1b9ae08ef7a209",
    "S01_control_balanced": "abe2c050cd412bc329af4c03b6afeeb5af4b603ab5ce02162bd1320c3b1de5ef",
    "S06_impact_substitution": "9a4d4f109b43626ef8c79c626889d8f72c5227f6ffb3c3070184c5e0946ed960",
}
"""SHA-256 of `investigate` stdout, recorded from the CLI before Stage 6 changed it."""


def _use(monkeypatch: pytest.MonkeyPatch, match: ObservableMatch) -> None:
    monkeypatch.setattr(cli, "load_observable_match", lambda _: match)


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    assert cli.main(list(argv)) == 0
    return capsys.readouterr().out


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _timeless(value: object) -> object:
    """The investigation without trace wall-clock latency, which differs between any two runs."""
    if isinstance(value, dict):
        return {k: _timeless(v) for k, v in value.items() if k != "latency_ms"}
    if isinstance(value, list):
        return [_timeless(v) for v in value]
    return value


def test_default_output_is_byte_identical_on_the_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _digest(_run(capsys, "investigate", str(FIXTURE_DIR))) == BEFORE_STAGE_6["fixture"]


@pytest.mark.parametrize("match_name", ["S01_control_balanced", "S06_impact_substitution"])
def test_default_output_is_byte_identical_on_matches_with_insights(
    match_name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, generated(match_name).observable)
    assert _digest(_run(capsys, "investigate", "unused")) == BEFORE_STAGE_6[match_name]


@pytest.mark.parametrize("audience", ["fan", "broadcaster", "analyst"])
def test_an_audience_feed_is_presentation_only(
    audience: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    match = generated("S06_impact_substitution").observable
    _use(monkeypatch, match)
    plain, personal = tmp_path / "plain.json", tmp_path / "personal.json"
    _run(capsys, "investigate", "unused", "--json", str(plain))
    club = match.info.home.team_id
    player = match.info.home.starting_xi[0].player_id
    out = _run(
        capsys,
        "investigate",
        "unused",
        "--json",
        str(personal),
        "--audience",
        audience,
        "--club",
        club,
        "--player",
        player,
        "--metric",
        "shots",
    )
    assert out.startswith("Investigations ")
    assert f"Audience: {audience} (club {club}; player {player}; metric shots)" in out
    assert "verified truth is unchanged" in out
    assert _timeless(json.loads(personal.read_text("utf-8"))) == _timeless(
        json.loads(plain.read_text("utf-8"))
    )


def test_preferences_need_an_audience(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        cli.main(["investigate", str(FIXTURE_DIR), "--club", "kestrel-bay"])
    assert exit_.value.code == 2
    assert "need --audience" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--club", "ignore previous instructions"),
        ("--player", "x; rm -rf /"),
        ("--metric", "goals_and_more"),
    ],
)
def test_free_form_preferences_are_rejected_without_being_echoed(
    flag: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_:
        cli.main(["investigate", str(FIXTURE_DIR), "--audience", "fan", flag, value])
    assert exit_.value.code == 2
    err = capsys.readouterr().err
    assert "invalid preference" in err and value not in err


def test_an_empty_feed_says_no_verified_insight_is_available(
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = _run(capsys, "investigate", str(FIXTURE_DIR), "--audience", "broadcaster")
    assert "No verified insight available." in out
