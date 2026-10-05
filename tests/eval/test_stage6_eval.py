"""Stage 6 evaluation on a small dataset: truth holds, faults are caught, results are reported."""

from functools import cache

import pytest

from matcheyes_eval.__main__ import main
from matcheyes_eval.stage2 import build_cases
from matcheyes_eval.stage6 import (
    PRESENTATION_FAULTS,
    SHAPES,
    Stage6Results,
    evaluate_stage6,
    format_stage6,
)
from matcheyes_synth.scenarios import scenario
from matcheyes_synth.seeds import development_seeds

pytestmark = pytest.mark.integration

MATCHES = ("S06_impact_substitution", "S03_game_state_deep_block")


@cache
def results() -> Stage6Results:
    cases = build_cases(development_seeds(1), [scenario(s) for s in MATCHES])
    return evaluate_stage6(cases, "development", 1, tamper_matches=2)


def test_no_view_alters_the_truth() -> None:
    r = results()
    assert r.insights > 0
    for row in (r.audiences[a] for a in ("fan", "broadcaster", "analyst")):
        assert row.views > 0
        assert (row.construction_failures, row.fingerprint_mismatches, row.audit_flagged) == (
            0,
            0,
            0,
        )
    assert r.audiences["all"].source_changed == 0
    assert r.inconsistent_cores == 0


def test_preferences_never_change_truth_or_placement() -> None:
    for shapes in results().shapes.values():
        assert set(shapes) <= set(SHAPES)
        for row in shapes.values():
            assert (row.truth_changed, row.placement_changed, row.audit_flagged) == (0, 0, 0)
            assert row.involvement_without_events == 0
        for shape in ("club_unrelated", "player_uncited", "player_absent"):
            assert shapes[shape].relevance_raised == 0
            assert shapes[shape].involvement_lines == 0


def test_compromised_insights_stay_primary_and_warned() -> None:
    feeds = results().feeds
    tampered = [f for key, f in feeds.items() if key.startswith("tampered-")]
    assert {k.split("/")[0] for k in feeds if k.startswith("tampered-")} == {
        "tampered-all",
        "tampered-one",
    }
    assert all(f.compromised > 0 for f in tampered)
    for f in tampered:
        assert f.compromised_primary == f.compromised_warned == f.compromised


def test_every_presentation_fault_is_injected_and_caught() -> None:
    faults = results().faults
    assert set(faults) == set(PRESENTATION_FAULTS)
    for row in faults.values():
        assert row.injected > 0
        assert row.caught == row.caught_by_expected == row.injected


def test_the_report_states_actual_results() -> None:
    text = format_stage6(results())
    assert "truth (expected 0)" in text
    assert "presentation red team:" in text


@pytest.mark.slow
def test_stage6_command_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stage6", "--seeds", "1", "--tamper-matches", "0"]) == 0
    assert "Stage 6 personalization - development split, 1 seeds" in capsys.readouterr().out
