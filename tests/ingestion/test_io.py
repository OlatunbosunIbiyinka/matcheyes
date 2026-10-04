from pathlib import Path

from matcheyes.ingestion.invariants import validate_match
from matcheyes.ingestion.io import load_observable_match, write_observable_match
from tests.support.builders import FIXTURE_DIR, minimal_match


def test_round_trip_preserves_every_event(tmp_path: Path) -> None:
    original = minimal_match()
    write_observable_match(original, tmp_path)
    assert load_observable_match(tmp_path) == original


def test_committed_fixture_is_valid_and_current() -> None:
    loaded = load_observable_match(FIXTURE_DIR)
    assert validate_match(loaded).ok
    assert loaded == minimal_match(), "regenerate: uv run python -m tests.support.builders"
