"""Requirement 1: reproducibility. Same (scenario, seed, version) -> identical bytes."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from matcheyes.domain.match import EVENT_ADAPTER, ObservableMatch
from matcheyes_synth.generator import GENERATOR_VERSION, GenerationError, generate_match
from matcheyes_synth.identity import opaque_match_id
from matcheyes_synth.scenarios import scenario
from tests.synth.generated import SCENARIO_IDS, generated


def fingerprint(match: ObservableMatch) -> str:
    digest = hashlib.sha256(match.info.model_dump_json().encode())
    for event in match.events:
        digest.update(EVENT_ADAPTER.dump_json(event))
    return digest.hexdigest()


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_same_seed_gives_identical_match_and_truth(scenario_id: str) -> None:
    spec = scenario(scenario_id)
    first = generate_match(spec, spec.default_seed)
    second = generate_match(spec, spec.default_seed)
    assert fingerprint(first.observable) == fingerprint(second.observable)
    assert first.truth.model_dump_json() == second.truth.model_dump_json()


_PROBE = """
import hashlib, sys
from matcheyes.domain.match import EVENT_ADAPTER
from matcheyes_synth.generator import generate_match
from matcheyes_synth.scenarios import scenario
spec = scenario(sys.argv[1])
match = generate_match(spec, spec.default_seed).observable
digest = hashlib.sha256(match.info.model_dump_json().encode())
for event in match.events:
    digest.update(EVENT_ADAPTER.dump_json(event))
print(digest.hexdigest())
"""


def test_output_does_not_depend_on_hash_randomisation() -> None:
    """A fresh interpreter with a different PYTHONHASHSEED must produce the same bytes."""
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONHASHSEED": "12345", "PYTHONPATH": str(root / "src")}
    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", _PROBE, "S10_comeback_multi_cause"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    assert result.stdout.strip() == fingerprint(generated("S10_comeback_multi_cause").observable)


def test_different_seeds_give_different_matches() -> None:
    fingerprints = {fingerprint(generated("S01_control_balanced", s).observable) for s in range(5)}
    assert len(fingerprints) == 5


def test_seed_and_version_are_recorded_in_the_answer_key() -> None:
    match = generated("S02_press_surge", 17)
    assert match.truth.seed == 17
    assert match.truth.generator_version == GENERATOR_VERSION
    assert match.truth.match_id == opaque_match_id("S02_press_surge", 17, GENERATOR_VERSION)


def test_negative_seed_is_rejected() -> None:
    with pytest.raises(GenerationError):
        generate_match(scenario("S01_control_balanced"), -1)


def test_squads_are_persistent_across_matches_and_seeds() -> None:
    """Players keep their identity: the same club fields the same squad in every match."""
    a = generated("S01_control_balanced", 1).observable.info.home
    b = generated("S01_control_balanced", 2).observable.info.home
    c = generated("S02_press_surge", 3).observable.info.home
    assert a == b == c
