from pathlib import Path

import pytest

import matcheyes
from matcheyes.__main__ import main
from tests.support.builders import FIXTURE_DIR


def test_version_is_exposed() -> None:
    assert matcheyes.__version__


def test_cli_entrypoint_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "See beyond the score." in capsys.readouterr().out


def test_cli_analyse_prints_labelled_summary_and_writes_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "analysis.json"
    assert main(["analyse", str(FIXTURE_DIR), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Candidate moments" in text
    assert "FACT: Goal for Redmarsh Town" in text
    assert out.read_text("utf-8").startswith("{")


def test_cli_analyse_contextual_reports_stage3_section(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["analyse", str(FIXTURE_DIR), "--contextual"]) == 0
    text = capsys.readouterr().out
    assert "Contextual candidates 0.2.0" in text
    assert "none: no Stage 2 metric shifts to assess." in text
