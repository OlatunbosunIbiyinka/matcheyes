"""Requirement 8: the answer key is complete, consistent and stored separately."""

import hashlib
import json
from itertools import pairwise
from pathlib import Path

import pytest

from matcheyes.ingestion.io import load_observable_match
from matcheyes_synth.io import (
    ANSWER_KEY_FILE,
    MANIFEST_FILE,
    OBSERVABLE_DIR,
    TRUTH_DIR,
    load_answer_key,
    write_generated_match,
    write_manifest,
)
from matcheyes_synth.scenarios import scenario
from matcheyes_synth.truth import GroundTruth, StateDriver
from tests.synth.generated import SCENARIO_IDS, generated, sample


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_answer_key_matches_the_scenario_and_the_observable_match(scenario_id: str) -> None:
    for match in sample(scenario_id):
        truth, observable = match.truth, match.observable
        assert truth.scenario == scenario(scenario_id)
        assert truth.match_id == observable.info.match_id
        squad = [
            p.player_id
            for sheet in (observable.info.home, observable.info.away)
            for p in sheet.squad
        ]
        assert [a.player_id for a in truth.player_attributes] == squad
        event_ids = {e.event_id for e in observable.events}
        for resolved in truth.resolved_events:
            assert set(resolved.event_ids) <= event_ids


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_state_timeline_covers_each_period_contiguously_in_one_minute_segments(
    scenario_id: str,
) -> None:
    match = generated(scenario_id)
    ends = {e.period: e.clock_ms for e in match.observable.events if e.type == "period_end"}
    for team_id in match.observable.info.team_ids:
        for period in (1, 2):
            timeline = [
                s
                for s in match.truth.state_timeline
                if s.team_id == team_id and s.start.period == period
            ]
            assert timeline[0].start.clock_ms == 0
            assert timeline[-1].end.clock_ms == ends[period]
            for a, b in pairwise(timeline):
                assert a.end == b.start
            for segment in timeline:
                assert 0 < segment.end.clock_ms - segment.start.clock_ms <= 60_000
                planted = StateDriver.INTERVENTION in segment.drivers
                assert planted == bool(segment.intervention_ids)


def test_answer_key_round_trips_through_json() -> None:
    truth = generated("S10_comeback_multi_cause").truth
    assert GroundTruth.model_validate_json(truth.model_dump_json()) == truth


def test_outputs_are_written_to_separate_roots_with_verifiable_hashes(tmp_path: Path) -> None:
    match = generated("S06_impact_substitution")
    match_id = match.observable.info.match_id
    entry = write_generated_match(match, tmp_path, held_out=False)
    manifest = write_manifest([entry], tmp_path)

    observable_dir = tmp_path / OBSERVABLE_DIR / match_id
    assert sorted(p.name for p in observable_dir.iterdir()) == ["events.jsonl", "match.json"]
    assert sorted(p.name for p in (tmp_path / TRUTH_DIR).iterdir()) == sorted(
        [MANIFEST_FILE, match_id]
    )
    assert (tmp_path / TRUTH_DIR / match_id / ANSWER_KEY_FILE).is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == [OBSERVABLE_DIR, TRUTH_DIR]

    assert load_observable_match(observable_dir) == match.observable
    assert load_answer_key(tmp_path, match_id) == match.truth

    [record] = json.loads(manifest.read_text("utf-8"))
    assert record["scenario_id"] == "S06_impact_substitution"
    assert record["held_out"] is False
    for relative, digest in record["sha256"].items():
        assert hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest() == digest


def test_written_files_are_byte_identical_across_runs(tmp_path: Path) -> None:
    match = generated("S03_game_state_deep_block")
    first = write_generated_match(match, tmp_path / "a", held_out=False)
    second = write_generated_match(match, tmp_path / "b", held_out=False)
    assert first.sha256 == second.sha256
