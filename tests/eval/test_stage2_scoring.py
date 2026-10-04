import pytest

from matcheyes.analytics.analysis import analyse_match
from matcheyes.analytics.metrics import METRIC_BY_NAME
from matcheyes.domain.events import PeriodEnd
from matcheyes.domain.time import MatchInstant
from matcheyes_eval.scoring import MECHANISM_METRICS, Clock, score_decoy, score_insight
from matcheyes_eval.stage2 import Case, build_cases, config_with, evaluate, format_results, tune
from matcheyes_synth.scenarios import scenario
from matcheyes_synth.seeds import development_seeds, held_out_seeds
from matcheyes_synth.truth import MechanismSignal
from tests.synth.generated import generated


def test_every_scored_mechanism_maps_to_an_engine_metric() -> None:
    for mech in MechanismSignal:
        if mech is MechanismSignal.PLAYER_INVOLVEMENT_UP:
            continue  # scored from player-involvement evidence
        assert MECHANISM_METRICS[mech].metric in METRIC_BY_NAME


def test_clock_places_the_second_half_after_first_half_stoppage() -> None:
    match = generated("S01_control_balanced").observable
    (first_end,) = [e for e in match.events if isinstance(e, PeriodEnd) and e.period == 1]
    clock = Clock(match)
    assert clock.seconds(MatchInstant(period=1, clock_ms=90_000)) == 90
    assert clock.seconds(MatchInstant(period=2, clock_ms=0)) == first_end.clock_ms / 1000


@pytest.mark.integration
def test_against_the_run_insight_and_decoy_are_scored() -> None:
    spec = scenario("S07_against_the_run_of_play")
    match = generated(spec.scenario_id).observable
    analysis = analyse_match(match)
    insight = score_insight(spec.expected_insights[0], analysis, match)
    assert insight.mechanisms_scored == 0
    assert insight.detected == ("against_run_of_play" in insight.mechanisms_found)
    assert not score_decoy(spec.decoys[0], analysis, match).over_ceiling


def test_tuning_refuses_held_out_seeds() -> None:
    spec = scenario("S01_control_balanced")
    case = Case(spec, held_out_seeds(1)[0], "planted", generated(spec.scenario_id).observable)
    with pytest.raises(ValueError, match="held-out"):
        tune([case], [(3.0, 10, 10)], 1)


@pytest.mark.integration
def test_evaluation_report_runs_end_to_end() -> None:
    specs = [scenario("S02_press_surge"), scenario("S08_coincidence_decoy")]
    cases = build_cases(development_seeds(1), specs)
    assert {c.variant for c in cases} == {"planted", "twin"}
    results = evaluate(cases, "development", 1, config_with(3.0, 10, 10))
    text = format_results(results)
    assert "S02_press_surge/E1" in text
    assert "S08_coincidence_decoy/D1" in text
    assert results.decoy_ceiling_violations == 0
