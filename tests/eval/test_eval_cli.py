import pytest

from matcheyes_eval.__main__ import main


@pytest.mark.slow
def test_stage2_command_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stage2", "--seeds", "1"]) == 0
    out = capsys.readouterr().out
    assert "development split, 1 seeds" in out
    assert "claims above the decoy ceiling: 0" in out
