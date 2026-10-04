from typing import Any

import pytest
from pydantic import ValidationError

from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.time import MatchInstant
from matcheyes_synth.league import CLUBS, club_profile
from matcheyes_synth.scenarios import CATALOGUE, S02_PRESS_SURGE, S07_AGAINST_THE_RUN, scenario
from matcheyes_synth.truth import (
    Decoy,
    DecoyKind,
    InterventionKind,
    MechanismSignal,
    ScenarioSpec,
)

m = MatchInstant.at_minute


def test_league_identities_are_unique() -> None:
    assert len({c.club.club_id for c in CLUBS}) == len(CLUBS)
    assert len({c.club.short_name for c in CLUBS}) == len(CLUBS)
    assert len({c.ground for c in CLUBS}) == len(CLUBS)


def test_default_formations_have_ten_outfield_players() -> None:
    for profile in CLUBS:
        assert sum(int(n) for n in profile.default_formation.split("-")) == 10


def test_catalogue_ids_and_seeds_are_unique() -> None:
    assert len({s.scenario_id for s in CATALOGUE}) == len(CATALOGUE)
    assert len({s.default_seed for s in CATALOGUE}) == len(CATALOGUE)


@pytest.mark.parametrize("spec", CATALOGUE, ids=lambda s: s.scenario_id)
def test_scenario_clubs_exist_in_the_league(spec: ScenarioSpec) -> None:
    club_profile(spec.home_club_id)
    club_profile(spec.away_club_id)


def test_catalogue_contains_negative_controls() -> None:
    assert any(s.is_control for s in CATALOGUE)
    assert sum(1 for s in CATALOGUE if s.decoys) >= 2


def test_catalogue_exercises_every_cause_and_mechanism() -> None:
    kinds = {i.kind for s in CATALOGUE for i in s.interventions}
    insights = [x for s in CATALOGUE for x in s.expected_insights]
    scored = {m for x in insights for m in x.mechanisms}
    supporting = {m for x in insights for m in x.supporting_mechanisms}
    assert kinds == set(InterventionKind)
    assert scored | supporting == set(MechanismSignal)
    # Stage 1b approval: below match noise, so present but never scored (ADR-0007).
    assert set(MechanismSignal) - scored == {MechanismSignal.OPPONENT_PASS_COMPLETION_DOWN}


def test_lookup_by_id() -> None:
    assert scenario("S02_press_surge") is S02_PRESS_SURGE
    with pytest.raises(KeyError):
        scenario("S99_missing")


def _spec_with(base: ScenarioSpec, **changes: Any) -> dict[str, Any]:
    return base.model_dump() | changes


def test_effect_cannot_precede_its_cause() -> None:
    insight = S02_PRESS_SURGE.expected_insights[0].model_dump() | {"window_start": m(50)}
    with pytest.raises(ValidationError, match="precedes its cause"):
        ScenarioSpec.model_validate(_spec_with(S02_PRESS_SURGE, expected_insights=[insight]))


def test_insight_cannot_cite_an_unknown_cause() -> None:
    insight = S02_PRESS_SURGE.expected_insights[0].model_dump() | {"primary_cause": "I9"}
    with pytest.raises(ValidationError, match="unknown cause"):
        ScenarioSpec.model_validate(_spec_with(S02_PRESS_SURGE, expected_insights=[insight]))


def test_descriptive_insight_cannot_name_a_cause() -> None:
    insight = S07_AGAINST_THE_RUN.expected_insights[0].model_dump() | {"primary_cause": "I1"}
    with pytest.raises(ValidationError, match="cannot name causes"):
        ScenarioSpec.model_validate(_spec_with(S07_AGAINST_THE_RUN, expected_insights=[insight]))


def test_causal_insight_requires_mechanisms() -> None:
    insight = S02_PRESS_SURGE.expected_insights[0].model_dump() | {"mechanisms": []}
    with pytest.raises(ValidationError, match="need a cause and mechanisms"):
        ScenarioSpec.model_validate(_spec_with(S02_PRESS_SURGE, expected_insights=[insight]))


def test_decoy_cannot_overlap_a_planted_effect() -> None:
    decoy = Decoy(
        decoy_id="D1",
        kind=DecoyKind.NOISE_CLUSTER,
        team_id="kestrel-bay",
        window_start=m(62),
        window_end=m(66),
        max_claim_strength=ClaimStrength.ASSOCIATED,
        rationale="overlaps E1",
    )
    with pytest.raises(ValidationError, match="overlaps"):
        ScenarioSpec.model_validate(_spec_with(S02_PRESS_SURGE, decoys=[decoy.model_dump()]))


def test_decoys_never_permit_causal_claims() -> None:
    with pytest.raises(ValidationError, match="ASSOCIATED or below"):
        Decoy(
            decoy_id="D1",
            kind=DecoyKind.NOISE_CLUSTER,
            team_id="x",
            window_start=m(10),
            window_end=m(20),
            max_claim_strength=ClaimStrength.HYPOTHESISED,
            rationale="too strong",
        )


def test_event_triggered_intervention_cannot_start_before_trigger() -> None:
    spec = scenario("S05_red_card_reorganisation")
    early = spec.interventions[0].model_dump() | {"start": m(30).model_dump()}
    insight = spec.expected_insights[0].model_dump() | {"window_start": m(38).model_dump()}
    with pytest.raises(ValidationError, match="before its trigger"):
        ScenarioSpec.model_validate(
            _spec_with(spec, interventions=[early], expected_insights=[insight])
        )
