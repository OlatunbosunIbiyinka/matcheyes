"""Contextual baselines on hand-built series (statistics computed by hand in comments)."""

import math

import pytest

from matcheyes.analytics.baselines import (
    PUSH,
    RETREAT,
    BaselineAssessment,
    BaselineKind,
    ShiftSpan,
    assess_baseline,
    expected_response,
)
from matcheyes.analytics.context import MatchContext
from matcheyes.analytics.metrics import METRIC_BY_NAME, Series
from tests.support.builders import AWAY, HOME
from tests.support.series import scripted_context, series_from, step


def _shift(team: str, metric: str, direction: str, t: int = 30) -> ShiftSpan:
    return ShiftSpan.model_validate(
        {
            "team_id": team,
            "metric": metric,
            "direction": direction,
            "before": (0, t),
            "after": (t, t + 15),
            "statistic": 2.5 if direction == "up" else -2.5,
        }
    )


def _assess(shift: ShiftSpan, series: Series, context: MatchContext) -> BaselineAssessment:
    return assess_baseline(shift, series, METRIC_BY_NAME[shift.metric], context, 2.0, 5)


def test_no_transition_keeps_the_stage2_comparison() -> None:
    series = series_from("pressures", HOME, step(1, 3))
    result = _assess(_shift(HOME, "pressures", "up"), series, scripted_context())
    assert result.kind is BaselineKind.UNCHANGED
    assert result.statistic == 2.5
    assert (result.before, result.after) == ((0, 30), (30, 45))
    assert result.unusual


def test_same_regime_comparison_survives_a_real_change() -> None:
    context = scripted_context(goals=[(20, AWAY)])
    series = series_from("pressures", HOME, step(1, 3))
    result = _assess(_shift(HOME, "pressures", "up"), series, context)
    assert result.kind is BaselineKind.SAME_REGIME
    assert (result.before, result.after) == ((20, 30), (30, 45))
    # before: mean 1, var max(0, 1) / 10; after: mean 3, var 3 / 15.
    assert result.statistic == pytest.approx(2 / math.sqrt(0.1 + 0.2))
    assert result.transition_ids == (context.transitions[0].event_id,)


def test_shift_that_only_mixes_game_states_is_removed() -> None:
    context = scripted_context(goals=[(20, AWAY)])
    series = series_from("pressures", HOME, step(1, 3, at_bin=20))
    result = _assess(_shift(HOME, "pressures", "up"), series, context)
    assert result.kind is BaselineKind.REMOVED
    assert result.statistic == 0
    assert not result.unusual


def test_coincident_goal_marks_the_ordinary_response_as_aligned() -> None:
    context = scripted_context(goals=[(28, HOME)])
    goal_id = context.transitions[0].event_id
    flat = series_from("pressures", HOME, [2] * 60)
    scorer_retreats = _assess(_shift(HOME, "pressures", "down"), flat, context)
    assert scorer_retreats.kind is BaselineKind.COINCIDENT
    assert scorer_retreats.aligned_with == goal_id
    assert scorer_retreats.statistic == -2.5
    tilt = series_from("field_tilt", AWAY, [0.5] * 60)
    assert _assess(_shift(AWAY, "field_tilt", "up"), tilt, context).aligned_with == goal_id


def test_coincident_but_unexpected_direction_is_not_aligned() -> None:
    context = scripted_context(goals=[(28, HOME)])
    flat = series_from("pressures", HOME, [2] * 60)
    result = _assess(_shift(HOME, "pressures", "up"), flat, context)
    assert result.kind is BaselineKind.COINCIDENT
    assert result.aligned_with is None
    assert result.unusual


def test_coincidence_window_is_strictly_within_five_bins() -> None:
    series = series_from("pressures", HOME, step(1, 3))
    near = _assess(_shift(HOME, "pressures", "up"), series, scripted_context(goals=[(34, AWAY)]))
    far = _assess(_shift(HOME, "pressures", "up"), series, scripted_context(goals=[(35, AWAY)]))
    assert near.kind is BaselineKind.COINCIDENT
    assert far.kind is BaselineKind.SAME_REGIME
    assert far.after == (30, 35)


def test_dismissed_team_protects_and_the_opponent_pushes() -> None:
    context = scripted_context(reds=[(29, HOME)])
    red = context.transitions[0]
    assert expected_response(red, HOME) == RETREAT
    assert expected_response(red, AWAY) == PUSH
    flat = series_from("on_ball_share", AWAY, [0.5] * 60)
    assert _assess(_shift(AWAY, "on_ball_share", "up"), flat, context).aligned_with == red.event_id


def test_complementary_metrics_appear_only_as_gains() -> None:
    complementary = {m for m, spec in METRIC_BY_NAME.items() if spec.complementary}
    assert all(d == "up" for m, d in PUSH | RETREAT if m in complementary)
    assert not {m for m, _ in RETREAT} & complementary
