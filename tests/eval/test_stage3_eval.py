import pytest

from matcheyes.domain.time import MatchInstant
from matcheyes_eval.__main__ import main
from matcheyes_eval.scoring import Clock, detection_window, metric_mechanisms
from matcheyes_eval.stage2 import CONTROL_SCENARIO, build_cases
from matcheyes_eval.stage3 import MEASURES, evaluate_stage3, format_stage3
from matcheyes_synth.scenarios import scenario
from matcheyes_synth.seeds import development_seeds
from matcheyes_synth.truth import MechanismSignal
from tests.synth.generated import generated


def test_metric_mechanisms_respect_side_and_direction() -> None:
    insight = scenario("S02_press_surge").expected_insights[0]
    team, other = insight.team_id, "someone-else"
    assert metric_mechanisms(insight, "high_regains", team, "up") == (
        MechanismSignal.HIGH_RECOVERIES_UP,
    )
    assert metric_mechanisms(insight, "high_regains", team, "down") == ()
    assert metric_mechanisms(insight, "high_regains", other, "up") == ()
    assert metric_mechanisms(insight, "field_tilt", other, "up") == (
        MechanismSignal.TERRITORY_SHIFT,
    )  # territory counts for either side
    assert metric_mechanisms(insight, "pass_completion", other, "down") == ()  # supporting only


def test_detection_window_allows_two_minutes_early() -> None:
    spec = scenario("S02_press_surge")
    insight = spec.expected_insights[0]
    clock = Clock(generated(spec.scenario_id).observable)
    lo, hi = detection_window(insight, clock)
    start = clock.seconds(insight.window_start)
    assert (start - lo, hi - start) == (120, insight.max_detection_latency_s)
    assert clock.seconds(MatchInstant(period=1, clock_ms=0)) == 0


@pytest.mark.integration
def test_stage3_evaluation_compares_both_stages_on_the_same_matches() -> None:
    specs = [scenario(s) for s in (CONTROL_SCENARIO, "S02_press_surge", "S08_coincidence_decoy")]
    results = evaluate_stage3(build_cases(development_seeds(1), specs), "development", 1)
    row = results.insights["S02_press_surge/E1"]
    for measure in MEASURES:
        assert row.planted[measure].total == 1
        assert row.twin[measure].total == 1
    assert "S08_coincidence_decoy/E1" not in results.insights  # no metric mechanism to score
    assert set(results.decoys["S08_coincidence_decoy/D1"]) == {"s2", "s3"}
    assert results.mean_density(CONTROL_SCENARIO, "s3_weak") <= results.mean_density(
        CONTROL_SCENARIO, "s2_shifts"
    )
    assert results.ceiling_violations == 0
    assert results.untraceable == 0
    assert sum(results.decoy_ceiling_violations.values()) == 0
    text = format_stage3(results)
    assert "S02_press_surge/E1" in text
    assert "untraceable candidates or moments: 0" in text
    assert "candidate profile" in text


@pytest.mark.slow
def test_stage3_command_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stage3", "--seeds", "1"]) == 0
    out = capsys.readouterr().out
    assert "Stage 3 evaluation - development split, 1 seeds" in out
    assert "claims above associated: 0" in out
