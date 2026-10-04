import re

import pytest
from pydantic import ValidationError

from matcheyes.domain.time import MatchInstant
from matcheyes_synth.identity import opaque_match_id
from matcheyes_synth.league import club_profile
from matcheyes_synth.scenarios import CATALOGUE
from matcheyes_synth.truth import (
    ExpectedInsight,
    MechanismSignal,
    StateDelta,
    StateDriver,
    TeamStateSegment,
)

m = MatchInstant.at_minute


def test_match_ids_are_deterministic_and_reveal_nothing() -> None:
    ids = {opaque_match_id(s.scenario_id, s.default_seed, "0.1.0") for s in CATALOGUE}
    assert len(ids) == len(CATALOGUE)
    assert opaque_match_id("S02_press_surge", 7, "0.1.0") == opaque_match_id(
        "S02_press_surge", 7, "0.1.0"
    )
    for match_id in ids:
        assert re.fullmatch(r"m-[0-9a-f]{12}", match_id), match_id


def test_a_mechanism_is_either_scored_or_supporting() -> None:
    s02 = next(s for s in CATALOGUE if s.scenario_id == "S02_press_surge")
    (insight,) = s02.expected_insights
    assert MechanismSignal.OPPONENT_PASS_COMPLETION_DOWN in insight.supporting_mechanisms
    assert MechanismSignal.OPPONENT_PASS_COMPLETION_DOWN not in insight.mechanisms
    with pytest.raises(ValidationError, match="either scored or supporting"):
        ExpectedInsight.model_validate(
            insight.model_dump() | {"supporting_mechanisms": (insight.mechanisms[0],)}
        )


def test_empty_state_delta_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one dimension"):
        StateDelta()


def test_planted_segments_must_name_their_intervention() -> None:
    state = club_profile("kestrel-bay").style
    with pytest.raises(ValidationError, match="intervention_ids"):
        TeamStateSegment(
            team_id="kestrel-bay",
            start=m(60),
            end=m(61),
            state=state,
            drivers=(StateDriver.INTERVENTION,),
        )
    TeamStateSegment(
        team_id="kestrel-bay",
        start=m(60),
        end=m(61),
        state=state,
        drivers=(StateDriver.GAME_STATE, StateDriver.FATIGUE),
    )
