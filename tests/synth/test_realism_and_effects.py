"""Requirements 10 and the 'detectable in principle' rule, measured over many seeds.

Realism is checked on development seeds (where the generator was calibrated) and on held-out
seeds (never used for calibration), so over-fitting to the calibration seeds would show.
"""

import pytest

from matcheyes_synth.effects import EFFECT_CHECKS, EffectCheck, measure_effect
from matcheyes_synth.realism import BANDS, format_table, match_metrics, summarise
from matcheyes_synth.scenarios import CATALOGUE, scenario
from matcheyes_synth.seeds import development_seeds, held_out_seeds
from tests.synth.generated import generated

pytestmark = pytest.mark.slow


def _matches(seeds: tuple[int, ...]) -> list:  # type: ignore[type-arg]
    return [generated(spec.scenario_id, seed).observable for spec in CATALOGUE for seed in seeds]


@pytest.mark.parametrize(
    "split", [development_seeds(8), held_out_seeds(8)], ids=["development", "held-out"]
)
def test_realism_bands_hold_across_the_catalogue(split: tuple[int, ...]) -> None:
    results = summarise(_matches(split))
    table = format_table(results)
    assert {r.band.metric for r in results} == {b.metric for b in BANDS}
    assert all(r.passed for r in results), "\n" + table


def test_match_metrics_are_per_team_where_banded_per_team() -> None:
    metrics = match_metrics(generated("S01_control_balanced").observable)
    for band in BANDS:
        assert len(metrics[band.metric]) == (2 if band.per == "team" else 1)
    assert sum(metrics["possession_share_per_team"]) == pytest.approx(1.0)


PRIMARY_CHECKS = [c for c in EFFECT_CHECKS if c.primary]


def test_every_scenario_with_a_planted_cause_has_one_primary_effect_check() -> None:
    planted = [s.scenario_id for s in CATALOGUE if s.interventions]
    assert sorted(c.scenario_id for c in PRIMARY_CHECKS) == sorted(planted)


@pytest.mark.parametrize("check", PRIMARY_CHECKS, ids=lambda c: c.scenario_id)
def test_planted_effect_is_detectable_against_its_counterfactual_twin(check: EffectCheck) -> None:
    """Paired against the counterfactual twin (same seed, interventions removed)."""
    result = measure_effect(scenario(check.scenario_id), check, list(development_seeds(20)))
    assert result.mean * check.expected_sign > 0, f"mean {result.mean:+.3f}"
    assert result.standardised * check.expected_sign > 1.0, f"t {result.standardised:+.2f}"
