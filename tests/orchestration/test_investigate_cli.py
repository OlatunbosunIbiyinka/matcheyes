import json
from pathlib import Path

import pytest

from matcheyes.__main__ import main
from matcheyes.agents.llm import ENV_API_KEY, ENV_ENDPOINT, ENV_MODEL
from tests.support.builders import FIXTURE_DIR


def test_investigate_runs_the_reference_reasoner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "inv.json"
    assert main(["investigate", str(FIXTURE_DIR), "--json", str(out)]) == 0
    assert "reasoning: rule-based-reference-v1" in capsys.readouterr().out
    assert json.loads(out.read_text("utf-8"))["model"] == "rule-based-reference-v1"


def test_llm_mode_refuses_to_run_unconfigured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (ENV_ENDPOINT, ENV_API_KEY, ENV_MODEL):
        monkeypatch.delenv(name, raising=False)
    assert main(["investigate", str(FIXTURE_DIR), "--llm"]) == 2
    assert "LLM not configured" in capsys.readouterr().err
