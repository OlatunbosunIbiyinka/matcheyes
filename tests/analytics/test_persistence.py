"""Temporal persistence on hand-built count series (before: 1 per minute over 30 minutes)."""

import pytest

from matcheyes.analytics.metrics import METRIC_BY_NAME
from matcheyes.analytics.persistence import (
    Persistence,
    PersistenceAssessment,
    SubWindow,
    assess_persistence,
    classify,
    sub_window_spec,
)
from tests.support.builders import AWAY, HOME
from tests.support.series import scripted_context, series_from

SPEC = METRIC_BY_NAME["pressures"]


def _assess(
    after_values: list[float],
    after: tuple[int, int] = (30, 45),
    goals: tuple[tuple[int, str], ...] = (),
) -> PersistenceAssessment:
    values = [1.0] * 30 + after_values
    values += [after_values[-1]] * (60 - len(values))
    series = series_from("pressures", HOME, values)
    context = scripted_context(goals=goals)
    return assess_persistence(series, SPEC, "up", (0, 30), after, context, 5, 1.0)


def test_uniform_change_is_sustained_and_continues() -> None:
    result = _assess([3.0] * 15)
    # each 5-minute third: (3 - 1) / sqrt(1/30 + 3/5) = 2.51
    assert [w.status for w in result.sub_windows] == ["hold"] * 3
    assert result.sub_windows[0].statistic == pytest.approx(2 / (1 / 30 + 3 / 5) ** 0.5)
    assert result.persistence is Persistence.SUSTAINED
    assert result.continues is True


def test_burst_in_the_first_third_is_transient() -> None:
    result = _assess([6.0] * 5 + [1.0] * 10)
    assert [w.status for w in result.sub_windows] == ["hold", "neutral", "neutral"]
    assert result.persistence is Persistence.TRANSIENT
    assert result.continues is False


def test_change_that_falls_below_baseline_is_reversed() -> None:
    result = _assess([4.0] * 10 + [0.0] * 5)
    assert result.sub_windows[-1].status == "against"
    assert result.persistence is Persistence.REVERSED


def test_short_window_is_indeterminate() -> None:
    result = _assess([3.0] * 15, after=(30, 37))
    assert len(result.sub_windows) == 1
    assert result.persistence is Persistence.INDETERMINATE


def test_no_follow_on_window_past_the_end_or_across_a_goal() -> None:
    assert _assess([3.0] * 30, after=(45, 60)).continues is None
    assert _assess([3.0] * 15, goals=((47, AWAY),)).continues is None


def test_classification_order() -> None:
    def w(*statuses: str) -> tuple[SubWindow, ...]:
        return tuple(
            SubWindow.model_validate({"span": (i, i + 5), "statistic": 0.0, "status": s})
            for i, s in enumerate(statuses)
        )

    assert classify(w("hold", "unavailable", "unavailable")) is Persistence.INDETERMINATE
    assert classify(w("hold", "hold", "against")) is Persistence.REVERSED
    assert classify(w("neutral", "hold", "hold")) is Persistence.SUSTAINED
    assert classify(w("hold", "hold", "neutral")) is Persistence.TRANSIENT  # decayed by the end
    assert classify(w("hold", "hold", "unavailable")) is Persistence.SUSTAINED


def test_sub_windows_need_a_third_of_the_samples() -> None:
    assert sub_window_spec(METRIC_BY_NAME["pass_completion"]).min_samples == 14
    assert sub_window_spec(METRIC_BY_NAME["wide_share"]).min_samples == 4
    assert sub_window_spec(SPEC).min_samples == 0
