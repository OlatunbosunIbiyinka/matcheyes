"""`python -m matcheyes record` and `serve --recordings`: recorded model lifecycles, never live."""

import json
from pathlib import Path
from typing import Any

import pytest

import matcheyes.__main__ as cli
from matcheyes.agents.llm import LLMSettings, Usage
from matcheyes.agents.reasoning import AgentTask, RuleBasedReasoner
from tests.lifecycle.support import in_progress

pytestmark = pytest.mark.integration
MATCH = in_progress().info.match_id


class StandIn(RuleBasedReasoner):
    def __init__(self) -> None:
        self.usage = Usage()

    @property
    def name(self) -> str:
        return "stand-in"

    def respond(self, task: AgentTask) -> str:
        self.usage.calls += 1
        return super().respond(task)


SUMMARY: dict[str, Any] = {}


@pytest.fixture
def recordings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Path:
    settings = LLMSettings.from_env(
        {
            "MATCHEYES_LLM_ENDPOINT": "https://example.invalid/openai/v1/",
            "MATCHEYES_LLM_MODEL": "dep",
            "MATCHEYES_LLM_AUTH": "entra",
        }
    )
    monkeypatch.setattr(cli.LLMSettings, "from_env", staticmethod(lambda _: settings))
    monkeypatch.setattr(cli, "live_model", lambda _: StandIn())
    monkeypatch.setattr(cli, "load_observable_match", lambda _: in_progress())
    out = tmp_path / "recordings"
    assert cli.main(["record", "unused", "--out", str(out), "--workers", "4"]) == 0
    printed = capsys.readouterr().out
    SUMMARY.clear()
    SUMMARY.update(json.loads(printed[printed.index("{") :]))
    return out


def _serve(monkeypatch: pytest.MonkeyPatch, recordings: Path) -> Any:
    seen: dict[str, Any] = {}

    def interrupted(self: cli.BroadcastServer, *_: object) -> None:
        seen["app"] = self.app
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "load_catalog", lambda _: {MATCH: in_progress()})
    monkeypatch.setattr(cli.BroadcastServer, "serve_forever", interrupted)
    argv = ["serve", "unused", "--port", "0", "--speed", "0", "--no-loop"]
    assert cli.main([*argv, "--recordings", str(recordings)]) == 0
    return seen["app"]


def test_record_pins_a_transcript_that_replays_identically(recordings: Path) -> None:
    summary = SUMMARY
    pins = json.loads((recordings / "pins.json").read_text("utf-8"))
    assert (recordings / f"{MATCH}.transcript.json.gz").is_file()
    assert pins == {MATCH: summary["transcript_sha256"]}
    assert summary["replay_identical"] is True and summary["transcript_problems"] == []
    assert summary["reasoner"] == "stand-in" and summary["unavailable"] == 0
    assert summary["metadata"]["deployment"] == "dep" and summary["entries"] > 0


def test_serve_replays_pinned_recordings_and_names_the_reasoner(
    recordings: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pins = json.loads((recordings / "pins.json").read_text("utf-8"))
    app = _serve(monkeypatch, recordings)
    reasoner = app.live[MATCH].reasoner
    assert reasoner.kind == "recorded-model" and reasoner.name == "stand-in"
    assert reasoner.transcript_sha256 == pins[MATCH] and reasoner.deployment == "dep"
    assert app.live[MATCH].explainer is not None
    assert f"{MATCH}: recorded-model reasoner stand-in" in capsys.readouterr().out


def test_a_mispinned_recording_is_reported_and_never_falls_back(
    recordings: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (recordings / "pins.json").write_text(json.dumps({MATCH: "0" * 64}), "utf-8")
    capsys.readouterr()
    app = _serve(monkeypatch, recordings)
    assert app.live[MATCH].reasoner.kind == "recorded-model"
    assert "transcript unusable (transcript hash mismatch)" in capsys.readouterr().err


def test_serve_without_recordings_uses_the_reference_reasoner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _serve(monkeypatch, tmp_path)
    assert app.live[MATCH].reasoner.kind == "reference"


def test_a_challenger_only_recording_is_served_in_the_same_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = LLMSettings.from_env(
        {
            "MATCHEYES_LLM_ENDPOINT": "https://example.invalid/openai/v1/",
            "MATCHEYES_LLM_MODEL": "dep",
            "MATCHEYES_LLM_AUTH": "entra",
        }
    )
    monkeypatch.setattr(cli.LLMSettings, "from_env", staticmethod(lambda _: settings))
    monkeypatch.setattr(cli, "live_model", lambda _: StandIn())
    monkeypatch.setattr(cli, "load_observable_match", lambda _: in_progress())
    out = tmp_path / "recordings"
    argv = ["record", "unused", "--out", str(out), "--workers", "4", "--roles", "challenger"]
    assert cli.main(argv) == 0
    reasoner = _serve(monkeypatch, out).live[MATCH].reasoner
    assert reasoner.name.startswith("investigator=") and reasoner.name.endswith("stand-in")


def test_record_needs_a_configured_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli.LLMSettings, "from_env", staticmethod(lambda _: None))
    assert cli.main(["record", "unused", "--out", str(tmp_path)]) == 2
    assert "LLM not configured" in capsys.readouterr().err
