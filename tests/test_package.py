import pytest

import matcheyes
from matcheyes.__main__ import main


def test_version_is_exposed() -> None:
    assert matcheyes.__version__


def test_cli_entrypoint_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 0
    assert "See beyond the score." in capsys.readouterr().out
