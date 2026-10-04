"""Session-wide cache of generated matches, so multi-seed tests stay fast."""

from functools import cache

from matcheyes_synth.generator import GeneratedMatch, generate_match
from matcheyes_synth.scenarios import CATALOGUE, scenario
from matcheyes_synth.seeds import development_seeds

SCENARIO_IDS = [spec.scenario_id for spec in CATALOGUE]
SAMPLE_SEEDS = development_seeds(4)


@cache
def generated(scenario_id: str, seed: int | None = None) -> GeneratedMatch:
    spec = scenario(scenario_id)
    return generate_match(spec, spec.default_seed if seed is None else seed)


def sample(scenario_id: str) -> list[GeneratedMatch]:
    """The default seed plus a few development seeds."""
    return [generated(scenario_id), *(generated(scenario_id, s) for s in SAMPLE_SEEDS)]
