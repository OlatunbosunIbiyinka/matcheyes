"""The hidden-state formula: baseline + planted interventions x ramp + game state + fatigue."""

import pytest

from matcheyes.domain.time import MatchInstant
from matcheyes_synth.state import (
    StateContext,
    fatigue_adjustment,
    game_state_adjustment,
    ramp_fraction,
    resolve_state,
)
from matcheyes_synth.truth import (
    HiddenTeamState,
    Intervention,
    InterventionKind,
    InterventionTrigger,
    StateDelta,
    StateDriver,
)

m = MatchInstant.at_minute
BASE = HiddenTeamState(
    press_intensity=0.5,
    defensive_line=0.5,
    tempo=0.5,
    directness=0.5,
    width=0.5,
    risk_appetite=0.5,
    execution=0.5,
)
NEUTRAL = StateContext(goal_difference=0, match_minute=60, average_fatigue=0.0)


def surge(ramp_s: int = 120, end: MatchInstant | None = None) -> Intervention:
    return Intervention(
        intervention_id="I1",
        team_id="t",
        kind=InterventionKind.PRESS_SURGE,
        trigger=InterventionTrigger.MANAGER_INSTRUCTION,
        start=m(60),
        ramp_s=ramp_s,
        end=end,
        delta=StateDelta(press_intensity=0.2),
    )


@pytest.mark.parametrize(
    ("minute", "expected"), [(59.9, 0.0), (60, 0.0), (61, 0.5), (62, 1.0), (80, 1.0)]
)
def test_ramp_is_linear_from_start(minute: float, expected: float) -> None:
    assert ramp_fraction(surge(), m(minute)) == pytest.approx(expected)


def test_intervention_stops_at_its_end() -> None:
    assert ramp_fraction(surge(ramp_s=0, end=m(70)), m(70)) == 0.0
    assert ramp_fraction(surge(ramp_s=0, end=m(70)), m(69)) == 1.0


def test_baseline_only_reports_baseline_driver() -> None:
    resolved = resolve_state(BASE, [], m(10), NEUTRAL)
    assert resolved.state == BASE
    assert resolved.drivers == (StateDriver.BASELINE,)


def test_intervention_is_applied_and_attributed() -> None:
    resolved = resolve_state(BASE, [surge()], m(61), NEUTRAL)
    assert resolved.state.press_intensity == pytest.approx(0.6)
    assert resolved.drivers == (StateDriver.INTERVENTION,)
    assert resolved.intervention_ids == ("I1",)


def test_trailing_teams_take_more_risk_and_leading_teams_protect() -> None:
    trailing = game_state_adjustment(StateContext(-1, 80, 0.0))
    leading = game_state_adjustment(StateContext(2, 80, 0.0))
    assert trailing["risk_appetite"] > 0 > leading["risk_appetite"]
    assert game_state_adjustment(StateContext(-1, 20, 0.0)) == {}
    assert abs(leading["risk_appetite"]) > abs(
        game_state_adjustment(StateContext(1, 80, 0.0))["risk_appetite"]
    )


def test_fatigue_erodes_pressing_and_execution_beyond_a_threshold() -> None:
    assert fatigue_adjustment(StateContext(0, 80, 0.2)) == {}
    tired = fatigue_adjustment(StateContext(0, 80, 0.6))
    assert tired["press_intensity"] < 0 and tired["execution"] < 0


def test_background_drivers_are_reported_alongside_planted_ones() -> None:
    context = StateContext(goal_difference=-2, match_minute=85, average_fatigue=0.6)
    resolved = resolve_state(BASE, [surge()], m(85), context)
    assert resolved.drivers == (
        StateDriver.INTERVENTION,
        StateDriver.GAME_STATE,
        StateDriver.FATIGUE,
    )


def test_state_is_clamped_to_unit_interval() -> None:
    strong = surge().model_copy(update={"delta": StateDelta(press_intensity=1.0)})
    assert resolve_state(BASE, [strong], m(70), NEUTRAL).state.press_intensity == 1.0
