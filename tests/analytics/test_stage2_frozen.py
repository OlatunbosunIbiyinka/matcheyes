"""Stage 2 is the frozen baseline: later stages must not change its output.

Digests were recorded from analytics 0.1.0 before Stage 3 work began. If one changes, a Stage 2
module's behaviour changed; that needs an explicit decision, not a digest refresh.
"""

import hashlib

import pytest

from matcheyes.analytics.analysis import ANALYTICS_VERSION, analyse_match
from matcheyes.domain.match import ObservableMatch
from tests.support.builders import minimal_match
from tests.synth.generated import generated

FROZEN = {
    "minimal": "b5f6197b5731eefe0fa016b4ee8d02d603d09620ae39e14401e21e493ef15ce7",
    "S02_press_surge": "6d537be2dbaa850f36c3e63748ddabb8250c3e1e754557017e885ee917420dd2",
    "S08_coincidence_decoy": "d0340484efe00fa0f2fdfe146836ff0c887009629018637a193b821995852ee1",
}


def _digest(match: ObservableMatch) -> str:
    return hashlib.sha256(analyse_match(match).model_dump_json().encode()).hexdigest()


def test_stage2_version_is_unchanged() -> None:
    assert ANALYTICS_VERSION == "0.1.0"


def test_minimal_fixture_analysis_is_frozen() -> None:
    assert _digest(minimal_match()) == FROZEN["minimal"]


@pytest.mark.integration
@pytest.mark.parametrize("scenario_id", ["S02_press_surge", "S08_coincidence_decoy"])
def test_generated_analysis_is_frozen(scenario_id: str) -> None:
    assert _digest(generated(scenario_id, 10000).observable) == FROZEN[scenario_id]
