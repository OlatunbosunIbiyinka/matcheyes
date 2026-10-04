import json
from pathlib import Path

import pytest

from matcheyes_synth.__main__ import main
from matcheyes_synth.io import MANIFEST_FILE, OBSERVABLE_DIR, TRUTH_DIR


def test_generate_writes_both_worlds_and_a_manifest(tmp_path: Path) -> None:
    code = main(
        [
            "generate",
            "--scenario",
            "S08_coincidence_decoy",
            "--seed",
            "900003",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    [record] = json.loads((tmp_path / TRUTH_DIR / MANIFEST_FILE).read_text("utf-8"))
    assert record["held_out"] is True
    assert (tmp_path / OBSERVABLE_DIR / record["match_id"] / "events.jsonl").is_file()


def test_generate_reports_failures_with_a_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from matcheyes_synth import __main__ as cli
    from matcheyes_synth.generator import GenerationError

    def fail(*_args: object) -> None:
        raise GenerationError("boom")

    monkeypatch.setattr(cli, "generate_match", fail)
    assert main(["generate", "--scenario", "S01_control_balanced", "--out", str(tmp_path)]) == 2
    assert "boom" in capsys.readouterr().err


@pytest.mark.slow
def test_realism_command_prints_the_band_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["realism", "--count", "2"]) in (0, 1)
    assert "| events_per_match |" in capsys.readouterr().out
