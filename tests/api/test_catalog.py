"""The match allow-list reads observable match directories only."""

from pathlib import Path

import pytest

from matcheyes.api.catalog import load_catalog
from matcheyes.ingestion.io import write_observable_match
from tests.lifecycle.support import in_progress


def test_a_single_match_directory_is_served(tmp_path: Path) -> None:
    match = in_progress()
    write_observable_match(match, tmp_path / match.info.match_id)
    assert list(load_catalog(tmp_path / match.info.match_id)) == [match.info.match_id]


def test_a_directory_of_matches_skips_everything_else(tmp_path: Path) -> None:
    match = in_progress()
    write_observable_match(match, tmp_path / match.info.match_id)
    (tmp_path / "notes.txt").write_text("ignored", "utf-8")
    (tmp_path / "bad name!").mkdir()
    (tmp_path / "empty").mkdir()
    (tmp_path / "truth" / match.info.match_id).mkdir(parents=True)
    (tmp_path / "truth" / match.info.match_id / "other.json").write_text("{}", "utf-8")
    catalog = load_catalog(tmp_path)
    assert list(catalog) == [match.info.match_id]
    assert catalog[match.info.match_id].events == match.events


def test_the_directory_name_must_be_the_match_id(tmp_path: Path) -> None:
    write_observable_match(in_progress(), tmp_path / "renamed")
    with pytest.raises(ValueError, match="differs"):
        load_catalog(tmp_path)


def test_an_empty_root_and_the_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no observable match"):
        load_catalog(tmp_path)
    match = in_progress()
    write_observable_match(match, tmp_path / match.info.match_id)
    with pytest.raises(ValueError, match="no observable match"):
        load_catalog(tmp_path, limit=0)
